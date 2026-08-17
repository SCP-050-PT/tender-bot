"""
Единый сервис для работы с LLM.
Заменяет: analyzer._parse_json(), analyzer._build_extraction_prompt(),
          llm_client._extract_json(), llm_wrapper._parse_llm_response().
"""

import json
import re
from typing import Optional, Dict, Any
from loguru import logger

from config.prompts import load_system_prompt


class LlmService:
    """Единый сервис для LLM-вызовов и парсинга ответов."""

    VERSION = "v7.0.0"

    def __init__(self, llm_client=None):
        self._llm = llm_client

    @property
    def llm(self):
        if self._llm is None:
            from utils.llm_client import YandexGPTClient

            self._llm = YandexGPTClient()
        return self._llm

    def extract_params(
        self, tender_type: str, documents_text: str, nmck: float = 0
    ) -> Optional[Dict[str, Any]]:
        """Извлекает параметры через LLM используя system_prompt.txt."""
        prompt = self.build_prompt(tender_type, documents_text)
        try:
            response = self.llm.send(
                system_prompt="Ты — аналитик тендеров. Извлеки параметры из текста и верни JSON.",
                user_message=prompt,
                temperature=0.1,
                max_tokens=2000,
            )
            if isinstance(response, dict) and "raw_text" in response:
                return self.parse_response(response["raw_text"])
            elif isinstance(response, dict):
                return response
            else:
                return self.parse_response(str(response))
        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка LLM-извлечения: {e}")
            return None

    def build_prompt(self, tender_type: str, documents_text: str) -> str:
        """Строит промпт из system_prompt.txt. Единый источник промптов."""
        section_map = {
            "sout": "EXTRACT_SOUT",
            "education": "EXTRACT_EDUCATION",
            "opr": "EXTRACT_OPR",
            "plk": "EXTRACT_PLK",
        }

        section_name = section_map.get(tender_type)
        if not section_name:
            logger.warning(f"[{self.VERSION}] Нет секции для типа '{tender_type}'")
            return (
                f"Извлеки параметры для типа {tender_type}:\n{documents_text[:15000]}"
            )

        try:
            full_prompt = load_system_prompt()
            section_text = self._get_section(full_prompt, section_name)
            if section_text:
                logger.info(
                    f"[{self.VERSION}] Используем секцию {section_name} "
                    f"из system_prompt.txt ({len(section_text)} симв.)"
                )
                # Для education нужно больше текста (таблицы цен в конце)
                text_limit = 30000 if tender_type == "education" else 15000
                parts = [
                    section_text,
                    "",
                    f"Текст тендера:\n{documents_text[:text_limit]}",
                    "Верни результат в формате JSON.",
                ]
                return "\n".join(parts)
        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка загрузки system_prompt.txt: {e}")

        return f"Извлеки параметры для типа {tender_type}:\n{documents_text[:15000]}"

    def parse_response(self, text: str) -> Optional[Dict[str, Any]]:
        """ЕДИНСТВЕННЫЙ парсер JSON из ответа LLM."""
        if not text or not isinstance(text, str):
            return None

        cleaned = text.strip()

        # Убираем markdown
        cleaned = re.sub(r"^```[a-z]*\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

        # Прямой JSON
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # JSON внутри текста
        json_match = re.search(r"\{[\s\S]*\}", cleaned)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        logger.warning(
            f"[{self.VERSION}] Не удалось распарсить JSON: {cleaned[:200]}..."
        )
        return None

    @staticmethod
    def _get_section(prompt_text: str, section_name: str) -> str:
        """Извлекает секцию из system_prompt.txt по имени."""
        pattern = re.compile(
            rf"===\s*{section_name}\s*===(.*?)(?====\s*\w+\s*===|\Z)",
            re.DOTALL | re.IGNORECASE,
        )
        match = pattern.search(prompt_text)
        return match.group(1).strip() if match else ""
