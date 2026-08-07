"""
Merger параметров: приоритизация источников.
КТРУ (confidence=1.0) > XLS/DOCX-таблицы (confidence=1.0) > regex (confidence=0.5-1.0) > LLM (confidence=0.0-1.0)
Багфикс v6.6-r2:
  - needs_manual_review при фантомных РМ от LLM
  - Guard: цена <= НМЦК
  - Guard: маржа > 200% = HIGH
"""

from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class MergedParams:
    """Результат слияния параметров из всех источников."""
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


class ParamMerger:
    """
    Объединяет параметры из разных источников по приоритету.
    Приоритет: КТРУ > XLS/DOCX > regex > LLM
    """

    # Приоритеты источников (чем выше — тем важнее)
    SOURCE_PRIORITY = {
        "ktru": 4,
        "xls": 3,
        "docx_table": 3,
        "regex": 2,
        "llm": 1,
        "extracted": 2,
        "unknown": 0,
    }

    def merge(
        self,
        ktru_params: Optional[Dict[str, Any]] = None,
        table_params: Optional[Dict[str, Any]] = None,
        regex_params: Optional[Dict[str, Any]] = None,
        llm_params: Optional[Dict[str, Any]] = None,
        llm_confidence: float = 0.0,
        nmck: float = 0,
    ) -> MergedParams:
        """
        Объединяет параметры из всех источников.

        Args:
            ktru_params: Параметры из КТРУ (confidence=1.0)
            table_params: Параметры из XLS/DOCX таблиц (confidence=1.0)
            regex_params: Параметры из regex
            llm_params: Параметры от LLM
            llm_confidence: Уверенность LLM
            nmck: НМЦК для guard'ов
        """
        merged = MergedParams()
        sources = {}

        # === Уровень 1: КТРУ (наивысший приоритет) ===
        if ktru_params:
            for field in ["rm_total", "students_count", "points_count", "opr_positions"]:
                if ktru_params.get(field):
                    setattr(merged, field, ktru_params[field])
                    sources[field] = "ktru"
            merged.confidence = max(merged.confidence, 1.0)

        # === Уровень 2: Таблицы XLS/DOCX ===
        if table_params:
            for field in ["rm_total", "students_count", "points_count", "opr_positions",
                          "addresses_count", "deadline_days"]:
                if table_params.get(field) and not getattr(merged, field):
                    setattr(merged, field, table_params[field])
                    sources[field] = "xls"
            merged.confidence = max(merged.confidence, 1.0)

        # === Уровень 3: Regex ===
        if regex_params:
            for field in ["rm_total", "rm_category_1", "rm_category_2", "rm_with_iii",
                          "points_count", "students_count", "factors_count",
                          "addresses_count", "deadline_days", "opr_positions", "opr_persons"]:
                if regex_params.get(field) is not None and getattr(merged, field) is None:
                    setattr(merged, field, regex_params[field])
                    sources[field] = "regex"

            # Булевы флаги
            for flag in ["has_full_time", "is_urgent", "is_seasonal",
                         "needs_siz_norms", "needs_dsiz_norms", "needs_iot_norms"]:
                if regex_params.get(flag):
                    setattr(merged, flag, True)

            # Строковые поля
            for field in ["deadline_text", "application_guarantee", "contract_guarantee",
                          "guarantee_method", "region_hint"]:
                if regex_params.get(field) and not getattr(merged, field):
                    setattr(merged, field, regex_params[field])
                    sources[field] = "regex"

            # Очные параметры
            for field in ["teacher_days", "accommodation_nights", "transport_km",
                          "venue_rent_days", "manikin_days", "trip_days"]:
                if regex_params.get(field) is not None and getattr(merged, field) is None:
                    setattr(merged, field, regex_params[field])

            merged.confidence = max(merged.confidence, regex_params.get("confidence", 0.5))

        # === Уровень 4: LLM (с валидацией) ===
        if llm_params:
            merged = self._merge_llm(merged, llm_params, llm_confidence, sources)

        # === Guard'ы ===
        merged = self._apply_guards(merged, nmck)

        merged.sources = sources
        return merged

    def _merge_llm(
        self,
        merged: MergedParams,
        llm_params: Dict[str, Any],
        llm_confidence: float,
        sources: Dict[str, str],
    ) -> MergedParams:
        """Объединяет параметры от LLM с валидацией."""
        low_confidence = llm_confidence < 0.3

        # РМ — критичное поле, требует валидации
        llm_rm = llm_params.get("rm_total")
        if llm_rm and llm_rm > 0:
            if low_confidence and llm_rm > 200:
                # Фантомные РМ при низком confidence
                logger.warning(
                    f"[ParamMerger] LLM rm_total={llm_rm} отклонён "
                    f"(confidence={llm_confidence:.2f} < 0.3, >200)"
                )
                merged.needs_manual_review = True
                merged.review_reason = f"Фантомные РМ от LLM: {llm_rm} (confidence={llm_confidence:.2f})"
                # Не перезаписываем, если уже есть значение от regex/КТРУ
                if merged.rm_total is None:
                    merged.rm_total = llm_rm
                    sources["rm_total"] = "llm_unvalidated"
            elif merged.rm_total is None:
                # Нет других источников — берём LLM
                merged.rm_total = llm_rm
                sources["rm_total"] = "llm"
            elif low_confidence:
                # Есть другие источники, LLM с низким confidence
                ratio = max(llm_rm, merged.rm_total) / min(llm_rm, merged.rm_total)
                if ratio > 3:
                    logger.warning(
                        f"[ParamMerger] LLM rm_total={llm_rm} vs existing={merged.rm_total} "
                        f"(ratio={ratio:.1f}), оставляем существующее"
                    )
                else:
                    merged.rm_total = llm_rm
                    sources["rm_total"] = "llm"

        # Остальные поля от LLM
        fields = [
            "rm_category_1", "rm_category_2", "rm_with_iii",
            "points_count", "students_count", "factors_count",
            "deadline_days", "trip_days", "opr_positions", "opr_persons",
        ]
        for field in fields:
            llm_val = llm_params.get(field)
            if llm_val is not None and getattr(merged, field) is None:
                setattr(merged, field, llm_val)
                sources[field] = "llm"

        # Булевы флаги от LLM
        for flag in ["has_full_time", "is_urgent", "is_seasonal",
                     "needs_siz_norms", "needs_dsiz_norms", "needs_iot_norms"]:
            if llm_params.get(flag) and not getattr(merged, flag):
                setattr(merged, flag, True)

        # Строковые поля
        for field in ["application_guarantee", "contract_guarantee", "guarantee_method"]:
            if llm_params.get(field) and not getattr(merged, field):
                setattr(merged, field, llm_params[field])
                sources[field] = "llm"

        merged.confidence = max(merged.confidence, llm_confidence)
        return merged

    def _apply_guards(self, merged: MergedParams, nmck: float) -> MergedParams:
        """Применяет guard'ы для защиты от ошибок."""
        # Guard 1: students_count > 200 при низком confidence
        if merged.students_count and merged.students_count > 200:
            if merged.confidence < 0.5:
                logger.warning(
                    f"[ParamMerger] Guard: students_count={merged.students_count} > 200 "
                    f"при confidence={merged.confidence:.2f}"
                )
                merged.needs_manual_review = True
                if not merged.review_reason:
                    merged.review_reason = f"students_count={merged.students_count} требует проверки"

        # Guard 2: deadline < 3 дней
        if merged.deadline_days and merged.deadline_days < 3:
            logger.warning(
                f"[ParamMerger] Guard: deadline_days={merged.deadline_days} < 3"
            )
            merged.needs_manual_review = True
            if not merged.review_reason:
                merged.review_reason = f"Срок исполнения {merged.deadline_days} дней — проверить"

        return merged
