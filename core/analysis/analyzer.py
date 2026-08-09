"""
core/analysis/analyzer.py
Главный анализатор тендеров.
ИСПРАВЛЕНО (v6.7.3):
  - Убрано дублирование мержа rm_total (теперь только через int_fields)
  - _merge_tender_info: rm_total → opr_positions для ОПР
  - _resolve_quantity: не ставит needs_manual_review при КТРУ (confidence=1.0)
  - Убран мёртвый код _create_manual_review_result
  - Убрано обращение к несуществующему tender_type_hint
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from loguru import logger

from core.calculation.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.tender_type import TenderTypeDetector
from core.param_extractor import TenderParamExtractor
from core.analysis.llm_wrapper import LlmWrapper
from dataclasses import dataclass


@dataclass
class LLMResult:
    tender_type: str = ""
    confidence: float = 0.0
    students_count: int = 0
    rm_total: int = 0
    points_count: int = 0
    variant: int = 1
    protocols_count: int = 0
    certificates: int = 0
    diplomas: int = 0
    worker_certs: int = 0
    qual_certs: int = 0
    is_distance: bool = False
    teacher_days: int = 0
    accommodation_nights: int = 0
    transport_km: int = 0
    venue_rent_days: int = 0
    manikin_days: int = 0
    opr_positions: int = 0
    opr_persons: int = 0
    deadline_days: int = 0
    addresses_count: int = 1
    cities_count: int = 1
    regions_count: int = 1
    trip_days: int = 3
    is_seasonal: bool = False
    needs_siz_norms: bool = False
    needs_dsiz_norms: bool = False
    needs_iot_norms: bool = False
    needs_subcontractor: bool = False
    notes: str = ""


@dataclass
class AnalysisResult:
    tender_type: str
    cost_price: float
    recommended_price: float
    margin_percent: float
    margin_rub: float
    decision: str
    risk_level: str
    flags: list
    comment: str
    needs_manual_review: bool
    review_reason: str
    llm_confidence: float
    details: dict


class TenderAnalyzer:
    """Главный анализатор тендеров."""

    def __init__(self):
        self.calculator = TenderCalculator()
        self.risk_analyzer = RiskAnalyzer()
        self.type_detector = TenderTypeDetector()
        self.param_extractor = TenderParamExtractor()
        self.llm = LlmWrapper()
        logger.info("TenderAnalyzer инициализирован (v6.7.3)")

    def analyze(
        self,
        nmck: float,
        region: str,
        tender_text: str,
        tender_info: dict = None,
        details: dict = None,
    ) -> AnalysisResult:
        """
        Полный анализ тендера.
        v6.7.3: Исправлены баги с ОПР и needs_manual_review.
        """
        tender_info = tender_info or {}
        details = details or {}

        logger.info(f"Начинаю анализ тендера")

        # === Извлечение параметров из текста ===
        extracted = self.param_extractor.extract(tender_text)
        logger.info(f"TextExtractor: confidence={extracted.confidence:.2f}")

        # === LLM: классификация и извлечение ===
        llm_raw = self.llm.analyze_tender(
            tender_text=tender_text,
            tender_info=tender_info,
            extracted_params=extracted,
            classification=None,
        )

        # Конвертируем dict → LLMResult (dataclass)
        if llm_raw is None:
            llm_result = LLMResult()
            logger.warning("LLM вернул None, используем пустой результат")
        else:
            # Берём только поля, которые есть в LLMResult
            llm_fields = {f.name for f in LLMResult.__dataclass_fields__.values()}
            filtered = {k: v for k, v in llm_raw.items() if k in llm_fields}
            llm_result = LLMResult(**filtered)

        logger.info(f"LLM confidence: {llm_result.confidence:.2f}")

        # === Определение типа тендера ===
        tender_type = self._resolve_type(extracted, llm_result, tender_info)
        logger.info(f"[v6.7.3] Нормализованный тип: '{tender_type}'")

        # === Мержинг данных ===
        merged_details = self._merge_tender_info(
            tender_info, details, llm_result, extracted, tender_type
        )

        # === Расчёт количества ===
        quantity, quantity_source = self._resolve_quantity(
            tender_type, merged_details, llm_result, nmck
        )
        logger.info(f"[v6.7.3] Количество: {quantity}, источник: {quantity_source}")

        # === Расчёт себестоимости ===
        calc_result = self._calculate(
            tender_type, quantity, merged_details, nmck, tender_text
        )

        # === Анализ рисков ===
        risk_result = self.risk_analyzer.analyze(
            tender_type=tender_type,
            nmck=nmck,
            cost_price=calc_result.cost_price,
            margin_percent=calc_result.margin_percent,
            deadline_days=merged_details.get("deadline_days"),
            region=region,
            has_forbidden=False,
            needs_manual_review=calc_result.needs_manual_review,
            review_reason=calc_result.review_reason,
            llm_confidence=llm_result.confidence,
            quantity_source=quantity_source,
        )

        # === Формирование результата ===
        return AnalysisResult(
            tender_type=tender_type,
            cost_price=calc_result.cost_price,
            recommended_price=calc_result.recommended_price,
            margin_percent=calc_result.margin_percent,
            margin_rub=calc_result.margin_rub,
            decision=risk_result.decision,
            risk_level=risk_result.risk_level,
            flags=risk_result.flags,
            comment=risk_result.comment,
            needs_manual_review=calc_result.needs_manual_review
            or risk_result.needs_manual_review,
            review_reason=calc_result.review_reason or risk_result.review_reason,
            llm_confidence=llm_result.confidence,
            details={
                **merged_details,
                "quantity": quantity,
                "quantity_source": quantity_source,
                "calculation_details": calc_result.details,
            },
        )

    def _resolve_type(self, extracted, llm_result, tender_info: dict) -> str:
        """Определяет тип тендера с приоритетами."""
        # Приоритет 1: tender_info из парсера (КТРУ/ключевые слова)
        if tender_info.get("tender_type"):
            return tender_info["tender_type"]

        # Приоритет 2: LLM классификация
        if llm_result.tender_type:
            return llm_result.tender_type

        return "unknown"

    def _merge_tender_info(
        self,
        tender_info: dict,
        details: dict,
        llm_result,
        extracted,
        tender_type: str,
    ) -> dict:
        """
        Мержит данные из всех источников.
        v6.7.3-fix: rm_total → opr_positions для ОПР.
        """
        merged = dict(details)

        # v6.7.3: Мержим через int_fields (единый блок, без дублирования)
        int_fields = [
            "rm_total",
            "rm_category_1",
            "rm_category_2",
            "rm_with_iii",
            "students_count",
            "points_count",
            "factors_count",
            "opr_positions",
            "opr_persons",
        ]
        for field in int_fields:
            if tender_info.get(field) is not None and tender_info.get(field) > 0:
                if merged.get(field) is None or merged.get(field) == 0:
                    merged[field] = tender_info[field]
                    logger.info(f"[v6.7.3] {field}={tender_info[field]} из tender_info")

        # v6.7.3-fix: Для ОПР — КТРУ rm_total → opr_positions
        if "опр" in tender_type.lower():
            ktru_rm = tender_info.get("rm_total")
            if ktru_rm and ktru_rm > 0:
                if not merged.get("opr_positions"):
                    merged["opr_positions"] = ktru_rm
                    logger.info(
                        f"[v6.7.3-fix] КТРУ rm_total={ktru_rm} → opr_positions для ОПР"
                    )

        # LLM-данные
        if llm_result.students_count is not None and llm_result.students_count > 0:
            if merged.get("students_count") is None:
                merged["students_count"] = llm_result.students_count
        if llm_result.rm_total is not None and llm_result.rm_total > 0:
            if merged.get("rm_total") is None:
                merged["rm_total"] = llm_result.rm_total

        # Извлечённые данные
        if extracted.students_count is not None and extracted.students_count > 0:
            if merged.get("students_count") is None:
                merged["students_count"] = extracted.students_count
        if extracted.rm_total is not None and extracted.rm_total > 0:
            if merged.get("rm_total") is None:
                merged["rm_total"] = extracted.rm_total

        # Регионы/города
        if tender_info.get("regions_count"):
            merged["regions_count"] = tender_info["regions_count"]
        if tender_info.get("cities_count"):
            merged["cities_count"] = tender_info["cities_count"]
        if tender_info.get("addresses_count"):
            merged["addresses_count"] = tender_info["addresses_count"]

        # Сроки
        if tender_info.get("deadline_days"):
            merged["deadline_days"] = tender_info["deadline_days"]
        if tender_info.get("trip_days"):
            merged["trip_days"] = tender_info["trip_days"]

        return merged

    def _resolve_quantity(
        self,
        tender_type: str,
        details: dict,
        llm_result,
        nmck: float,
    ) -> tuple:
        """
        Определяет количество для расчёта.
        v6.7.3: Не ставит needs_manual_review при КТРУ (confidence=1.0).
        """
        quantity = 0
        source = ""

        if "обучение" in tender_type.lower() or "education" in tender_type.lower():
            quantity = details.get("students_count", 0)
            if quantity > 0:
                source = (
                    "ktru"
                    if details.get("students_count_source") == "ktru"
                    else "extracted"
                )
            elif llm_result.students_count:
                quantity = llm_result.students_count
                source = "llm"

        elif "соут" in tender_type.lower() or "sout" in tender_type.lower():
            quantity = details.get("rm_total", 0)
            if quantity > 0:
                source = "ktru"
            elif llm_result.rm_total:
                quantity = llm_result.rm_total
                source = "llm"

        elif "опр" in tender_type.lower() or "opr" in tender_type.lower():
            # v6.7.3-fix: Для ОПР берём opr_positions (из КТРУ rm_total)
            quantity = details.get("opr_positions", 0)
            if quantity > 0:
                source = "ktru"
            elif details.get("rm_total", 0) > 0:
                quantity = details["rm_total"]
                source = "ktru_fallback"
            elif llm_result.rm_total:
                quantity = llm_result.rm_total
                source = "llm"

        elif "плк" in tender_type.lower() or "plk" in tender_type.lower():
            quantity = details.get("points_count", 0)
            if quantity > 0:
                source = "extracted"
            elif llm_result.points_count:
                quantity = llm_result.points_count
                source = "llm"

        # Если количество не определено — оценка по НМЦК
        if quantity == 0 and nmck > 0:
            quantity = self._estimate_from_nmck(tender_type, nmck)
            source = "nmck_estimate"

        return quantity, source

    def _estimate_from_nmck(self, tender_type: str, nmck: float) -> int:
        """Оценка количества по НМЦК (fallback)."""
        if "обучение" in tender_type.lower():
            return max(1, int(nmck / 2500))
        elif "соут" in tender_type.lower():
            return max(1, int(nmck / 8000))
        elif "опр" in tender_type.lower():
            return max(1, int(nmck / 1200))
        elif "плк" in tender_type.lower():
            return max(1, int(nmck / 500))
        return 1

    def _calculate(
        self,
        tender_type: str,
        quantity: int,
        details: dict,
        nmck: float,
        tender_text: str,
    ):
        """Расчёт себестоимости по типу тендера."""
        from core.calculation.calculation_result import CalculationResult

        if "обучение" in tender_type.lower() or "education" in tender_type.lower():
            return self._calculate_education(quantity, details, nmck, tender_text)
        elif "соут" in tender_type.lower() or "sout" in tender_type.lower():
            return self._calculate_sout(quantity, details)
        elif "опр" in tender_type.lower() or "opr" in tender_type.lower():
            return self._calculate_opr(quantity, details)
        elif "плк" in tender_type.lower() or "plk" in tender_type.lower():
            return self._calculate_plk(quantity, details)
        else:
            # Fallback: оценка по НМЦК
            cost_price = nmck * 0.7
            return CalculationResult(
                cost_price=cost_price,
                recommended_price=nmck * 0.85,
                margin_percent=10.0,
                margin_rub=nmck * 0.15,
                transport_cost=0,
                subcontractor_cost=0,
                needs_manual_review=True,
                review_reason="Неизвестный тип тендера — требуется ручная проверка",
            )

    def _calculate_education(
        self, students_count: int, details: dict, nmck: float, tender_text: str
    ):
        """Расчёт обучения."""
        is_distance = details.get("is_distance", False)
        teacher_days = details.get("teacher_days", 0)
        teacher_rate = details.get("teacher_rate", 0)
        transport_km = details.get("transport_km", 0)
        accommodation_nights = details.get("accommodation_nights", 0)
        manikin_days = details.get("manikin_days", 0)
        venue_days = details.get("venue_days", 0)
        delivery_count = details.get("delivery_count", 1)
        llm_confidence = details.get("llm_confidence", 0.0)

        return self.calculator.calculate_education(
            students_count=students_count,
            is_distance=is_distance,
            teacher_days=teacher_days,
            teacher_rate=teacher_rate,
            transport_km=transport_km,
            accommodation_nights=accommodation_nights,
            manikin_days=manikin_days,
            venue_days=venue_days,
            delivery_count=delivery_count,
            llm_confidence=llm_confidence,
            tender_text=tender_text,
        )

    def _calculate_sout(self, rm_total: int, details: dict):
        """Расчёт СОУТ."""
        rm_category_1 = details.get("rm_category_1", 0)
        rm_category_2 = details.get("rm_category_2", 0)
        rm_with_iii = details.get("rm_with_iii", 0)
        variant = details.get("variant", 1)
        delivery_count = details.get("delivery_count", 1)
        trip_days = details.get("trip_days", 3)
        regions_count = details.get("regions_count", 1)
        cities_count = details.get("cities_count", 1)
        transport_cost = details.get("transport_cost", 0)
        is_seasonal = details.get("is_seasonal", False)

        return self.calculator.calculate_sout(
            rm_total=rm_total,
            rm_category_1=rm_category_1,
            rm_category_2=rm_category_2,
            rm_with_iii=rm_with_iii,
            variant=variant,
            delivery_count=delivery_count,
            trip_days=trip_days,
            regions_count=regions_count,
            cities_count=cities_count,
            transport_cost=transport_cost,
            is_seasonal=is_seasonal,
        )

    def _calculate_opr(self, rm_count: int, details: dict):
        """Расчёт ОПР."""
        logger.info(f"[v6.7.3] calculate_opr: rm_count={rm_count}")
        delivery_count = details.get("delivery_count", 1)
        needs_siz_norms = details.get("needs_siz_norms", False)
        needs_dsiz_norms = details.get("needs_dsiz_norms", False)
        needs_iot_norms = details.get("needs_iot_norms", False)
        transport_cost = details.get("transport_cost", 0)

        return self.calculator.calculate_opr(
            rm_count=rm_count,
            delivery_count=delivery_count,
            needs_siz_norms=needs_siz_norms,
            needs_dsiz_norms=needs_dsiz_norms,
            needs_iot_norms=needs_iot_norms,
            transport_cost=transport_cost,
        )

    def _calculate_plk(self, points_count: int, details: dict):
        """Расчёт ПЛК."""
        factors_count = details.get("factors_count", 0)
        delivery_count = details.get("delivery_count", 1)
        needs_subcontractor = details.get("needs_subcontractor", False)
        transport_cost = details.get("transport_cost", 0)

        return self.calculator.calculate_plk(
            points_count=points_count,
            factors_count=factors_count,
            delivery_count=delivery_count,
            needs_subcontractor=needs_subcontractor,
            transport_cost=transport_cost,
        )
