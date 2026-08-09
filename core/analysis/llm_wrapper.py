"""
core/analysis/llm_wrapper.py
Обёртка для LLM-вызовов.
v6.7.4: Исправлены баги с None, убрано сжатие.
"""

import json
import re
from typing import Optional, Tuple, List
from loguru import logger

from config.prompts import load_system_prompt

_system_prompt = None


def get_system_prompt() -> str:
    """Загружает системный промпт с кэшированием."""
    global _system_prompt
    if _system_prompt is None:
        try:
            _system_prompt = load_system_prompt()
        except Exception as e:
            logger.warning(f"Не удалось загрузить системный промпт: {e}")
            _system_prompt = _get_fallback_system_prompt()
    return _system_prompt


def _get_fallback_system_prompt() -> str:
    """Fallback промпт."""
    return """Ты — старший аналитик тендерного отдела компании «АС Безопасности».
Проанализируй текст закупки и верни СТРОГИЙ JSON с параметрами.
ТИПЫ: sout (СОУТ), education (обучение), plk (ПЛК), opr (ОПР), combined (СОУТ+ОПР).
ВАРИАНТЫ СОУТ: variant=1 (по умолчанию), variant=2 (карты), variant=3 (протоколы).
ВАЖНО: "протокол проверки знаний" = обучение, НЕ variant=3.
ДОКУМЕНТЫ ОБУЧЕНИЯ:
- protocols_count: "обучение охране труда", "ОТ" (ВСЕГДА протоколы для ОТ!)
- certificates: "работа на высоте"
- diplomas: "переподготовка" (только если НЕ ОТ)
- worker_certs: "рабочая профессия"
- qual_certs: "повышение квалификации"
Верни СТРОГИЙ JSON без markdown:
{
  "tender_type": "sout|education|plk|opr|combined",
  "variant": 1,
  "confidence": 0.0,
  "students_count": 0,
  ...
}"""


class LlmWrapper:
    """Обёртка для взаимодействия с LLM."""

    def __init__(self, llm_client=None):
        self._llm = llm_client

    @property
    def llm(self):
        if self._llm is None:
            try:
                from utils.llm_client import YandexGPTClient

                self._llm = YandexGPTClient()
            except Exception as e:
                logger.warning(f"LLM недоступен: {e}")
                self._llm = False
        return self._llm

    def analyze_tender(
        self,
        tender_text: str,
        tender_info: dict,
        extracted_params=None,
        classification: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Двухуровневый анализ: классификация → извлечение параметров.
        Возвращает dict или None.
        """
        if not self.llm or self.llm is False:
            return None

        system_prompt = get_system_prompt()
        context = self._build_context(tender_info, classification)

        # Строим user_message
        if extracted_params:
            user_message = self._build_enriched_user_message(
                extracted_params, tender_text, tender_info, classification
            )
        else:
            user_message = self._build_basic_user_message(tender_text, classification)

        system_prompt_enriched = system_prompt + context

        try:
            result = self.llm.send(
                system_prompt=system_prompt_enriched,
                user_message=user_message,
                temperature=0.05,
                max_tokens=3000,
            )
            parsed = self._parse_llm_response(result)
            if parsed is None:
                logger.warning("[LLM] Пустой/невалидный ответ")
                return None

            # Принудительно применяем classification если извлечение другое
            if classification:
                classified_type = (
                    (classification.get("tender_type") or "").lower().strip()
                )
                extracted_type = (parsed.get("tender_type") or "").lower().strip()

                if classified_type == "opr" and extracted_type in ("sout", "соут"):
                    logger.warning(f"[v6.7.4] Исправление типа: {extracted_type} → opr")
                    parsed["tender_type"] = "opr"
                    parsed["notes"] = (
                        parsed.get("notes", "") + " [Тип скорректирован: ОПР]"
                    )
                elif classified_type == "education" and extracted_type == "sout":
                    logger.warning(
                        f"[v6.7.4] Исправление типа: {extracted_type} → education"
                    )
                    parsed["tender_type"] = "education"
                    parsed["notes"] = (
                        parsed.get("notes", "") + " [Тип скорректирован: обучение]"
                    )

            return parsed

        except Exception as e:
            # v6.7.4: Исправлен баг — parsed может быть не определена
            logger.error(f"Ошибка LLM-анализа: {e}")
            if classification:
                classified_type = classification.get("tender_type", "")
                if classified_type:
                    logger.warning(f"[v6.7.4] Fallback с типом {classified_type}")
                    return {
                        "tender_type": classified_type,
                        "confidence": 0.0,
                        "parse_error": True,
                        "notes": "Ошибка анализа, тип из классификации",
                    }
            return None

    def _build_context(self, tender_info: dict, classification: Optional[dict]) -> str:
        """Строит контекст для промпта."""
        lines = ["\n=== КОНТЕКСТ ЗАКУПКИ ==="]
        lines.append(f"- Название: {tender_info.get('purchase_name', '')}")
        lines.append(f"- Заказчик: {tender_info.get('customer_name', '')}")
        lines.append(f"- Регион: {tender_info.get('customer_region', '')}")
        lines.append(f"- НМЦК: {tender_info.get('nmck', '')}")
        lines.append(f"- Срок подачи: {tender_info.get('deadline_date', '')}")

        if classification:
            lines.append(
                f"- Предварительный тип: {classification.get('tender_type', 'не определён')}"
            )
            lines.append(
                f"- Обоснование: {str(classification.get('reasoning', ''))[:100]}"
            )

        lines.append(
            "\nПроанализируй текст и верни СТРОГИЙ JSON согласно правилам выше."
        )
        return "\n".join(lines)

    def _build_enriched_user_message(
        self,
        extracted_params,
        tender_text: str,
        tender_info: dict,
        classification: Optional[dict] = None,
    ) -> str:
        """Строит обогащённый промпт с найденными параметрами."""
        lines = ["=== НАЙДЕНО В ТЕКСТЕ (проверь и подтверди) ==="]

        # v6.7.4: Безопасная проверка classification
        classified_type = ""
        if classification:
            classified_type = (classification.get("tender_type") or "").lower()
            lines.append(f"=== КЛАССИФИКАЦИЯ: {classified_type} ===")
            if classified_type == "education":
                lines.append(
                    "⚠️ КРИТИЧЕСКО: 'охрана труда' → protocols_count = students_count"
                )
            elif classified_type == "opr":
                lines.append("⚠️ КРИТИЧЕСКО: Это ОПР, НЕ СОУТ. rm_total = 0.")
            lines.append("")

        # Поля из extracted_params
        fields = [
            ("Рабочих мест (РМ)", getattr(extracted_params, "rm_total", None)),
            ("Слушателей", getattr(extracted_params, "students_count", None)),
            ("Точек замеров (ПЛК)", getattr(extracted_params, "points_count", None)),
            ("Адресов", getattr(extracted_params, "addresses_count", None)),
            ("Городов", getattr(extracted_params, "cities_count", None)),
            ("Регионов", getattr(extracted_params, "regions_count", None)),
            (
                "Срок исполнения",
                getattr(extracted_params, "deadline_days", None),
                "дней",
            ),
        ]

        for field_info in fields:
            label = field_info[0]
            value = field_info[1]
            suffix = field_info[2] if len(field_info) > 2 else ""
            if value is not None and value > 0:
                lines.append(f"- {label}: {value}{suffix}")

        # Повторяем критические правила
        if classified_type == "education":
            lines.append(
                "⚠️ КРИТИЧЕСКО: 'охрана труда' → protocols_count = students_count"
            )
        elif classified_type == "opr":
            lines.append("⚠️ КРИТИЧЕСКО: Это ОПР, НЕ СОУТ. rm_total = 0.")

        lines.extend(
            [
                "",
                "=== ЗАДАЧА ===",
                "1. Подтверди найденные значения или укажи правильные",
                "2. Если не найдено — верни 0 (не придумывай)",
                "3. Заполни reasoning — объясни свои выводы",
                "4. Оцени confidence реально",
                "",
                "=== ТЕКСТ ДОКУМЕНТОВ ===",
                tender_text[:10000],
            ]
        )

        return "\n".join(lines)

    def _build_basic_user_message(
        self, tender_text: str, classification: Optional[dict] = None
    ) -> str:
        """Строит базовый user_message без extracted_params."""
        lines = []
        if classification:
            lines.extend(
                [
                    "=== ПРЕДВАРИТЕЛЬНАЯ КЛАССИФИКАЦИЯ ===",
                    f"Тип: {classification.get('tender_type', 'не определён')}",
                    f"Уверенность: {classification.get('confidence', 0)}",
                    "",
                    "ВАЖНО: Используй предварительную классификацию как основу.",
                    "",
                ]
            )
        lines.append("=== ТЕКСТ ДОКУМЕНТОВ ===")
        lines.append(tender_text[:12000])
        return "\n".join(lines)

    def _parse_llm_response(self, result) -> Optional[dict]:
        """Парсит ответ LLM. Возвращает dict или None."""
        if isinstance(result, dict):
            return self._normalize_parsed(result)
        if not isinstance(result, str):
            return None

        text = result.strip()
        if not text:
            return None

        # Проверка на отказ
        refusal_patterns = [
            r"я\s+не\s+могу",
            r"не\s+могу\s+помочь",
            r"извините",
            r"не\s+удалось",
            r"ошибка",
        ]
        for pattern in refusal_patterns:
            if re.search(pattern, text.lower()):
                logger.warning(f"[LLM] Модель отказалась: {text[:100]}")
                return None

        # Убираем markdown
        text = re.sub(r"```[a-z]*\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text)
        text = re.sub(r"^json\s*", "", text, flags=re.IGNORECASE)
        text = text.strip()

        # JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return self._normalize_parsed(parsed)
        except json.JSONDecodeError:
            pass

        # JSON внутри текста
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, dict):
                    return self._normalize_parsed(parsed)
            except json.JSONDecodeError:
                pass

        # Fallback key-value
        parsed = self._parse_key_value_fallback(text)
        if parsed:
            return self._normalize_parsed(parsed)

        logger.warning(f"[LLM] Не удалось распарсить: {text[:200]}...")
        return None

    def _parse_key_value_fallback(self, text: str) -> Optional[dict]:
        """Fallback: парсит ключ-значение из текста."""
        result = {
            "tender_type": "",
            "variant": 1,
            "confidence": 0.0,
            "students_count": 0,
            "certificates": 0,
            "diplomas": 0,
            "worker_certs": 0,
            "qual_certs": 0,
            "protocols_count": 0,
            "is_distance": True,
            "rm_total": 0,
            "rm_category_1": 0,
            "rm_category_2": 0,
            "has_iii": False,
            "iii_count": 0,
            "points_count": 0,
            "factors_count": 0,
            "delivery_count": 1,
            "is_annual": False,
            "needs_siz_norms": False,
            "needs_dsiz_norms": False,
            "needs_iot_norms": False,
            "needs_subcontractor": False,
            "deadline_days": 0,
            "addresses_count": 1,
            "cities_count": 1,
            "regions_count": 1,
            "trip_days": 3,
            "has_venue": False,
            "urgency": "normal",
            "application_guarantee": "",
            "contract_guarantee": "",
            "guarantee_method": "",
            "special_requirements": [],
            "red_flags": [],
            "notes": "",
            "teacher_days": 0,
            "accommodation_nights": 0,
            "transport_km": 0,
            "venue_rent_days": 0,
            "manikin_days": 0,
            "opr_positions": 0,
            "opr_persons": 0,
            "is_seasonal": False,
        }

        num_patterns = [
            ("tender_type", r"tender_type\s*[:=]\s*([^,}\n]+)"),
            ("students_count", r"students_count\s*[:=]\s*(\d+)"),
            ("rm_total", r"rm_total\s*[:=]\s*(\d+)"),
            ("points_count", r"points_count\s*[:=]\s*(\d+)"),
            ("variant", r"variant\s*[:=]\s*(\d+)"),
            ("confidence", r"confidence\s*[:=]\s*(\d+\.?\d*)"),
            ("teacher_days", r"teacher_days\s*[:=]\s*(\d+)"),
            ("accommodation_nights", r"accommodation_nights\s*[:=]\s*(\d+)"),
            ("transport_km", r"transport_km\s*[:=]\s*(\d+)"),
            ("venue_rent_days", r"venue_rent_days\s*[:=]\s*(\d+)"),
            ("manikin_days", r"manikin_days\s*[:=]\s*(\d+)"),
            ("opr_positions", r"opr_positions\s*[:=]\s*(\d+)"),
            ("opr_persons", r"opr_persons\s*[:=]\s*(\d+)"),
            ("deadline_days", r"deadline_days\s*[:=]\s*(\d+)"),
            ("addresses_count", r"addresses_count\s*[:=]\s*(\d+)"),
            ("cities_count", r"cities_count\s*[:=]\s*(\d+)"),
            ("regions_count", r"regions_count\s*[:=]\s*(\d+)"),
            ("trip_days", r"trip_days\s*[:=]\s*(\d+)"),
        ]

        bool_patterns_list = [
            ("is_distance", r"is_distance\s*[:=]\s*(true|false)"),
            ("has_iii", r"has_iii\s*[:=]\s*(true|false)"),
            ("is_annual", r"is_annual\s*[:=]\s*(true|false)"),
            ("needs_subcontractor", r"needs_subcontractor\s*[:=]\s*(true|false)"),
            ("needs_siz_norms", r"needs_siz_norms\s*[:=]\s*(true|false)"),
            ("needs_dsiz_norms", r"needs_dsiz_norms\s*[:=]\s*(true|false)"),
            ("needs_iot_norms", r"needs_iot_norms\s*[:=]\s*(true|false)"),
            ("has_venue", r"has_venue\s*[:=]\s*(true|false)"),
            ("is_seasonal", r"is_seasonal\s*[:=]\s*(true|false)"),
        ]

        found_any = False

        for key, pattern in num_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = match.group(1).strip().lower()
                if key == "tender_type":
                    result[key] = val.strip("\"'")
                else:
                    try:
                        result[key] = int(float(val))
                    except ValueError:
                        pass
                found_any = True

        for key, pattern in bool_patterns_list:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result[key] = match.group(1).lower() == "true"
                found_any = True

        if not found_any:
            return None

        key_fields = ["rm_total", "students_count", "points_count", "variant"]
        filled = sum(1 for f in key_fields if result.get(f, 0) > 0)
        if filled >= 2:
            result["confidence"] = 0.4
        elif filled == 1:
            result["confidence"] = 0.2
        else:
            result["confidence"] = 0.1

        return result

    def _normalize_parsed(self, parsed: dict) -> dict:
        """Нормализует распарсенный результат."""
        if not isinstance(parsed, dict):
            return {}

        # Нормализация типа
        from core.tender_type import get_type_detector

        type_detector = get_type_detector()
        parsed["tender_type"] = type_detector._normalize_alias(
            parsed.get("tender_type", "unknown")
        )

        # Guard: students_count > 0 → education
        students = parsed.get("students_count", 0)
        teacher_days = parsed.get("teacher_days", 0)
        opr_positions = parsed.get("opr_positions", 0)

        if parsed.get("tender_type") == "соут" and (students > 0 or teacher_days > 0):
            if opr_positions > 0:
                parsed["tender_type"] = "комбинированный"
            else:
                parsed["tender_type"] = "обучение"
                parsed["rm_total"] = 0
                parsed["rm_category_1"] = 0
                parsed["rm_category_2"] = 0
                parsed["opr_positions"] = 0
                parsed["opr_persons"] = 0

        # Нормализация confidence
        confidence = parsed.get("confidence", 0)
        if confidence == 0 or confidence is None:
            key_fields = ["rm_total", "students_count", "points_count", "variant"]
            filled = sum(1 for f in key_fields if parsed.get(f, 0) > 0)
            if filled >= 2:
                confidence = 0.5
            elif filled == 1:
                confidence = 0.3
            else:
                confidence = 0.1
            parsed["confidence"] = confidence

        return parsed

    def validate_rm(self, llm_result: dict, extracted_rm: int) -> Tuple[bool, int]:
        """Валидация РМ от LLM."""
        if not llm_result or not isinstance(llm_result, dict):
            return False, extracted_rm or 0

        llm_rm = llm_result.get("rm_total", 0)
        llm_confidence = llm_result.get("confidence", 0.0)

        if llm_rm == 0:
            return False, extracted_rm or 0

        if extracted_rm is None:
            extracted_rm = 0

        if extracted_rm > 0 and llm_rm > 0:
            ratio = max(llm_rm, extracted_rm) / min(llm_rm, extracted_rm)
            if ratio > 5:
                logger.warning(
                    f"⚠️ КРИТИЧЕСКОЕ РАСХОЖДЕНИЕ: LLM={llm_rm} vs extracted={extracted_rm} "
                    f"(ratio={ratio:.1f}). Приоритет у extracted."
                )
                return True, extracted_rm

        if extracted_rm > 0 and llm_confidence < 0.3:
            ratio = max(llm_rm, extracted_rm) / min(llm_rm, extracted_rm)
            if ratio > 3:
                return True, extracted_rm

        if llm_rm > 200 and llm_confidence < 0.3:
            return True, extracted_rm or 0

        return False, llm_rm
