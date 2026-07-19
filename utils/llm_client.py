"""
utils/llm_client.py
Клиент для работы с YandexGPT API (Yandex Cloud).
Поддерживает синхронные запросы, обработку ответов, retry.
"""

import json
import time
from typing import Optional
import requests
from loguru import logger

from config.settings import settings


class YandexGPTClient:
    """
    Клиент для YandexGPT API.
    Документация: https://yandex.cloud/ru/docs/foundation-models/
    """

    BASE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(
        self,
        folder_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 60,
    ):
        self.folder_id = folder_id or settings.YANDEX_FOLDER_ID
        self.api_key = api_key or settings.YANDEX_API_KEY
        self.model = model or settings.YANDEX_GPT_MODEL
        self.max_retries = max_retries
        self.timeout = timeout

        # Валидация
        if not self.folder_id:
            raise ValueError("YANDEX_FOLDER_ID не задан")
        if not self.api_key:
            raise ValueError("YANDEX_API_KEY не задан")

        logger.info(f"YandexGPTClient инициализирован. Модель: {self.model}")

    def _build_headers(self) -> dict:
        """Формирует заголовки для запроса."""
        return {
            "Authorization": f"Api-Key {self.api_key}",
            "x-folder-id": self.folder_id,
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> dict:
        """Формирует тело запроса."""
        return {
            "modelUri": f"gpt://{self.folder_id}/{self.model}",
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens),
            },
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_message},
            ],
        }

    def _extract_json(self, text: str) -> Optional[dict]:
        """Извлекает JSON из ответа LLM (обрабатывает markdown-код)."""
        # Пробуем найти JSON в markdown-блоке
        import re

        # Ищем ```json ... ```
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Ищем ``` ... ```
            json_match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Пробуем найти JSON напрямую
                json_match = re.search(r"(\{.*\})", text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    return None

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
            logger.debug(f"Текст для парсинга: {json_str[:500]}")
            return None

    def send(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ) -> Optional[dict]:
        """
        Отправляет запрос к YandexGPT и возвращает распарсенный JSON.

        Args:
            system_prompt: Системный промпт
            user_message: Сообщение пользователя (текст тендера)
            temperature: Температура (0.0-1.0), ниже = точнее
            max_tokens: Максимум токенов в ответе

        Returns:
            dict: Распарсенный JSON или None при ошибке
        """
        payload = self._build_payload(
            system_prompt, user_message, temperature, max_tokens
        )
        headers = self._build_headers()

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    f"Запрос к YandexGPT (попытка {attempt}/{self.max_retries})"
                )

                response = requests.post(
                    self.BASE_URL, headers=headers, json=payload, timeout=self.timeout
                )
                response.raise_for_status()

                result = response.json()

                # Извлекаем текст ответа
                if "result" in result and "alternatives" in result["result"]:
                    text = result["result"]["alternatives"][0]["message"]["text"]
                    logger.info("Ответ получен, извлекаю JSON...")

                    parsed = self._extract_json(text)
                    if parsed:
                        logger.info("JSON успешно распарсен")
                        return parsed
                    else:
                        logger.warning(
                            "Не удалось распарсить JSON, возвращаю сырой текст"
                        )
                        return {"raw_text": text, "parse_error": True}
                else:
                    logger.error(f"Неожиданная структура ответа: {result}")
                    return None

            except requests.exceptions.Timeout:
                logger.warning(f"Таймаут (попытка {attempt})")
                if attempt < self.max_retries:
                    time.sleep(2**attempt)  # Экспоненциальная задержка
                continue

            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP ошибка: {e}")
                if response.status_code == 429:  # Rate limit
                    logger.warning("Rate limit, жду 10 сек...")
                    time.sleep(10)
                    continue
                return None

            except Exception as e:
                logger.error(f"Ошибка запроса: {e}")
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                continue

        logger.error("Все попытки исчерпаны")
        return None

    def analyze_tender(
        self, tender_text: str, system_prompt: Optional[str] = None
    ) -> Optional[dict]:
        """
        Упрощённый метод для анализа тендера.

        Args:
            tender_text: Полный текст тендера (ТЗ, извещение)
            system_prompt: Опционально — кастомный промпт

        Returns:
            dict: Результат анализа в формате JSON
        """
        from config.prompts import load_system_prompt

        prompt = system_prompt or load_system_prompt()

        return self.send(
            system_prompt=prompt,
            user_message=tender_text,
            temperature=0.2,  # Низкая температура для точности
            max_tokens=2500,
        )
