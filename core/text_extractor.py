"""
core/text_extractor.py
Предварительный NLP-парсинг текста тендерных документов.
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - РЕФАКТОРИНГ: Вся логика извлечения перенесена в param_extractor.py
  - Вся логика определения типа перенесена в tender_type.py
  - Этот файл — тонкий wrapper для обратной совместимости
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from loguru import logger

# ← v6.3: Импорт новых модулей
from core.param_extractor import (
    TenderParamExtractor,
    ExtractedParams as NewExtractedParams,
)
from core.tender_type import TenderTypeDetector, TypeDetectionResult


@dataclass
class ExtractedParams:
    """Результат извлечения параметров из текста. (LEGACY — для обратной совместимости)"""

    rm_total: Optional[int] = None
    rm_category_1: Optional[int] = None
    rm_category_2: Optional[int] = None
    rm_with_iii: Optional[int] = None
    points_count: Optional[int] = None
    students_count: Optional[int] = None
    factors_count: Optional[int] = None
    addresses_count: Optional[int] = None

    deadline_days: Optional[int] = None
    deadline_text: Optional[str] = None

    application_guarantee: Optional[str] = None
    contract_guarantee: Optional[str] = None
    guarantee_method: Optional[str] = None

    tender_type_hint: Optional[str] = None
    region_hint: Optional[str] = None

    has_full_time: bool = False
    has_polygon: bool = False
    is_urgent: bool = False
    urgency_days: Optional[int] = None

    needs_siz_norms: bool = False
    needs_dsiz_norms: bool = False
    needs_iot_norms: bool = False

    confidence: float = 0.0
    raw_matches: List[Dict] = field(default_factory=list)

    rm_total_source: str = ""
    points_count_source: str = ""
    students_count_source: str = ""

    trip_days: Optional[int] = None

    opr_positions: Optional[int] = None
    opr_persons: Optional[int] = None
    is_seasonal: bool = False


class TenderTextExtractor:
    """
    Извлекает параметры из текста тендерных документов.
    v6.3: Тонкий wrapper над TenderParamExtractor + TenderTypeDetector.
    """

    def __init__(self):
        self._param_extractor = TenderParamExtractor()
        self._type_detector = TenderTypeDetector()
        logger.info("TenderTextExtractor инициализирован (v6.3 wrapper)")

    def extract(
        self, text: str, nmck: float = 0, tender_type_hint: str = None
    ) -> ExtractedParams:
        """Извлекает все параметры из текста (LEGACY API)."""
        # Используем новый экстрактор
        new_params = self._param_extractor.extract(
            text=text, nmck=nmck, tender_type_hint=tender_type_hint
        )

        # Конвертируем в legacy формат
        legacy = ExtractedParams()

        # Копируем все поля
        for attr in [
            "rm_total",
            "rm_category_1",
            "rm_category_2",
            "rm_with_iii",
            "points_count",
            "students_count",
            "factors_count",
            "addresses_count",
            "deadline_days",
            "deadline_text",
            "application_guarantee",
            "contract_guarantee",
            "guarantee_method",
            "has_full_time",
            "has_polygon",
            "is_urgent",
            "urgency_days",
            "needs_siz_norms",
            "needs_dsiz_norms",
            "needs_iot_norms",
            "confidence",
            "trip_days",
            "opr_positions",
            "opr_persons",
            "is_seasonal",
        ]:
            setattr(legacy, attr, getattr(new_params, attr, None))

        legacy.region_hint = new_params.region_hint
        legacy.rm_total_source = new_params.rm_total_source
        legacy.points_count_source = new_params.points_count_source
        legacy.students_count_source = new_params.students_count_source

        # Определяем тип через новый детектор
        type_result = self._type_detector.detect(text)
        legacy.tender_type_hint = type_result.tender_type

        return legacy

    def build_enriched_prompt(
        self,
        params: ExtractedParams,
        original_text: str,
        nmck: float = 0,
        tender_type_hint: str = None,
    ) -> str:
        """Строит обогащённый промпт для LLM."""
        # Конвертируем legacy → new для использования нового метода
        new_params = self._legacy_to_new(params)
        return self._param_extractor.build_enriched_prompt(
            new_params, original_text, nmck, tender_type_hint
        )

    def merge_with_llm_result(
        self, extracted: ExtractedParams, llm_result: dict, llm_confidence: float = 0.0
    ) -> dict:
        """Объединяет извлечённые параметры с результатом LLM."""
        new_params = self._legacy_to_new(extracted)
        return self._param_extractor.merge_with_llm_result(
            new_params, llm_result, llm_confidence
        )

    def _legacy_to_new(self, legacy: ExtractedParams) -> NewExtractedParams:
        """Конвертирует legacy ExtractedParams в новый формат."""
        new = NewExtractedParams()
        for attr in dir(new):
            if not attr.startswith("_") and hasattr(legacy, attr):
                setattr(new, attr, getattr(legacy, attr))
        return new