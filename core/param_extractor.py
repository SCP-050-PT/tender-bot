"""
Фасад для извлечения параметров тендера.
Делегирует работу специализированным экстракторам и merger'у.

Багфиксы v6.6-r2:
  - Двухуровневое извлечение: структурированное (КТРУ/XLS) > regex > LLM
  - Приоритизация источников через ParamMerger
  - Guard'ы: фантомные РМ, students_count > 200, deadline < 3 дней
  - needs_manual_review при подозрительных значениях
  - Регионы вынесены в общий конфиг
"""

import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from loguru import logger

from core.config.regions_config import RUSSIAN_REGIONS, MONTHS_RU
from core.extractors.regex_extractor import RegexExtractor
from core.extractors.table_extractor import TableExtractor
from core.merger.param_merger import ParamMerger, MergedParams


@dataclass
class ExtractedParams:
    """Результат извлечения параметров (обратная совместимость)."""
    # Основные количества
    rm_total: Optional[int] = None
    rm_category_1: Optional[int] = None
    rm_category_2: Optional[int] = None
    rm_with_iii: Optional[int] = None
    points_count: Optional[int] = None
    students_count: Optional[int] = None
    factors_count: Optional[int] = None

    # Логистика
    addresses_count: Optional[int] = None
    cities_count: Optional[int] = None
    regions_count: Optional[int] = None
    trips: Optional[int] = None
    trip_days: Optional[int] = None

    # Сроки
    deadline_days: Optional[int] = None
    deadline_text: Optional[str] = None

    # Обеспечение
    application_guarantee: Optional[str] = None
    contract_guarantee: Optional[str] = None
    guarantee_method: Optional[str] = None

    # Очная часть
    has_full_time: bool = False
    teacher_days: Optional[int] = None
    accommodation_nights: Optional[int] = None
    transport_km: Optional[int] = None
    venue_rent_days: Optional[int] = None
    manikin_days: Optional[int] = None

    # Флаги
    has_polygon: bool = False
    is_urgent: bool = False
    urgency_days: Optional[int] = None
    is_seasonal: bool = False
    needs_siz_norms: bool = False
    needs_dsiz_norms: bool = False
    needs_iot_norms: bool = False

    # ОПР
    opr_positions: Optional[int] = None
    opr_persons: Optional[int] = None

    # Регион
    region_hint: Optional[str] = None

    # Метаданные
    confidence: float = 0.0
    needs_manual_review: bool = False
    review_reason: str = ""
    raw_matches: List[Dict] = field(default_factory=list)

    # Источники
    rm_total_source: str = ""
    points_count_source: str = ""
    students_count_source: str = ""
    sources: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rm_total": self.rm_total,
            "rm_category_1": self.rm_category_1,
            "rm_category_2": self.rm_category_2,
            "rm_with_iii": self.rm_with_iii,
            "points_count": self.points_count,
            "students_count": self.students_count,
            "factors_count": self.factors_count,
            "addresses_count": self.addresses_count,
            "cities_count": self.cities_count,
            "regions_count": self.regions_count,
            "trips": self.trips,
            "trip_days": self.trip_days,
            "deadline_days": self.deadline_days,
            "deadline_text": self.deadline_text,
            "has_full_time": self.has_full_time,
            "teacher_days": self.teacher_days,
            "accommodation_nights": self.accommodation_nights,
            "transport_km": self.transport_km,
            "venue_rent_days": self.venue_rent_days,
            "manikin_days": self.manikin_days,
            "is_urgent": self.is_urgent,
            "is_seasonal": self.is_seasonal,
            "opr_positions": self.opr_positions,
            "opr_persons": self.opr_persons,
            "region_hint": self.region_hint,
            "confidence": self.confidence,
            "needs_manual_review": self.needs_manual_review,
            "review_reason": self.review_reason,
            "sources": self.sources,
        }


class TenderParamExtractor:
    """
    Единый фасад для извлечения параметров.
    Заменяет: text_extractor.TenderTextExtractor, analyzer._extract_params_from_text().
    """

    def __init__(self):
        self.regex_extractor = RegexExtractor()
        self.table_extractor = TableExtractor()
        self.param_merger = ParamMerger()
        logger.info("[TenderParamExtractor] Инициализирован (v6.6-r2)")

    def extract(
        self,
        text: str,
        nmck: float = 0,
        tender_type_hint: str = None,
        ktru_params: Optional[Dict[str, Any]] = None,
        table_params: Optional[Dict[str, Any]] = None,
    ) -> ExtractedParams:
        """
        Извлекает все параметры из текста с двухуровневой системой.

        Args:
            text: Текст документов
            nmck: НМЦК для guard'ов
            tender_type_hint: Подсказка типа тендера
            ktru_params: Параметры из КТРУ (confidence=1.0)
            table_params: Параметры из XLS/DOCX таблиц (confidence=1.0)
        """
        if not text or len(text) < 50:
            logger.warning("[TenderParamExtractor] Текст слишком короткий")
            return ExtractedParams(confidence=0.0)

        # === Уровень 1: Структурированное извлечение (КТРУ, таблицы) ===
        # Передаётся извне через ktru_params / table_params

        # === Уровень 2: Regex-извлечение из текста ===
        regex_params = self._extract_regex(text, tender_type_hint)

        # === Уровень 3: Merge с приоритизацией ===
        merged = self.param_merger.merge(
            ktru_params=ktru_params,
            table_params=table_params,
            regex_params=regex_params,
            llm_params=None,  # LLM-результат добавляется позже в analyzer
            llm_confidence=0.0,
            nmck=nmck,
        )

        # Конвертируем MergedParams -> ExtractedParams (обратная совместимость)
        result = self._merged_to_extracted(merged)

        logger.info(
            f"[TenderParamExtractor] РМ={result.rm_total}(src={result.sources.get('rm_total', '')}), "
            f"кат.1={result.rm_category_1}, кат.2={result.rm_category_2}, "
            f"ИИИ={result.rm_with_iii}, точек={result.points_count}, слушателей={result.students_count}, "
            f"срок={result.deadline_days}д, trip_days={result.trip_days}, "
            f"teacher_days={result.teacher_days}, manikin_days={result.manikin_days}, "
            f"сезон={result.is_seasonal}, opr_pos={result.opr_positions}, opr_per={result.opr_persons}, "
            f"confidence={result.confidence:.2f}, review={result.needs_manual_review}"
        )

        return result

    def _extract_regex(self, text: str, tender_type_hint: str = None) -> Dict[str, Any]:
        """Извлекает параметры через regex."""
        rex = self.regex_extractor
        text_lower = text.lower()

        params = {
            "rm_total": rex.extract_number(text, rex.RM_PATTERNS),
            "rm_category_1": rex.extract_number(text, rex.RM_CATEGORY_PATTERNS, field="rm_category_1"),
            "rm_category_2": rex.extract_number(text, rex.RM_CATEGORY_PATTERNS, field="rm_category_2"),
            "rm_with_iii": rex.extract_number(text, rex.III_PATTERNS),
            "points_count": rex.extract_number(text, rex.POINTS_PATTERNS),
            "students_count": rex.extract_number(text, rex.STUDENTS_PATTERNS),
            "factors_count": rex.extract_number(text, rex.FACTORS_PATTERNS),
            "addresses_count": rex.extract_number(text, rex.ADDRESSES_PATTERNS),
            "deadline_days": rex.extract_number(text, rex.DEADLINE_PATTERNS),
            "opr_positions": rex.extract_number(text, rex.OPR_POSITIONS_PATTERNS),
            "opr_persons": rex.extract_number(text, rex.OPR_PERSONS_PATTERNS),

            # Trip days
            "trip_days": self._extract_trip_days(text_lower),

            # Булевы флаги
            "has_full_time": rex.detect_full_time(text),
            "has_polygon": rex.detect_polygon(text),
            "is_urgent": False,
            "urgency_days": None,
            "is_seasonal": rex.detect_seasonal(text),
            "needs_siz_norms": rex.detect_norms(text, "needs_siz_norms"),
            "needs_dsiz_norms": rex.detect_norms(text, "needs_dsiz_norms"),
            "needs_iot_norms": rex.detect_norms(text, "needs_iot_norms"),

            # Строковые поля
            "deadline_text": self._extract_deadline_date(text),
            "application_guarantee": self._extract_guarantee(text, "application"),
            "contract_guarantee": self._extract_guarantee(text, "contract"),
            "guarantee_method": self._extract_guarantee_method(text),
            "region_hint": self._extract_region(text),

            # Очные параметры
            "teacher_days": self._extract_teacher_days(text),
            "accommodation_nights": self._extract_accommodation_nights(text),
            "transport_km": self._extract_transport_km(text),
            "venue_rent_days": self._extract_venue_rent_days(text),
            "manikin_days": self._extract_manikin_days(text, tender_type_hint),

            "confidence": 0.0,
        }

        # Срочность
        params["is_urgent"], params["urgency_days"] = rex.detect_urgency(text)

        # Confidence
        params["confidence"] = self._calculate_confidence(params)

        return params

    def _extract_trip_days(self, text_lower: str) -> Optional[int]:
        match = re.search(
            r"(?:срок|длительность)[\s]*(?:выезда|командировки)[\s]*[\-—]?[\s]*(\d+)[\s]*дн",
            text_lower,
        )
        if match:
            return int(match.group(1))
        return 3  # дефолт

    def _extract_deadline_date(self, text: str) -> Optional[str]:
        text_lower = text.lower()

        # "не позднее 15 октября 2026"
        match = re.search(
            r"не[\s]*позднее[\s]*[«]?(\d{1,2})[\s]*([а-я]+)[\s]*(\d{4})?",
            text_lower,
        )
        if match:
            day, month_str, year = match.groups()
            month = MONTHS_RU.get(month_str.lower(), 0)
            if month > 0:
                year_str = year if year else "2026"
                return f"{day}.{month:02d}.{year_str}"

        # "срок оказания услуг: 15 октября 2026"
        match = re.search(
            r"срок[\s]*(?:оказания|выполнения)[\s]*(?:услуг|работ)[\s]*[\-—]?[\s]*[«]?(\d{1,2})[\s]*([а-я]+)[\s]*(\d{4})?",
            text_lower,
        )
        if match:
            day, month_str, year = match.groups()
            month = MONTHS_RU.get(month_str.lower(), 0)
            if month > 0:
                year_str = year if year else "2026"
                return f"{day}.{month:02d}.{year_str}"

        return None

    def _extract_guarantee(self, text: str, guarantee_type: str) -> Optional[str]:
        from core.extractors.regex_extractor import RegexExtractor
        patterns = [p for p in RegexExtractor.GUARANTEE_PATTERNS if p[1] == guarantee_type]
        for pattern, field, weight, _ in patterns:
            try:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = (
                        match.group(1).strip()
                        if match.lastindex and match.lastindex >= 1
                        else ""
                    )
                    value = re.sub(r"\s+", " ", value)
                    value = value[:200]
                    if len(value) > 5:
                        return value
            except re.error:
                continue
        return None

    def _extract_guarantee_method(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        if "банковская гарантия" in text_lower or "банковск" in text_lower:
            return "БГ"
        elif "депозит" in text_lower or "зачислен" in text_lower:
            return "депозит"
        elif "тариф" in text_lower:
            return "тариф"
        elif "не требуется" in text_lower or "обеспечение не" in text_lower:
            return "не требуется"
        return None

    def _extract_teacher_days(self, text: str) -> Optional[int]:
        text_lower = text.lower()
        match = re.search(r"преподавател[ья]\s*(?:работ[аеы]\s*)?(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(
            r"учебн[ыо][\s]*дн[ея][\s]*(?:для[\s]+преподавателя)?\s*[\-—]?\s*(\d+)",
            text_lower,
        )
        if match:
            return int(match.group(1))
        return None

    def _extract_accommodation_nights(self, text: str) -> Optional[int]:
        text_lower = text.lower()
        match = re.search(r"проживани[ея]\s*(?:в[\s]+гостинице)?\s*(\d+)\s*ноч", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(r"ночей[\s]*проживани[ея]\s*[\-—]?\s*(\d+)", text_lower)
        if match:
            return int(match.group(1))
        return None

    def _extract_transport_km(self, text: str) -> Optional[int]:
        text_lower = text.lower()
        match = re.search(r"расстояни[ея]\s*[\-—]?\s*(\d+)\s*км", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d+)\s*км[\s]*(?:от[\s]+|до[\s]+)", text_lower)
        if match:
            return int(match.group(1))
        return None

    def _extract_venue_rent_days(self, text: str) -> Optional[int]:
        text_lower = text.lower()
        match = re.search(r"аренд[аы][\s]*помещени[ея]\s*(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(
            r"учебн[ыо][\s]*помещени[ея]\s*[\-—]?\s*(\d+)\s*дн", text_lower
        )
        if match:
            return int(match.group(1))
        return None

    def _extract_manikin_days(self, text: str, tender_type_hint: str = None) -> Optional[int]:
        text_lower = text.lower()
        match = re.search(r"манекен[аовы]\s*(?:на[\s]+)?(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(r"тренаж[её]р[аовы]\s*(?:на[\s]+)?(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        if "первая помощь" in text_lower or "манекен" in text_lower:
            return 1
        return None

    def _extract_region(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        for region in RUSSIAN_REGIONS:
            if region.lower() in text_lower:
                return region
        return None

    def _calculate_confidence(self, params: Dict[str, Any]) -> float:
        score = 0.0
        max_score = 0.0

        if params.get("rm_total") is not None:
            score += 0.3
        max_score += 0.3

        if params.get("rm_category_1") is not None or params.get("rm_category_2") is not None:
            score += 0.2
        max_score += 0.2

        if params.get("points_count") is not None:
            score += 0.3
        max_score += 0.3

        if params.get("students_count") is not None:
            score += 0.3
        max_score += 0.3

        if params.get("deadline_days") is not None:
            score += 0.1
        max_score += 0.1

        if params.get("application_guarantee") is not None:
            score += 0.05
        max_score += 0.05

        if params.get("region_hint") is not None:
            score += 0.05
        max_score += 0.05

        if max_score == 0:
            return 0.0
        return min(1.0, score / max_score)

    def _merged_to_extracted(self, merged: MergedParams) -> ExtractedParams:
        """Конвертирует MergedParams в ExtractedParams (обратная совместимость)."""
        return ExtractedParams(
            rm_total=merged.rm_total,
            rm_category_1=merged.rm_category_1,
            rm_category_2=merged.rm_category_2,
            rm_with_iii=merged.rm_with_iii,
            points_count=merged.points_count,
            students_count=merged.students_count,
            factors_count=merged.factors_count,
            addresses_count=merged.addresses_count,
            cities_count=merged.cities_count,
            regions_count=merged.regions_count,
            trips=merged.trips,
            trip_days=merged.trip_days,
            deadline_days=merged.deadline_days,
            deadline_text=merged.deadline_text,
            application_guarantee=merged.application_guarantee,
            contract_guarantee=merged.contract_guarantee,
            guarantee_method=merged.guarantee_method,
            has_full_time=merged.has_full_time,
            teacher_days=merged.teacher_days,
            accommodation_nights=merged.accommodation_nights,
            transport_km=merged.transport_km,
            venue_rent_days=merged.venue_rent_days,
            manikin_days=merged.manikin_days,
            has_polygon=merged.has_polygon,
            is_urgent=merged.is_urgent,
            urgency_days=merged.urgency_days,
            is_seasonal=merged.is_seasonal,
            needs_siz_norms=merged.needs_siz_norms,
            needs_dsiz_norms=merged.needs_dsiz_norms,
            needs_iot_norms=merged.needs_iot_norms,
            opr_positions=merged.opr_positions,
            opr_persons=merged.opr_persons,
            region_hint=merged.region_hint,
            confidence=merged.confidence,
            needs_manual_review=merged.needs_manual_review,
            review_reason=merged.review_reason,
            sources=merged.sources,
        )

    def merge_with_llm_result(
        self,
        extracted: ExtractedParams,
        llm_result: dict,
        llm_confidence: float = 0.0,
        nmck: float = 0,
    ) -> ExtractedParams:
        """
        Объединяет извлечённые параметры с результатом LLM.
        При llm_confidence < 0.3 приоритет у extracted.
        """
        if not llm_result or not isinstance(llm_result, dict):
            return extracted

        # Конвертируем ExtractedParams -> MergedParams
        merged = MergedParams(
            rm_total=extracted.rm_total,
            rm_category_1=extracted.rm_category_1,
            rm_category_2=extracted.rm_category_2,
            rm_with_iii=extracted.rm_with_iii,
            points_count=extracted.points_count,
            students_count=extracted.students_count,
            factors_count=extracted.factors_count,
            addresses_count=extracted.addresses_count,
            cities_count=extracted.cities_count,
            regions_count=extracted.regions_count,
            trips=extracted.trips,
            trip_days=extracted.trip_days,
            deadline_days=extracted.deadline_days,
            deadline_text=extracted.deadline_text,
            application_guarantee=extracted.application_guarantee,
            contract_guarantee=extracted.contract_guarantee,
            guarantee_method=extracted.guarantee_method,
            has_full_time=extracted.has_full_time,
            teacher_days=extracted.teacher_days,
            accommodation_nights=extracted.accommodation_nights,
            transport_km=extracted.transport_km,
            venue_rent_days=extracted.venue_rent_days,
            manikin_days=extracted.manikin_days,
            has_polygon=extracted.has_polygon,
            is_urgent=extracted.is_urgent,
            urgency_days=extracted.urgency_days,
            is_seasonal=extracted.is_seasonal,
            needs_siz_norms=extracted.needs_siz_norms,
            needs_dsiz_norms=extracted.needs_dsiz_norms,
            needs_iot_norms=extracted.needs_iot_norms,
            opr_positions=extracted.opr_positions,
            opr_persons=extracted.opr_persons,
            region_hint=extracted.region_hint,
            confidence=extracted.confidence,
            needs_manual_review=extracted.needs_manual_review,
            review_reason=extracted.review_reason,
            sources=dict(extracted.sources),
        )

        # Merge с LLM
        merged = self.param_merger.merge(
            ktru_params=None,
            table_params=None,
            regex_params=None,
            llm_params=llm_result,
            llm_confidence=llm_confidence,
            nmck=nmck,
        )

        # НОВОЕ (всегда сохранять КТРУ, если LLM не дал лучше):
        for field in ["rm_total", "students_count", "points_count"]:
            extracted_val = getattr(extracted, field)
            if extracted_val is not None and extracted_val > 0:
                llm_val = getattr(merged, field)
                if llm_val is None or llm_val == 0:
                    setattr(merged, field, extracted_val)
                    merged.sources[field] = extracted.sources.get(field, "ktru")

        return self._merged_to_extracted(merged)

    def build_enriched_prompt(
        self,
        params: ExtractedParams,
        original_text: str,
        nmck: float = 0,
        tender_type_hint: str = None,
    ) -> str:
        """Строит обогащённый промпт для LLM."""
        lines = [
            "Проанализируй текст закупки и подтверди или скорректируй параметры.",
            "",
            "=== НАЙДЕНО В ТЕКСТЕ (проверь и подтверди) ===",
        ]

        fields = [
            ("Рабочих мест (РМ)", params.rm_total),
            ("РМ 1 категории", params.rm_category_1),
            ("РМ 2 категории", params.rm_category_2),
            ("РМ с ИИИ", params.rm_with_iii),
            ("Точек замеров (ПЛК)", params.points_count),
            ("Слушателей", params.students_count),
            ("Вредных факторов", params.factors_count),
            ("Адресов/объектов", params.addresses_count),
            ("Городов", params.cities_count),
            ("Регионов", params.regions_count),
            ("Выездов", params.trips),
            ("Дней выезда", params.trip_days),
            ("Должностей ОПР", params.opr_positions),
            ("Человек ОПР", params.opr_persons),
            ("Срок исполнения", params.deadline_days, "дней"),
            ("Дата окончания", params.deadline_text),
            ("Дней преподавателя", params.teacher_days),
            ("Ночей проживания", params.accommodation_nights),
            ("Расстояние", params.transport_km, "км"),
            ("Дней аренды", params.venue_rent_days),
            ("Дней манекена", params.manikin_days),
        ]

        for field_info in fields:
            label = field_info[0]
            value = field_info[1]
            suffix = field_info[2] if len(field_info) > 2 else ""
            if value is not None:
                lines.append(f"- {label}: {value}{suffix}")

        flags = []
        if params.has_full_time:
            flags.append("⚠️ Обнаружена очная часть / полигон")
        if params.is_urgent:
            flags.append(f"⚠️ Срочный тендер (до {params.urgency_days or 'N/A'} дней)")
        if params.is_seasonal:
            flags.append("⚠️ Сезонность (отопительный сезон / сезонные РМ)")
        if params.needs_siz_norms:
            flags.append("- Требуются нормы СИЗ")
        if params.needs_dsiz_norms:
            flags.append("- Требуются нормы ДСИЗ")
        if params.needs_iot_norms:
            flags.append("- Требуются ИОТ")
        if params.needs_manual_review:
            flags.append(f"🔴 ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА: {params.review_reason}")

        if flags:
            lines.extend(["", "=== ФЛАГИ ==="])
            lines.extend(flags)

        if params.application_guarantee:
            lines.append(f"- Обеспечение заявки: {params.application_guarantee}")
        if params.contract_guarantee:
            lines.append(f"- Обеспечение контракта: {params.contract_guarantee}")

        lines.extend([
            "",
            "=== ЗАДАЧА ===",
            "1. Подтверди найденные значения или укажи правильные",
            "2. Если значение не найдено в тексте — верни 0 (не придумывай)",
            "3. Определи тип тендера: sout|education|plk|opr|combined",
            "4. Извлеки обеспечение заявки и контракта",
            "5. Оцени сроки исполнения в днях",
            "6. Укажи категории РМ (1 и 2) если есть",
            "7. Укажи РМ с ИИИ если есть",
            "8. Укажи addresses_count и trip_days для СОУТ",
            "9. Укажи opr_positions и opr_persons для combined",
            "10. Укажи is_seasonal если есть сезонность",
            "11. Укажи regions_count (количество регионов) для расчёта выездов",
            "",
            "=== ТЕКСТ ДОКУМЕНТОВ ===",
            original_text[:12000],
        ])

        return "\n".join(lines)
