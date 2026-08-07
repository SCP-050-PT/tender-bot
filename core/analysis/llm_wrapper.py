"""
core/analysis/llm_wrapper.py
Обёртка для LLM-вызовов: JSON-промпт, отправка, парсинг, валидация.
v6.7.1: JSON-only, classification передаётся в user_message, улучшенное сжатие.
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
    """Fallback промпт если system_prompt.txt недоступен."""
    return """Ты — старший аналитик тендерного отдела компании «АС Безопасности».
Проанализируй текст закупки и верни СТРОГИЙ JSON с параметрами.

ТИПЫ: sout (СОУТ), education (обучение), plk (ПЛК), opr (ОПР), combined (СОУТ+ОПР).

ВАРИАНТЫ СОУТ: 
- variant=1 (по умолчанию): 20% + аналогия 100₽
- variant=2: "карты условий труда" в тексте
- variant=3: "протоколы СОУТ" или "комплекты протоколов СОУТ" (НЕ "протокол проверки знаний"!)

ВАЖНО: "протокол проверки знаний" = обучение, НЕ variant=3.

ДОКУМЕНТЫ ОБУЧЕНИЯ:
- protocols_count: "обучение охране труда", "ОТ", "охрана труда" (ВСЕГДА протоколы для ОТ!)
- certificates: "работа на высоте", "ограниченные пространства"
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
        v6.7.1: classification передаётся в user_message этапа 2.
        """
        if not self.llm or self.llm is False:
            return None

        system_prompt = get_system_prompt()
        compressed_text = self._compress_text(tender_text, classification)
        context = self._build_context(tender_info, classification)

        # v6.7.1: Передаём classification в user_message
        if extracted_params:
            user_message = self._build_enriched_user_message(
                extracted_params, compressed_text, tender_info, classification
            )
        else:
            user_message = self._build_basic_user_message(
                compressed_text, classification
            )

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
                logger.warning("[LLM] Пустой/невалидный ответ, используем fallback")
                return None

            # v6.7.1: Принудительно применяем classification если извлечение другое
            if classification:
                classified_type = classification.get("tender_type", "").lower().strip()
                extracted_type = parsed.get("tender_type", "").lower().strip()

                # Если классификация = opr, а извлечение = sout — принудительно opr
                if classified_type == "opr" and extracted_type == "sout":
                    logger.warning(
                        f"[v6.7.1] Исправление типа: извлечение={extracted_type} → классификация={classified_type}"
                    )
                    parsed["tender_type"] = "opr"
                    parsed["notes"] = (
                        parsed.get("notes", "")
                        + " [Тип скорректирован по классификации: ОПР]"
                    )

                # Если классификация = education, а извлечение = sout — принудительно education
                elif classified_type == "education" and extracted_type == "sout":
                    logger.warning(
                        f"[v6.7.1] Исправление типа: извлечение={extracted_type} → классификация={classified_type}"
                    )
                    parsed["tender_type"] = "education"
                    parsed["notes"] = (
                        parsed.get("notes", "")
                        + " [Тип скорректирован по классификации: обучение]"
                    )

            return parsed
        except Exception as e:
            logger.error(f"Ошибка LLM-анализа: {e}")
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

    def _compress_text(self, text: str, classification: Optional[dict] = None) -> str:
        """Умное сжатие текста: зоны А/Б/В. v6.7.1: улучшенные паттерны."""
        lines = text.split("\n")
        zone_a = []
        zone_b = []
        zone_c = []

        # v6.7.1: Расширенные паттерны зоны А
        zone_a_patterns = [
            r"количество\s*[:=]",
            r"кол-во\s*[:=]",
            r"обучаемых",
            r"рабочих\s*мест",
            r"слушател",
            r"стоимость\s*[:=]",
            r"цена\s*[:=]",
            r"срок\s*исполнен",
            r"дата\s*окончан",
            r"адрес",
            r"город",
            r"регион",
            r"протокол\s*специальной\s*оценки",
            r"комплект\s*протоколов",
            r"карта\s*условий\s*труда",
            r"оценка\s*проф\s*рисков",
            r"специальная\s*оценка",
            r"переподготовка",
            r"повышение\s*квалификации",
            r"дополнительная\s*проф\s*программа",
        ]

        zone_c_patterns = [
            r"^[\s\d\.]*$",
            r"статья\s*\d+",
            r"федеральн\w+\s*закон",
            r"постановлени\w+\s*правительства",
            r"приложение\s*\d+",
            r"лист\s*\d+\s*из\s*\d+",
            r"^\d{2}\.\d{2}\.\d{4}$",  # даты
            r"^\d{4,}$",  # длинные числа (ID, телефоны)
            r"ИНН|ОГРН|КПП|БИК",
        ]

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped or len(line_stripped) < 3:
                continue

            is_zone_a = any(
                re.search(p, line_stripped, re.IGNORECASE) for p in zone_a_patterns
            )
            is_zone_c = any(
                re.search(p, line_stripped, re.IGNORECASE) for p in zone_c_patterns
            )

            if is_zone_a:
                zone_a.append(line_stripped)
            elif is_zone_c:
                zone_c.append(line_stripped)
            else:
                zone_b.append(line_stripped)

        result = []
        if zone_a:
            result.append("=== КЛЮЧЕВЫЕ ДАННЫЕ ===")
            result.extend(zone_a[:150])  # v6.7.1: больше ключевых данных

        if zone_b:
            result.append("\n=== КОНТЕКСТ ===")
            result.extend(zone_b[:300])  # v6.7.1: больше контекста

        if zone_c:
            result.append("\n=== ДЕТАЛИ ===")
            zone_c_compressed = self._compress_repeats(zone_c)
            result.extend(zone_c_compressed[:30])

        compressed = "\n".join(result)
        logger.info(
            f"[v6.7.1] Текст сжат: {len(text)} → {len(compressed)} симв. "
            f"(A={len(zone_a)}, B={len(zone_b)}, C={len(zone_c)})"
        )
        return compressed

    def _compress_repeats(self, lines: List[str]) -> List[str]:
        """Сжимает повторяющиеся строки."""
        if not lines:
            return []

        result = []
        prev = None
        repeat_count = 0

        for line in lines:
            if line == prev:
                repeat_count += 1
                continue
            if repeat_count > 2:
                result.append(f"[... повторяется {repeat_count} раз ...]")
            if prev is not None:
                result.append(prev)
            prev = line
            repeat_count = 0

        if prev:
            if repeat_count > 2:
                result.append(f"[... повторяется {repeat_count} раз ...]")
            result.append(prev)

        return result

    def _build_enriched_user_message(
        self,
        extracted_params,
        compressed_text: str,
        tender_info: dict,
        classification: Optional[dict] = None,
    ) -> str:
        """Строит обогащённый промпт с найденными параметрами."""
        lines = [
            "=== НАЙДЕНО В ТЕКСТЕ (проверь и подтверди) ===",
        ]

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

        # v6.7.1: Добавляем classification в user_message
        if classification:
            lines.extend(
                [
                    "",
                    f"=== ПРЕДВАРИТЕЛЬНАЯ КЛАССИФИКАЦИЯ ===",
                    f"Тип: {classification.get('tender_type', 'не определён')}",
                    f"Уверенность: {classification.get('confidence', 0)}",
                    f"Обоснование: {classification.get('reasoning', '')}",
                    "",
                    "ВАЖНО: Используй предварительную классификацию как основу. "
                    "Если извлечение параметров даёт другой тип — проверь внимательно.",
                ]
            )

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
                compressed_text[:10000],
            ]
        )

        return "\n".join(lines)

    def _build_basic_user_message(
        self, compressed_text: str, classification: Optional[dict] = None
    ) -> str:
        """Строит базовый user_message без extracted_params."""
        lines = []
        if classification:
            lines.extend(
                [
                    f"=== ПРЕДВАРИТЕЛЬНАЯ КЛАССИФИКАЦИЯ ===",
                    f"Тип: {classification.get('tender_type', 'не определён')}",
                    f"Уверенность: {classification.get('confidence', 0)}",
                    "",
                    "ВАЖНО: Используй предварительную классификацию как основу.",
                    "",
                ]
            )
        lines.append("=== ТЕКСТ ДОКУМЕНТОВ ===")
        lines.append(compressed_text[:12000])
        return "\n".join(lines)

    def _parse_llm_response(self, result) -> Optional[dict]:
        """Парсит ответ LLM: JSON → key-value fallback. v6.7.1: YAML удалён."""
        if isinstance(result, dict):
            return result
        if not isinstance(result, str):
            return None

        text = result.strip()
        if not text:
            logger.warning("[LLM] Пустой ответ от модели")
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

        # === Шаг 1: Убираем markdown ===
        text = re.sub(r"```[a-z]*\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text)
        text = re.sub(r"^json\s*", "", text, flags=re.IGNORECASE)
        text = text.strip()

        # === Шаг 2: JSON (приоритет) ===
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                logger.info(
                    f"[LLM] JSON распарсен: confidence={parsed.get('confidence', 0)}"
                )
                return self._normalize_parsed(parsed)
        except json.JSONDecodeError:
            pass

        # === Шаг 3: JSON внутри текста ===
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, dict):
                    logger.info("[LLM] JSON извлечён из текста")
                    return self._normalize_parsed(parsed)
            except json.JSONDecodeError:
                pass

        # v6.7.1: YAML fallback УДАЛЁН — только key-value
        parsed = self._parse_key_value_fallback(text)
        if parsed:
            logger.info("[LLM] Fallback key-value")
            return self._normalize_parsed(parsed)

        logger.warning(f"[LLM] Не удалось распарсить: {text[:200]}...")
        return None

    def _parse_key_value_fallback(self, text: str) -> Optional[dict]:
        """Fallback: парсит ключ-значение из текста LLM-ответа."""
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

        # Паттерны для числовых полей
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

        # Guard: students_count > 0 → всегда education (если не combined)
        students = parsed.get("students_count", 0)
        teacher_days = parsed.get("teacher_days", 0)
        opr_positions = parsed.get("opr_positions", 0)

        if parsed.get("tender_type") == "соут" and (students > 0 or teacher_days > 0):
            if opr_positions > 0:
                logger.warning(
                    f"[LLM Guard] 'соут' при students={students}, opr_positions={opr_positions} → 'combined'"
                )
                parsed["tender_type"] = "комбинированный"
            else:
                logger.warning(
                    f"[LLM Guard] 'соут' при students={students}, teacher_days={teacher_days} → 'education'"
                )
                parsed["tender_type"] = "обучение"
                parsed["rm_total"] = 0
                parsed["rm_category_1"] = 0
                parsed["rm_category_2"] = 0
                parsed["opr_positions"] = 0
                parsed["opr_persons"] = 0

        # Извлекаем reasoning для логов
        reasoning = parsed.get("reasoning", "")
        if reasoning:
            logger.info(f"[LLM Reasoning] {str(reasoning)[:200]}...")

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
            logger.info(f"[LLM] Auto-confidence: {confidence} (filled={filled})")

        return parsed

    def validate_rm(self, llm_result: dict, extracted_rm: int) -> Tuple[bool, int]:
        """Валидация РМ от LLM. Возвращает (needs_review, validated_rm)."""
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
                logger.warning(
                    f"⚠️ Расхождение при низком confidence: LLM={llm_rm} vs {extracted_rm} "
                    f"(confidence={llm_confidence:.2f}, ratio={ratio:.1f})"
                )
                return True, extracted_rm

        if llm_rm > 200 and llm_confidence < 0.3:
            logger.warning(
                f"⚠️ ФАНТОМНЫЕ РМ: rm_total={llm_rm}, confidence={llm_confidence:.2f} < 0.3. "
                f"Игнорируем LLM, используем extracted={extracted_rm}"
            )
            return True, extracted_rm or 0

        return False, llm_rm
