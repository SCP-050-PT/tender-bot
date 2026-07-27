"""
core/analyzer.py
Главный анализатор тендера.
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - РЕФАКТОРИНГ: Убраны _TYPE_KEYWORDS, _extract_params_from_text(), inline промпт
  - Тип определяется через tender_type.TenderTypeDetector
  - Параметры извлекаются через param_extractor.TenderParamExtractor
  - Промпт загружается из system_prompt.txt
  - URL строится через url_builder.TenderURLBuilder
"""

import json
import re
from typing import Optional, Tuple
from dataclasses import dataclass
from loguru import logger

from config.settings import settings
from config.prompts import load_system_prompt
from core.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.text_extractor import TenderTextExtractor, ExtractedParams

# ← v6.3: Новые импорты
from core.tender_type import TenderTypeDetector, TypeDetectionResult
from core.param_extractor import TenderParamExtractor
from utils.url_builder import get_url_builder
from utils.price_parser import format_for_sheets

# ← v6.3: УБРАНЫ _TYPE_ALIASES, _TYPE_KEYWORDS, _EDUCATION_EXCLUSIVE_KEYWORDS
# → Перенесены в tender_type.py

# ← v6.3: УБРАН _VARIANT_KEYWORDS и _detect_sout_variant()
# → Перенесены в tender_type.py

# ← v6.3: УБРАН _normalize_tender_type()
# → Перенесён в tender_type.py

_system_prompt = None


def get_system_prompt() -> str:
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
    return """Ты — аналитик тендеров компании "АС Безопасности".
Проанализируй текст закупки и извлеки ключевые параметры.

ПРАВИЛА ОПРЕДЕЛЕНИЯ ТИПА:
- "sout" — специальная оценка условий труда, СОУТ
- "education" — обучение, повышение квалификации, переподготовка
- "plk" — производственный лабораторный контроль, ПЛК
- "opr" — оценка профессиональных рисков, ОПР
- "combined" — комбинированный лот (СОУТ + ОПР)

ВАЖНО: "Обучение охране труда" — это ТИП "education", НЕ "sout"!

ВАЖНО ПРО КОЛИЧЕСТВО:
- Если в тексте явно указано количество — используй его
- Если количество НЕ указано — верни 0 (не придумывай)

Верни СТРОГО JSON без markdown."""


@dataclass
class TenderAnalysis:
    tender_id: str
    tender_type: str
    nmck: float
    calculated_price: float
    recommended_price: float
    margin_percent: float
    margin_rub: float
    cost_price: float
    risk_level: str
    decision: str
    comment: str
    red_flags: list
    transport_cost: float
    subcontractor_cost: float
    guarantee_cost: float
    details: dict
    raw_llm_response: Optional[dict] = None
    quantity_source: str = "unknown"
    needs_manual_review: bool = False
    llm_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "tender_id": self.tender_id,
            "tender_type": self.tender_type,
            "nmck": self.nmck,
            "calculated_price": round(self.calculated_price, 2),
            "recommended_price": round(self.recommended_price, 2),
            "margin_percent": round(self.margin_percent, 2),
            "margin_rub": round(self.margin_rub, 2),
            "cost_price": round(self.cost_price, 2),
            "risk_level": self.risk_level,
            "decision": self.decision,
            "comment": self.comment,
            "red_flags": self.red_flags,
            "transport_cost": round(self.transport_cost, 2),
            "subcontractor_cost": round(self.subcontractor_cost, 2),
            "guarantee_cost": round(self.guarantee_cost, 2),
            "details": self.details,
            "quantity_source": self.quantity_source,
            "needs_manual_review": self.needs_manual_review,
            "llm_confidence": self.llm_confidence,
        }

    def to_sheets_row(self, law_type: str = "44") -> dict:
        # ← v6.3: URL через url_builder
        url_builder = get_url_builder()
        tender_url = url_builder.build_common_info_url(
            reg_number=self.tender_id, law_type=law_type
        )
        return {
            "Наименование услуг": self.details.get("service_name", "Не определено"),
            "Количество": self.details.get("quantity", 1),
            "Способ проведения закупки": self.details.get("procurement_method", ""),
            "НМЦК": self.nmck,
            "Ссылка на тендер": tender_url,
            "ЭТП": self.details.get("etp", ""),
            "Регион": self.details.get("region", ""),
            "Обеспечение заявки": self.details.get("application_guarantee", ""),
            "Обеспечение контракта": self.details.get("contract_guarantee", ""),
            "Срок подачи заявки до": self.details.get("deadline_date", ""),
            "Решение по участию": self.decision,
            "Цена предложения": self.recommended_price,
            "Результат": "",
            "Комментарии руководителя отдела по участию": self._format_comment(),
        }

    def _format_comment(self) -> str:
        lines = [
            f"Тип: {self.tender_type}",
            f"НМЦК: {self.nmck:,.0f} ₽",
            f"Расчётная цена: {self.calculated_price:,.0f} ₽",
            f"Рекомендуемая цена: {self.recommended_price:,.0f} ₽",
            f"Маржа: {self.margin_percent:.1f}% ({self.margin_rub:,.0f} ₽)",
            f"Себестоимость: {self.cost_price:,.0f} ₽",
            f"Уровень риска: {self.risk_level}",
            f"Решение: {self.decision}",
            f"Источник количества: {self.quantity_source}",
        ]
        if self.needs_manual_review:
            lines.append(
                "⚠️ ТРЕБУЕТСЯ РУЧНОЙ АНАЛИЗ: количество не определено или ненадёжно"
            )
        if self.llm_confidence > 0:
            lines.append(f"Уверенность ИИ: {self.llm_confidence:.2f}")
        lines.extend(["", "Риски и флаги:"])
        for flag in self.red_flags:
            lines.append(f"• {flag}")
        lines.extend(
            [
                "",
                f"Транспортные: {self.transport_cost:,.0f} ₽",
                f"Субподряд: {self.subcontractor_cost:,.0f} ₽",
                f"Обеспечение: {self.guarantee_cost:,.0f} ₽",
            ]
        )
        return "\n".join(lines)


class TenderAnalyzer:
    def __init__(self, llm_client=None, calculator=None, risk_analyzer=None):
        self._llm = llm_client
        self.calc = calculator or TenderCalculator()
        self.risk = risk_analyzer or RiskAnalyzer()
        self.text_extractor = TenderTextExtractor()
        # ← v6.3: Новые компоненты
        self.type_detector = TenderTypeDetector()
        self.param_extractor = TenderParamExtractor()
        self.url_builder = get_url_builder()
        logger.info("TenderAnalyzer инициализирован (v6.3)")

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

    def _call_llm(
        self,
        tender_text: str,
        tender_info: dict,
        extracted_params: ExtractedParams = None,
    ) -> Optional[dict]:
        if not self.llm or self.llm is False:
            return None

        # ← v6.3: Используем системный промпт из файла
        system_prompt = get_system_prompt()

        # Формируем user_message через param_extractor
        if extracted_params:
            new_params = self._legacy_to_new(extracted_params)
            user_message = self.param_extractor.build_enriched_prompt(
                new_params,
                tender_text,
                nmck=tender_info.get("nmck", 0),
                tender_type_hint=self.type_detector.detect(tender_text).tender_type,
            )
        else:
            user_message = tender_text[:15000]

        # Дополняем системный промпт информацией о закупке
        system_prompt_enriched = f"""{system_prompt}

Информация о закупке:
- Название: {tender_info.get("purchase_name", "")}
- Заказчик: {tender_info.get("customer_name", "")}
- Регион: {tender_info.get("customer_region", "")}
- НМЦК: {tender_info.get("nmck", "")}
- Срок подачи заявок: {tender_info.get("deadline_date", "")}

ВАЖНО ПРО ВАРИАНТ СОУТ:
- variant=1 (по умолчанию): 20% основных РМ по категориям + аналогия 100₽/РМ
- variant=2: если в тексте есть "карты", "индивидуальные карты"
- variant=3: если в тексте есть "протоколы", "комплекты протоколов"

ВАЖНО ПРО ОЧНУЮ ЧАСТЬ ОБУЧЕНИЯ:
- teacher_days: из ТЗ, не авто
- accommodation_nights: из ТЗ или teacher_days
- transport_km: расстояние в км (0 если не указано)
- venue_rent_days: из ТЗ
- manikin_days: 1 если "первая помощь" в ТЗ

ВАЖНО ПРО КОМАНДИРОВОЧНЫЕ ДЛЯ СОУТ:
- cities_count: количество уникальных городов (не адресов)
- trip_days: длительность выезда (обычно 3)

ВАЖНО ПРО COMBINED:
- opr_positions: количество должностей для ОПР
- opr_persons: количество человек для ОПР

ВАЖНО ПРО СЕЗОННОСТЬ:
- is_seasonal: отопительный сезон / сезонные РМ

ВАЖНО ПРО CONFIDENCE:
- 1.0 — все параметры явно указаны
- 0.7–0.9 — большинство найдены
- 0.4–0.6 — часть не указана явно
- 0.1–0.3 — мало данных
- НЕ оставляй 0.0 по умолчанию — оцени реально!

Верни СТРОГО JSON без markdown:
{{
  "tender_type": "sout|education|plk|opr|combined",
  "variant": 1,
  "confidence": 0.0,
  "students_count": 0, "certificates": 0, "is_distance": true,
  "rm_total": 0, "rm_category_1": 0, "rm_category_2": 0,
  "has_iii": false, "iii_count": 0,
  "points_count": 0, "factors_count": 0,
  "delivery_count": 1, "is_annual": false,
  "needs_siz_norms": false, "needs_dsiz_norms": false, "needs_iot_norms": false,
  "needs_subcontractor": false, "deadline_days": 0, "addresses_count": 1,
  "cities_count": 1, "trip_days": 3,
  "has_venue": false, "urgency": "normal|high|critical",
  "application_guarantee": "", "contract_guarantee": "", "guarantee_method": "",
  "special_requirements": [], "red_flags": [], "notes": "",
  "teacher_days": 0, "accommodation_nights": 0, "transport_km": 0,
  "venue_rent_days": 0, "manikin_days": 0,
  "opr_positions": 0, "opr_persons": 0,
  "is_seasonal": false
}}"""

        try:
            result = self.llm.send(
                system_prompt=system_prompt_enriched,
                user_message=user_message,
                temperature=0.1,
                max_tokens=2000,
            )
            if isinstance(result, str):
                text = result.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                parsed = json.loads(text)
                # ← v6.3: Нормализация типа через TenderTypeDetector
                parsed["tender_type"] = self.type_detector._normalize_alias(
                    parsed.get("tender_type", "unknown")
                )
                return parsed
            elif isinstance(result, dict):
                result["tender_type"] = self.type_detector._normalize_alias(
                    result.get("tender_type", "unknown")
                )
                return result
            return None
        except Exception as e:
            logger.error(f"Ошибка LLM-анализа: {e}")
            return None

    # ← v6.3: УБРАН _extract_params_from_text()
    # → Логика перенесена в param_extractor.py

    def _validate_llm_rm(self, llm_result: dict, extracted_rm: int) -> Tuple[bool, int]:
        if not llm_result or not isinstance(llm_result, dict):
            return False, extracted_rm or 0

        llm_rm = llm_result.get("rm_total", 0)
        llm_confidence = llm_result.get("confidence", 0.0)

        if llm_rm == 0:
            return False, extracted_rm or 0

        if extracted_rm is None:
            extracted_rm = 0

        if llm_rm > 200 and llm_confidence < 0.3:
            logger.warning(
                f"⚠️ ФАНТОМНЫЕ РМ ОТ LLM: rm_total={llm_rm}, confidence={llm_confidence:.2f}. "
                f"Игнорируем LLM, используем extracted={extracted_rm}"
            )
            return True, extracted_rm

        if extracted_rm > 0 and llm_confidence < 0.3:
            ratio = max(llm_rm, extracted_rm) / min(llm_rm, extracted_rm)
            if ratio > 3:
                logger.warning(
                    f"⚠️ Расхождение LLM vs extracted: {llm_rm} vs {extracted_rm} "
                    f"(confidence={llm_confidence:.2f}, ratio={ratio:.1f}). Приоритет у extracted."
                )
                return True, extracted_rm

        return False, llm_rm

    def analyze(
        self,
        tender_text: str,
        tender_id: str = None,
        nmck: float = None,
        region: str = None,
        procurement_method: str = None,
        etp: str = None,
        deadline_date: str = None,
        law_type: str = "44",
        tender_info: dict = None,
    ):
        logger.info(f"Начинаю анализ тендера {tender_id or 'N/A'}")
        tender_info = tender_info or {}
        actual_nmck = nmck or 0

        nmck_red_flags = []
        if actual_nmck > 0 and actual_nmck < 100000:
            nmck_red_flags.append(
                f"⚠️ НМЦК {actual_nmck:,.0f}₽ ниже минимального порога 100 000₽"
            )

        extracted = None
        extracted_rm = 0
        extracted_type_hint = None

        if tender_text and len(tender_text) > 100:
            try:
                extracted = self.text_extractor.extract(
                    text=tender_text,
                    tender_type_hint=self.type_detector.detect(tender_text).tender_type,
                )
                logger.info(f"TextExtractor: confidence={extracted.confidence:.2f}")
                if extracted:
                    extracted_rm = getattr(extracted, "rm_total", 0)
                    extracted_type_hint = getattr(extracted, "tender_type_hint", None)
            except Exception as e:
                logger.warning(f"TextExtractor не сработал: {e}")

        llm_result = None
        llm_confidence = 0.0
        if self.llm and self.llm is not False:
            try:
                llm_result = self._call_llm(tender_text, tender_info, extracted)
                if llm_result and isinstance(llm_result, dict):
                    llm_confidence = llm_result.get("confidence", 0.0)
                    logger.info(f"LLM confidence: {llm_confidence:.2f}")
            except Exception as e:
                logger.warning(f"LLM-анализ не удался: {e}")

        # Валидация РМ от LLM
        llm_needs_review = False
        validated_rm = extracted_rm

        if (
            llm_result
            and isinstance(llm_result, dict)
            and "parse_error" not in llm_result
        ):
            llm_needs_review, validated_rm = self._validate_llm_rm(
                llm_result, extracted_rm
            )

            if llm_needs_review:
                llm_result["rm_total"] = validated_rm
                llm_result["needs_manual_review"] = True

            if extracted:
                merged = self.text_extractor.merge_with_llm_result(
                    extracted, llm_result, llm_confidence=llm_confidence
                )
                llm_result = merged

            # Приоритет типа из extracted при низком confidence
            llm_type = llm_result.get("tender_type", "соут") if llm_result else "соут"
            if llm_confidence < 0.3 and extracted_type_hint:
                tender_type = extracted_type_hint
                logger.info(
                    f"[v6.3] Низкий confidence={llm_confidence:.2f}, "
                    f"используем тип из extracted: {extracted_type_hint}"
                )
            else:
                tender_type = llm_type

            details = self._normalize_llm_params(llm_result)
            quantity_source = "llm"
            if llm_result.get("rm_total_source") == "extracted":
                quantity_source = "extracted"
            elif llm_result.get("points_count_source") == "extracted":
                quantity_source = "extracted"
            elif llm_result.get("students_count_source") == "extracted":
                quantity_source = "extracted"
        else:
            # ← v6.3: Fallback — используем param_extractor вместо inline логики
            new_params = self.param_extractor.extract(tender_text)
            details = new_params.to_dict()
            details["tender_type"] = self.type_detector.detect(tender_text).tender_type
            details["variant"] = self.type_detector.detect_variant(tender_text)
            tender_type = details.get("tender_type", "соут")
            quantity_source = "fallback_text"

        # Переопределение из tender_info
        if (
            tender_info.get("students_count") is not None
            and tender_info["students_count"] > 0
        ):
            details["students_count"] = tender_info["students_count"]
            quantity_source = "detail_html"
        if tender_info.get("rm_total") is not None and tender_info["rm_total"] > 0:
            details["rm_total"] = tender_info["rm_total"]
            quantity_source = "detail_html"
        if (
            tender_info.get("points_count") is not None
            and tender_info["points_count"] > 0
        ):
            details["points_count"] = tender_info["points_count"]
            quantity_source = "detail_html"

        # Очные параметры из tender_info
        if tender_info.get("has_full_time") is not None:
            details["is_distance"] = not tender_info["has_full_time"]
        for field in [
            "teacher_days",
            "accommodation_nights",
            "transport_km",
            "venue_rent_days",
            "manikin_days",
        ]:
            if tender_info.get(field) is not None and tender_info[field] > 0:
                details[field] = tender_info[field]

        # v5.3/v5.4 параметры из tender_info
        if (
            tender_info.get("addresses_count") is not None
            and tender_info["addresses_count"] > 0
        ):
            details["addresses_count"] = tender_info["addresses_count"]
        if (
            tender_info.get("cities_count") is not None
            and tender_info["cities_count"] > 0
        ):
            details["cities_count"] = tender_info["cities_count"]
            logger.info(
                f"[v6.3] cities_count из tender_info: {details['cities_count']}"
            )
        if tender_info.get("trip_days") is not None and tender_info["trip_days"] > 0:
            details["trip_days"] = tender_info["trip_days"]
        if (
            tender_info.get("opr_positions") is not None
            and tender_info["opr_positions"] > 0
        ):
            details["opr_positions"] = tender_info["opr_positions"]
        if (
            tender_info.get("opr_persons") is not None
            and tender_info["opr_persons"] > 0
        ):
            details["opr_persons"] = tender_info["opr_persons"]
        if tender_info.get("is_seasonal") is not None:
            details["is_seasonal"] = tender_info["is_seasonal"]

        # Определяем вариант СОУТ
        if tender_type == "соут" or tender_type == "комбинированный":
            sout_variant = self.type_detector.detect_variant(
                tender_text, llm_result.get("variant") if llm_result else None
            )
        else:
            sout_variant = 1
        details["variant"] = sout_variant

        needs_manual_review = llm_needs_review
        quantity = self._get_quantity(tender_type, details)
        if quantity == 0 or quantity is None:
            quantity, quantity_source = self._estimate_quantity_from_nmck(
                tender_type, actual_nmck, quantity_source
            )
            if quantity == 0:
                needs_manual_review = True

        # Валидации
        if quantity > 200 and quantity_source == "llm" and llm_confidence < 0.3:
            needs_manual_review = True

        if (
            quantity > 200
            and quantity_source == "nmck_fallback"
            and llm_confidence < 0.3
        ):
            needs_manual_review = True

        if (
            tender_type != "соут"
            and quantity > 0
            and quantity_source == "nmck_fallback"
        ):
            needs_manual_review = True

        if (
            tender_type == "обучение"
            and details.get("rm_total", 0) > 0
            and details.get("students_count", 0) == 0
        ):
            needs_manual_review = True

        if (
            quantity > 0
            and quantity_source == "nmck_fallback"
            and tender_type != "соут"
            and tender_type != "комбинированный"
        ):
            needs_manual_review = True

        if (
            tender_type == "обучение"
            and details.get("rm_total", 0) > 0
            and quantity_source in ("nmck_fallback", "llm")
        ):
            needs_manual_review = True

        # Устанавливаем quantity в правильное поле
        if tender_type == "соут" or tender_type == "комбинированный":
            details["rm_total"] = quantity
        elif tender_type == "плк":
            details["points_count"] = quantity
        elif tender_type == "обучение":
            details["students_count"] = quantity

        # Расчёт
        if needs_manual_review:
            calc_result = self._create_manual_review_result(tender_type, actual_nmck)
        else:
            calc_result = self._calculate_by_type(tender_type, details, tender_text)

        # Обеспечение
        guarantee_cost = 0
        if actual_nmck > 0:
            try:
                guarantee_cost = self.calc.calculate_guarantee(
                    contract_sum=actual_nmck, guarantee_type="application"
                )
            except Exception as e:
                logger.warning(f"Не удалось рассчитать обеспечение: {e}")

        # Риски
        risk_result = self.risk.analyze(
            tender_text=tender_text,
            margin_percent=calc_result.margin_percent,
            cost_price=calc_result.cost_price,
            nmck=actual_nmck or 100000,
            deadline_days=details.get("deadline_days", 30),
            volume_large=quantity > 50 if quantity else False,
            region_distance=0,
            venue_required=details.get("has_venue", False),
            addresses_count=details.get("addresses_count", 1),
            cities_count=details.get("cities_count", 1),
            tender_type=tender_type,
            needs_manual_review=needs_manual_review,
            llm_confidence=llm_confidence,
        )

        all_red_flags = list(risk_result.red_flags) + nmck_red_flags

        # Финальная цена
        llm_price = 0
        if llm_result and isinstance(llm_result, dict):
            llm_price = llm_result.get("recommended_price", 0)
        final_price = (
            max(llm_price, calc_result.recommended_price)
            if llm_price > 0
            else calc_result.recommended_price
        )
        margin_rub = final_price - calc_result.cost_price
        margin_percent = (
            (margin_rub / calc_result.cost_price * 100)
            if calc_result.cost_price > 0
            else 0
        )

        # Комментарий
        comment = ""
        if llm_result and isinstance(llm_result, dict):
            comment = llm_result.get("comment", "")
        if not comment:
            comment = self._generate_comment(
                tender_type, calc_result, risk_result, details
            )
        if needs_manual_review:
            comment += f"⚠️ ВНИМАНИЕ: Количество не определено из текста. Ориентировочная оценка по НМЦК: {quantity} ед. Требуется ручная проверка ТЗ."

        if tender_type == "соут":
            variant_names = {
                1: "20% + аналогия 100₽/РМ",
                2: "1 карта + аналогия 200₽/РМ",
                3: "карты + 20% комплектов протоколов",
            }
            comment += f"📋 Вариант расчёта СОУТ: {sout_variant} ({variant_names.get(sout_variant, 'неизвестно')})"

        # ← v6.3: URL через url_builder
        tender_url = self.url_builder.build_common_info_url(
            reg_number=tender_id or "", law_type=law_type
        )

        return TenderAnalysis(
            tender_id=tender_id or "unknown",
            tender_type=tender_type,
            nmck=actual_nmck or 0,
            calculated_price=calc_result.recommended_price,
            recommended_price=final_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            cost_price=calc_result.cost_price,
            risk_level=risk_result.risk_level,
            decision=risk_result.decision,
            comment=comment,
            red_flags=all_red_flags,
            transport_cost=calc_result.transport_cost,
            subcontractor_cost=calc_result.subcontractor_cost,
            guarantee_cost=guarantee_cost,
            details={
                **details,
                "service_name": tender_type,
                "procurement_method": procurement_method or "",
                "tender_url": tender_url,
                "etp": etp or "",
                "region": region or "",
                "deadline_date": deadline_date or "",
                "quantity": quantity or 1,
                "application_guarantee": (
                    llm_result.get("application_guarantee", "") if llm_result else ""
                ),
                "contract_guarantee": (
                    llm_result.get("contract_guarantee", "") if llm_result else ""
                ),
            },
            raw_llm_response=llm_result,
            quantity_source=quantity_source,
            needs_manual_review=needs_manual_review,
            llm_confidence=llm_confidence,
        )

    def _get_quantity(self, tender_type: str, details: dict) -> int:
        tt = tender_type.lower()
        if "соут" in tt or "опр" in tt or "комбинированный" in tt:
            return details.get("rm_total", 0) or 0
        elif "плк" in tt:
            return details.get("points_count", 0) or 0
        elif "обучение" in tt:
            return details.get("students_count", 0) or 0
        return 0

    def _estimate_quantity_from_nmck(
        self, tender_type: str, nmck: float, current_source: str
    ) -> Tuple[int, str]:
        if nmck <= 0:
            return 0, current_source
        rates = {
            "соут": 1500,
            "плк": 500,
            "обучение": 1500,
            "опр": 1000,
            "комбинированный": 1500,
        }
        tt = tender_type.lower()
        rate = rates.get(tt, 1500)
        estimated = max(1, int(nmck / rate))
        logger.debug(
            f"Fallback по НМЦК: {tender_type} ~{estimated} ед. (rate={rate}₽/ед)"
        )
        return estimated, "nmck_fallback"

    def _create_manual_review_result(self, tender_type: str, nmck: float):
        from core.calculator import CalculationResult

        estimated_cost = nmck * 0.7 if nmck > 0 else 0
        recommended = nmck * 0.85 if nmck > 0 else 0
        return CalculationResult(
            cost_price=estimated_cost,
            recommended_price=recommended,
            margin_percent=10.0,
            margin_rub=recommended - estimated_cost,
            transport_cost=0,
            subcontractor_cost=0,
            details={"note": "Ручной анализ требуется"},
        )

    def _normalize_llm_params(self, llm_result: dict) -> dict:
        has_iii = llm_result.get("has_iii", False)
        iii_count = llm_result.get("iii_count", 0)
        rm_total = llm_result.get("rm_total") or llm_result.get("quantity_rm") or 0
        if has_iii and iii_count == 0 and rm_total > 0:
            iii_count = max(1, int(rm_total * 0.12))
            logger.info(
                f"has_iii=true, аппроксимируем iii_count={iii_count} из rm_total={rm_total}"
            )

        result = {
            "students_count": llm_result.get("students_count")
            or llm_result.get("quantity_students")
            or 0,
            "certificates": llm_result.get("certificates", 0),
            "is_distance": llm_result.get("is_distance", True),
            "delivery_count": llm_result.get("delivery_count", 1),
            "rm_total": rm_total,
            "rm_category_1": llm_result.get("rm_category_1", 0),
            "rm_category_2": llm_result.get("rm_category_2", 0),
            "iii_count": iii_count,
            "is_annual": llm_result.get("is_annual", False),
            "points_count": llm_result.get("points_count")
            or llm_result.get("quantity_points")
            or 0,
            "factors_count": llm_result.get("factors_count", 0),
            "needs_subcontractor": llm_result.get("needs_subcontractor", False),
            "needs_siz_norms": llm_result.get("needs_siz_norms", False),
            "needs_dsiz_norms": llm_result.get("needs_dsiz_norms", False),
            "needs_iot_norms": llm_result.get("needs_iot_norms", False),
            "deadline_days": llm_result.get("deadline_days", 30),
            "addresses_count": llm_result.get("addresses_count", 1),
            "has_venue": llm_result.get("has_venue", False),
            "variant": llm_result.get("variant", 1),
        }

        result["teacher_days"] = llm_result.get("teacher_days", 0)
        result["accommodation_nights"] = llm_result.get("accommodation_nights", 0)
        result["transport_km"] = llm_result.get("transport_km", 0)
        result["venue_rent_days"] = llm_result.get("venue_rent_days", 0)
        result["manikin_days"] = llm_result.get("manikin_days", 0)
        result["trip_days"] = llm_result.get("trip_days", 3)
        result["opr_positions"] = llm_result.get("opr_positions", 0)
        result["opr_persons"] = llm_result.get("opr_persons", 0)
        result["is_seasonal"] = llm_result.get("is_seasonal", False)
        result["llm_confidence"] = llm_result.get("confidence", 0.0)
        result["cities_count"] = llm_result.get("cities_count", 1)

        return result

    def _calculate_by_type(
        self, tender_type: str, details: dict, tender_text: str = ""
    ):
        tt = tender_type.lower()
        variant = details.get("variant", 1)

        # Защита от ошибочного типа "соут" при наличии признаков обучения
        students_count = details.get("students_count", 0)
        teacher_days = details.get("teacher_days", 0)
        if "соут" in tt and (students_count > 0 or teacher_days > 0):
            logger.warning(
                f"[v6.3] Тип '{tt}' переопределён в 'обучение' (students={students_count}, teacher_days={teacher_days})"
            )
            tt = "обучение"

        if "комбинированный" in tt:
            return self.calc.calculate_combined(
                rm_total=details.get("rm_total", 0),
                rm_category_1=details.get("rm_category_1", 0),
                rm_category_2=details.get("rm_category_2", 0),
                rm_with_iii=details.get("iii_count", 0),
                opr_positions=details.get("opr_positions", 0),
                opr_persons=details.get("opr_persons", 0),
                variant=variant,
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                cities_count=details.get("cities_count", 1),
                addresses_count=details.get("addresses_count", 1),
                trip_days=details.get("trip_days", 3),
                is_seasonal=details.get("is_seasonal", False),
            )

        if "обучение" in tt:
            return self.calc.calculate_education(
                students_count=details.get("students_count", 0),
                certificates=details.get("certificates", 0),
                is_distance=details.get("is_distance", True),
                delivery_count=details.get("delivery_count", 1),
                teacher_days=details.get("teacher_days", 0),
                accommodation_nights=details.get("accommodation_nights", 0),
                transport_km=details.get("transport_km", 0),
                venue_rent_days=details.get("venue_rent_days", 0),
                manikin_days=details.get("manikin_days", 0),
                tender_text=tender_text,
            )
        elif "соут" in tt:
            cities_count = details.get(
                "cities_count", details.get("addresses_count", 1)
            )
            return self.calc.calculate_sout(
                rm_total=details.get("rm_total", 0),
                rm_category_1=details.get("rm_category_1", 0),
                rm_category_2=details.get("rm_category_2", 0),
                rm_with_iii=details.get("iii_count", 0),
                variant=variant,
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                cities_count=cities_count,
                addresses_count=details.get("addresses_count", 1),
                trip_days=details.get("trip_days", 3),
                is_seasonal=details.get("is_seasonal", False),
            )
        elif "плк" in tt:
            return self.calc.calculate_plk(
                points_count=details.get("points_count", 0),
                factors_count=details.get("factors_count", 0),
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                needs_subcontractor=details.get("needs_subcontractor", False),
            )
        elif "опр" in tt:
            return self.calc.calculate_opr(
                rm_count=details.get("rm_total", 0),
                delivery_count=details.get("delivery_count", 1),
                needs_siz_norms=details.get("needs_siz_norms", False),
                needs_dsiz_norms=details.get("needs_dsiz_norms", False),
                needs_iot_norms=details.get("needs_iot_norms", False),
            )
        else:
            logger.warning(f"Неизвестный тип тендера: {tender_type}")
            return self.calc.calculate_sout(
                rm_total=details.get("rm_total", 1),
                variant=variant,
                cities_count=details.get(
                    "cities_count", details.get("addresses_count", 1)
                ),
                addresses_count=details.get("addresses_count", 1),
                trip_days=details.get("trip_days", 3),
            )

    def _generate_comment(self, tender_type, calc_result, risk_result, details):
        lines = [
            f"Анализ тендера типа «{tender_type}»",
            "",
            f"Расчётная себестоимость: {calc_result.cost_price:,.0f} ₽",
            f"Рекомендуемая цена: {calc_result.recommended_price:,.0f} ₽",
            f"Маржа: {calc_result.margin_percent:.1f}%",
            "",
            f"Решение: {risk_result.decision}",
            f"Уровень риска: {risk_result.risk_level}",
        ]
        if risk_result.red_flags:
            lines.extend(["", "Выявленные риски:"])
            for flag in risk_result.red_flags:
                lines.append(f"• {flag}")
        return "\n".join(lines)

    def _legacy_to_new(self, legacy: ExtractedParams):
        """Конвертирует legacy ExtractedParams в новый формат."""
        from core.param_extractor import ExtractedParams as NewExtractedParams

        new = NewExtractedParams()
        for attr in dir(new):
            if not attr.startswith("_") and hasattr(legacy, attr):
                setattr(new, attr, getattr(legacy, attr))
        return new
