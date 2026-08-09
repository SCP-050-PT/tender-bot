"""
core/analysis/analyzer.py
Главный анализатор тендеров.
v6.7.4: Исправлены критические баги с типами данных.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from loguru import logger

from core.calculation.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.tender_type import TenderTypeDetector
from core.param_extractor import TenderParamExtractor
from core.analysis.llm_wrapper import LlmWrapper


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
    nmck: float
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

    def to_dict(self) -> dict:
        """Конвертирует в dict для сериализации."""
        return asdict(self)


class TenderAnalyzer:
    """Главный анализатор тендеров."""

    def __init__(self):
        self.calculator = TenderCalculator()
        self.risk_analyzer = RiskAnalyzer()
        self.type_detector = TenderTypeDetector()
        self.param_extractor = TenderParamExtractor()
        self.llm = LlmWrapper()
        logger.info("TenderAnalyzer инициализирован (v6.7.4)")

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
        v6.7.4: Исправлены баги с типами данных.
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

        # Конвертируем dict → LLMResult
        if llm_raw is None:
            llm_result = LLMResult()
            logger.warning("LLM вернул None, используем пустой результат")
        else:
            llm_fields = {f.name for f in LLMResult.__dataclass_fields__.values()}
            filtered = {k: v for k, v in llm_raw.items() if k in llm_fields}
            llm_result = LLMResult(**filtered)

        logger.info(f"LLM confidence: {llm_result.confidence:.2f}")

        # === Определение типа тендера ===
        tender_type = self._resolve_type(llm_result, tender_info)
        logger.info(f"[v6.7.4] Нормализованный тип: '{tender_type}'")

        # === Мержинг данных ===
        merged_details = self._merge_tender_info(
            tender_info, details, llm_result, extracted, tender_type
        )

        # === Расчёт количества ===
        quantity, quantity_source = self._resolve_quantity(
            tender_type, merged_details, llm_result, nmck
        )
        logger.info(f"[v6.7.4] Количество: {quantity}, источник: {quantity_source}")

        # === Расчёт себестоимости ===
        calc_result = self._calculate(
            tender_type, quantity, merged_details, nmck, tender_text, llm_result
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
            nmck=nmck,
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

    def _resolve_type(self, llm_result: LLMResult, tender_info: dict) -> str:
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
        llm_result: LLMResult,
        extracted,
        tender_type: str,
    ) -> dict:
        """
        Мержит данные из всех источников.
        v6.7.4: Убрано дублирование, добавлен llm_confidence.
        """
        merged = dict(details)

        # Мержим числовые поля из tender_info (КТРУ/парсер)
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
            val = tender_info.get(field)
            if val is not None and val > 0:
                if merged.get(field) is None or merged.get(field) == 0:
                    merged[field] = val
                    logger.info(f"[v6.7.4] {field}={val} из tender_info")

        # Для ОПР — КТРУ rm_total → opr_positions
        if "опр" in tender_type.lower():
            ktru_rm = tender_info.get("rm_total")
            if ktru_rm and ktru_rm > 0:
                if not merged.get("opr_positions"):
                    merged["opr_positions"] = ktru_rm
                    logger.info(
                        f"[v6.7.4-fix] КТРУ rm_total={ktru_rm} → opr_positions для ОПР"
                    )

        # LLM-данные (только если tender_info не дал значение)
        if llm_result.students_count > 0 and not merged.get("students_count"):
            merged["students_count"] = llm_result.students_count
        if llm_result.rm_total > 0 and not merged.get("rm_total"):
            merged["rm_total"] = llm_result.rm_total

        # Извлечённые данные (резерв)
        if hasattr(extracted, "students_count") and extracted.students_count:
            if not merged.get("students_count"):
                merged["students_count"] = extracted.students_count
        if hasattr(extracted, "rm_total") and extracted.rm_total:
            if not merged.get("rm_total"):
                merged["rm_total"] = extracted.rm_total

        # Регионы/города/адреса
        for field in [
            "regions_count",
            "cities_count",
            "addresses_count",
            "deadline_days",
            "trip_days",
        ]:
            if tender_info.get(field):
                merged[field] = tender_info[field]

        # v6.7.4: Прокидываем llm_confidence для calculate_education
        merged["llm_confidence"] = llm_result.confidence

        return merged

    def _resolve_quantity(
        self,
        tender_type: str,
        details: dict,
        llm_result: LLMResult,
        nmck: float,
    ) -> tuple:
        """Определяет количество для расчёта."""
        quantity = 0
        source = ""

        if "обучение" in tender_type.lower() or "education" in tender_type.lower():
            quantity = details.get("students_count", 0)
            source = "ktru" if quantity > 0 else ""
            if not quantity and llm_result.students_count > 0:
                quantity = llm_result.students_count
                source = "llm"

        elif "соут" in tender_type.lower() or "sout" in tender_type.lower():
            quantity = details.get("rm_total", 0)
            source = "ktru" if quantity > 0 else ""
            if not quantity and llm_result.rm_total > 0:
                quantity = llm_result.rm_total
                source = "llm"

        elif "опр" in tender_type.lower() or "opr" in tender_type.lower():
            quantity = details.get("opr_positions", 0)
            source = "ktru" if quantity > 0 else ""
            if not quantity:
                quantity = details.get("rm_total", 0)
                source = "ktru_fallback" if quantity > 0 else ""
            if not quantity and llm_result.rm_total > 0:
                quantity = llm_result.rm_total
                source = "llm"

        elif "плк" in tender_type.lower() or "plk" in tender_type.lower():
            quantity = details.get("points_count", 0)
            source = "extracted" if quantity > 0 else ""
            if not quantity and llm_result.points_count > 0:
                quantity = llm_result.points_count
                source = "llm"

        # Fallback: оценка по НМЦК
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
        llm_result: LLMResult,
    ):
        """Расчёт себестоимости по типу тендера."""
        from core.calculation.calculation_result import CalculationResult

        if "обучение" in tender_type.lower() or "education" in tender_type.lower():
            return self._calculate_education(
                quantity, details, nmck, tender_text, llm_result
            )
        elif "соут" in tender_type.lower() or "sout" in tender_type.lower():
            return self._calculate_sout(quantity, details)
        elif "опр" in tender_type.lower() or "opr" in tender_type.lower():
            return self._calculate_opr(quantity, details)
        elif "плк" in tender_type.lower() or "plk" in tender_type.lower():
            return self._calculate_plk(quantity, details)
        else:
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
        self,
        students_count: int,
        details: dict,
        nmck: float,
        tender_text: str,
        llm_result: LLMResult,
    ):
        """Расчёт обучения. v6.7.4: llm_confidence из llm_result."""
        is_distance = details.get("is_distance", False)
        teacher_days = details.get("teacher_days", 0)
        teacher_rate = details.get("teacher_rate", 0)
        transport_km = details.get("transport_km", 0)
        accommodation_nights = details.get("accommodation_nights", 0)
        manikin_days = details.get("manikin_days", 0)
        venue_days = details.get("venue_days", 0)
        delivery_count = details.get("delivery_count", 1)
        llm_confidence = llm_result.confidence  # Берём из LLMResult напрямую

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
        return self.calculator.calculate_sout(
            rm_total=rm_total,
            rm_category_1=details.get("rm_category_1", 0),
            rm_category_2=details.get("rm_category_2", 0),
            rm_with_iii=details.get("rm_with_iii", 0),
            variant=details.get("variant", 1),
            delivery_count=details.get("delivery_count", 1),
            trip_days=details.get("trip_days", 3),
            regions_count=details.get("regions_count", 1),
            cities_count=details.get("cities_count", 1),
            transport_cost=details.get("transport_cost", 0),
            is_seasonal=details.get("is_seasonal", False),
        )

    def _calculate_opr(self, rm_count: int, details: dict):
        """Расчёт ОПР."""
        logger.info(f"[v6.7.4] calculate_opr: rm_count={rm_count}")
        return self.calculator.calculate_opr(
            rm_count=rm_count,
            delivery_count=details.get("delivery_count", 1),
            needs_siz_norms=details.get("needs_siz_norms", False),
            needs_dsiz_norms=details.get("needs_dsiz_norms", False),
            needs_iot_norms=details.get("needs_iot_norms", False),
            transport_cost=details.get("transport_cost", 0),
        )

    def _calculate_plk(self, points_count: int, details: dict):
        """Расчёт ПЛК."""
        return self.calculator.calculate_plk(
            points_count=points_count,
            factors_count=details.get("factors_count", 0),
            delivery_count=details.get("delivery_count", 1),
            needs_subcontractor=details.get("needs_subcontractor", False),
            transport_cost=details.get("transport_cost", 0),
        )
