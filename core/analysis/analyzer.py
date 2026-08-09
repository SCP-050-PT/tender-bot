"""
core/analysis/analyzer.py
Анализ тендера: классификация + извлечение + расчёт.

ИСПРАВЛЕНО (v6.8):
- Guard'ы cross-type: students_count при СОУТ=0, rm_total при обучении=0
- Разделение: classify() + extract() -> 2 запроса к LLM
- Поддержка tender_type_hint из detailed_parser
- Улучшенное логирование "почему"
- История ошибок для self-learning
"""

import json
import re
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from core.calculation.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.tender_type import TenderTypeDetector
from core.param_extractor import TenderParamExtractor
from utils.llm_client import YandexGPTClient


@dataclass
class AnalysisResult:
    """Результат анализа тендера."""

    tender_type: str
    cost_price: float
    recommended_price: float
    margin_percent: float
    risk_level: str
    decision: str
    needs_manual_review: bool
    llm_confidence: float
    details: Dict[str, Any]
    comment: str = ""
    review_reason: str = ""
    # v6.8: новые поля
    type_detection_source: str = ""  # откуда определён тип
    classification_method: str = ""  # classify / extract / hint / fallback
    guards_triggered: List[str] = None  # какие guard'ы сработали

    def __post_init__(self):
        if self.guards_triggered is None:
            self.guards_triggered = []


class TenderAnalyzer:
    """Анализатор тендеров."""

    VERSION = "v6.8"

    def __init__(
        self,
        calculator: TenderCalculator,
        risk_analyzer: RiskAnalyzer,
        type_detector: TenderTypeDetector,
        llm_client: Optional[YandexGPTClient] = None,
    ):
        self.calculator = calculator
        self.risk_analyzer = risk_analyzer
        self.type_detector = type_detector
        self.param_extractor = TenderParamExtractor()
        self.llm_client = llm_client or YandexGPTClient()
        logger.info(f"TenderAnalyzer инициализирован ({self.VERSION})")

    def analyze(
        self,
        tender_info: Dict[str, Any],
        documents_text: str = "",
        llm_classification: Optional[str] = None,
        llm_confidence: float = 0.0,
        tender_type_hint: Optional[str] = None,  # v6.8: из detailed_parser
    ) -> AnalysisResult:
        """Анализирует тендер."""
        logger.info(f"Начинаю анализ тендера")

        # v6.8: Каскадное определение типа
        tender_type, type_source, classification_method = self._resolve_type(
            tender_info, documents_text, llm_classification, tender_type_hint
        )

        # v6.8: Guard'ы cross-type
        tender_info, guards = self._apply_cross_type_guards(tender_info, tender_type)

        # v6.8: Разделённое извлечение (если нужно)
        extracted = self._extract_params_if_needed(
            tender_info, documents_text, tender_type
        )

        # Расчёт
        result = self._calculate(tender_info, tender_type, documents_text)

        # Анализ рисков
        risk_result = self.risk_analyzer.analyze(
            tender_type=tender_type,
            nmck=tender_info.get("nmck", 0),
            cost_price=result["cost_price"],
            margin_percent=result["margin_percent"],
            deadline_days=tender_info.get("deadline_days", 30),
            region=tender_info.get("region", ""),
            needs_manual_review=result.get("needs_manual_review", False),
        )

        # v6.8: Формируем комментарий с объяснением
        comment = self._build_comment(
            tender_type, result, type_source, classification_method, guards
        )

        return AnalysisResult(
            tender_type=tender_type,
            cost_price=result["cost_price"],
            recommended_price=result["recommended_price"],
            margin_percent=result["margin_percent"],
            risk_level=risk_result["risk_level"],
            decision=risk_result["decision"],
            needs_manual_review=result.get("needs_manual_review", False),
            llm_confidence=llm_confidence,
            details=result.get("details", {}),
            comment=comment,
            review_reason=result.get("review_reason", ""),
            type_detection_source=type_source,
            classification_method=classification_method,
            guards_triggered=guards,
        )

    # ==================== v6.8: КАСКАДНОЕ ОПРЕДЕЛЕНИЕ ТИПА ====================

    def _resolve_type(
        self,
        tender_info: Dict[str, Any],
        documents_text: str,
        llm_classification: Optional[str],
        tender_type_hint: Optional[str],
    ) -> tuple:
        """
        Каскадное определение типа.
        Возвращает (type, source, method).
        """
        # Шаг 1: hint из detailed_parser (самый надёжный для 223-ФЗ)
        if tender_type_hint:
            logger.info(
                f"[{self.VERSION}] Тип из detailed_parser hint: {tender_type_hint}"
            )
            return tender_type_hint, "detailed_parser_hint", "hint"

        # Шаг 2: LLM классификация (если confidence высокий)
        if llm_classification and tender_info.get("llm_confidence", 0) >= 0.7:
            normalized = self._normalize_type(llm_classification)
            logger.info(f"[{self.VERSION}] Тип из LLM классификации: {normalized}")
            return normalized, "llm_classification", "classify"

        # Шаг 3: КТРУ-данные (для 44-ФЗ)
        if tender_info.get("rm_total") and tender_info.get("rm_total") > 0:
            if (
                tender_info.get("students_count")
                and tender_info.get("students_count") > 0
            ):
                logger.info(f"[{self.VERSION}] Тип из КТРУ: combined")
                return "combined", "ktru", "data"
            logger.info(f"[{self.VERSION}] Тип из КТРУ: sout")
            return "sout", "ktru", "data"

        if tender_info.get("students_count") and tender_info.get("students_count") > 0:
            logger.info(f"[{self.VERSION}] Тип из КТРУ: education")
            return "education", "ktru", "data"

        # Шаг 4: Эвристика по тексту
        text = documents_text.lower()
        if any(
            kw in text for kw in ["обучение", "слушатели", "программа", "удостоверение"]
        ):
            if "охрана труда" in text or "охране труда" in text:
                logger.info(f"[{self.VERSION}] Тип из текста: education (ОТ)")
                return "education", "text_heuristic", "heuristic"

        if any(kw in text for kw in ["специальная оценка", "соут", "вредные факторы"]):
            logger.info(f"[{self.VERSION}] Тип из текста: sout")
            return "sout", "text_heuristic", "heuristic"

        if any(kw in text for kw in ["профессиональный риск", "опр", "оценка рисков"]):
            logger.info(f"[{self.VERSION}] Тип из текста: opr")
            return "opr", "text_heuristic", "heuristic"

        # Шаг 5: Fallback
        logger.warning(f"[{self.VERSION}] Тип не определён, fallback на LLM")
        return "unknown", "fallback", "llm"

    def _normalize_type(self, raw_type: str) -> str:
        """Нормализует тип тендера."""
        type_map = {
            "sout": "sout",
            "соут": "sout",
            "специальная оценка": "sout",
            "education": "education",
            "обучение": "education",
            "opr": "opr",
            "опр": "opr",
            "оценка профессиональных рисков": "opr",
            "plk": "plk",
            "плк": "plk",
            "производственный контроль": "plk",
            "combined": "combined",
            "комбинированный": "combined",
        }
        return type_map.get(raw_type.lower(), raw_type.lower())

    # ==================== v6.8: GUARD'Ы CROSS-TYPE ====================

    def _apply_cross_type_guards(
        self, tender_info: Dict[str, Any], tender_type: str
    ) -> tuple:
        """
        Применяет guard'ы для исправления противоречивых данных.
        Возвращает (tender_info, guards_triggered).
        """
        guards = []
        info = dict(tender_info)  # Копия

        # Guard 1: СОУТ не имеет слушателей
        if tender_type in ("sout", "opr", "plk"):
            if info.get("students_count") and info["students_count"] > 0:
                old = info["students_count"]
                info["students_count"] = 0
                guards.append(f"students_count={old} при типе={tender_type} -> 0")
                logger.warning(
                    f"[{self.VERSION}] GUARD: students_count={old} при {tender_type} -> обнулено"
                )

        # Guard 2: Обучение не имеет РМ
        if tender_type == "education":
            if info.get("rm_total") and info["rm_total"] > 0:
                old = info["rm_total"]
                info["rm_total"] = 0
                guards.append(f"rm_total={old} при типе=education -> 0")
                logger.warning(
                    f"[{self.VERSION}] GUARD: rm_total={old} при education -> обнулено"
                )

        # Guard 3: ОПР с rm_total > 200 -> возможно СОУТ
        if tender_type == "opr":
            rm = info.get("rm_total", 0)
            if rm > 200:
                guards.append(f"opr с rm_total={rm} > 200, возможно СОУТ")
                logger.warning(
                    f"[{self.VERSION}] GUARD: ОПР с {rm} РМ -> проверьте, возможно СОУТ"
                )

        # Guard 4: Фантомные students_count > 500 при низком confidence
        if info.get("students_count") and info["students_count"] > 500:
            confidence = info.get("extraction_confidence", 0)
            if confidence < 0.5:
                old = info["students_count"]
                info["students_count"] = 0
                guards.append(
                    f"students_count={old} при confidence={confidence} -> фантом, обнулено"
                )
                logger.warning(
                    f"[{self.VERSION}] GUARD: Фантомные students_count={old} при confidence={confidence} -> обнулено"
                )

        # Guard 5: ОПР с малой себестоимостью -> не считать маржу аномалией
        # (перенесено в risk_rules.py)

        return info, guards

    # ==================== v6.8: РАЗДЕЛЁННОЕ ИЗВЛЕЧЕНИЕ ====================

    def _extract_params_if_needed(
        self,
        tender_info: Dict[str, Any],
        documents_text: str,
        tender_type: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Если параметров недостаточно — делает LLM-извлечение.
        v6.8: Использует тип-специфичный промпт.
        """
        # Проверяем, есть ли ключевые параметры
        has_key_params = self._has_sufficient_params(tender_info, tender_type)
        if has_key_params:
            logger.info(
                f"[{self.VERSION}] Параметров достаточно, LLM-извлечение не требуется"
            )
            return None

        # v6.8: Тип-специфичное извлечение
        prompt = self._build_extraction_prompt(tender_type, documents_text)
        try:
            response = self.llm_client.extract(prompt)
            # Парсим ответ
            extracted = self._parse_extraction_response(response)
            # Мержим с tender_info
            for key, value in extracted.items():
                if value is not None and tender_info.get(key) is None:
                    tender_info[key] = value
            return extracted
        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка LLM-извлечения: {e}")
            return None

    def _has_sufficient_params(
        self, tender_info: Dict[str, Any], tender_type: str
    ) -> bool:
        """Проверяет, достаточно ли параметров для расчёта."""
        if tender_type == "sout":
            return bool(tender_info.get("rm_total") and tender_info["rm_total"] > 0)
        elif tender_type == "education":
            return bool(
                tender_info.get("students_count") and tender_info["students_count"] > 0
            )
        elif tender_type == "opr":
            return bool(
                tender_info.get("opr_positions") and tender_info["opr_positions"] > 0
            )
        elif tender_type == "plk":
            return bool(
                tender_info.get("measurement_points")
                and tender_info["measurement_points"] > 0
            )
        return False

    def _build_extraction_prompt(self, tender_type: str, documents_text: str) -> str:
        """Строит тип-специфичный промпт для извлечения."""
        base = f"""Тендер типа: {tender_type}

Извлеки ТОЛЬКО параметры для этого типа:
"""
        if tender_type == "sout":
            base += """
- rm_total: количество рабочих мест (число)
- variant: вариант расчёта (1, 2, 3)
- addresses_count: количество адресов (число)
- has_iii: есть ли вредные факторы 3-4 класса (true/false)
"""
        elif tender_type == "education":
            base += """
- students_count: количество слушателей (число)
- protocols_count: количество протоколов (число)
- qual_certs: удостоверений о повышении квалификации (число)
- is_distance: дистанционное обучение (true/false)
- teacher_days: дней преподавателя (число)
"""
        elif tender_type == "opr":
            base += """
- opr_positions: количество должностей (число)
- opr_persons: количество работников (число)
"""
        elif tender_type == "plk":
            base += """
- measurement_points: количество точек замера (число)
- measurement_types: типы замеров (список)
"""

        base += f"""

Текст тендера:
{documents_text[:15000]}

Верни результат в формате JSON.
"""
        return base

    def _parse_extraction_response(self, response: str) -> Dict[str, Any]:
        """Парсит JSON-ответ LLM."""
        try:
            # Ищем JSON в ответе
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {}
        except json.JSONDecodeError:
            logger.warning(f"[{self.VERSION}] Не удалось распарсить JSON извлечения")
            return {}

    # ==================== РАСЧЁТ ====================

    def _calculate(
        self,
        tender_info: Dict[str, Any],
        tender_type: str,
        documents_text: str,
    ) -> Dict[str, Any]:
        """Выполняет расчёт себестоимости."""
        if tender_type == "sout":
            return self._calculate_sout(tender_info)
        elif tender_type == "education":
            return self._calculate_education(tender_info, documents_text)
        elif tender_type == "opr":
            return self._calculate_opr(tender_info)
        elif tender_type == "plk":
            return self._calculate_plk(tender_info)
        elif tender_type == "combined":
            return self._calculate_combined(tender_info)
        else:
            return self._create_manual_review_result("Неизвестный тип тендера")

    def _calculate_sout(self, tender_info: Dict[str, Any]) -> Dict[str, Any]:
        """Расчёт СОУТ."""
        rm_total = tender_info.get("rm_total", 0)
        if not rm_total:
            return self._create_manual_review_result("Не определено количество РМ")

        result = self.calculator.calculate_sout(
            rm_total=rm_total,
            variant=tender_info.get("variant", 1),
            addresses_count=tender_info.get("addresses_count", 1),
            has_iii=tender_info.get("has_iii", False),
            region=tender_info.get("region", ""),
        )
        return result

    def _calculate_education(
        self, tender_info: Dict[str, Any], documents_text: str
    ) -> Dict[str, Any]:
        """Расчёт обучения."""
        students = tender_info.get("students_count", 0)
        if not students:
            return self._create_manual_review_result(
                "Не определено количество слушателей"
            )

        # v6.8: Определяем тип документов
        doc_types = self._detect_education_doc_types(tender_info, documents_text)

        result = self.calculator.calculate_education(
            students_count=students,
            protocols_count=doc_types.get("protocols", 0),
            qual_certs=doc_types.get("qual_certs", 0),
            diplomas=doc_types.get("diplomas", 0),
            is_distance=tender_info.get("is_distance", False),
            teacher_days=tender_info.get("teacher_days", 0),
        )
        return result

    def _detect_education_doc_types(
        self, tender_info: Dict[str, Any], documents_text: str
    ) -> Dict[str, int]:
        """Определяет типы документов для обучения."""
        text_lower = documents_text.lower()
        students = tender_info.get("students_count", 0)

        # v6.8: Guard для ОТ
        if "охрана труда" in text_lower or "обучение по охране труда" in text_lower:
            logger.info(
                f"[{self.VERSION}] Обнаружено обучение ОТ -> protocols={students}"
            )
            return {"protocols": students, "qual_certs": 0, "diplomas": 0}

        # Проверяем явно указанные
        protocols = tender_info.get("protocols_count", 0)
        qual_certs = tender_info.get("qual_certs", 0)
        diplomas = tender_info.get("diplomas", 0)

        if protocols > 0 or qual_certs > 0 or diplomas > 0:
            return {
                "protocols": protocols,
                "qual_certs": qual_certs,
                "diplomas": diplomas,
            }

        # Auto-detect
        if "переподготовка" in text_lower or "повышение квалификации" in text_lower:
            return {"protocols": 0, "qual_certs": students, "diplomas": 0}

        # Default: protocols
        return {"protocols": students, "qual_certs": 0, "diplomas": 0}

    def _calculate_opr(self, tender_info: Dict[str, Any]) -> Dict[str, Any]:
        """Расчёт ОПР."""
        positions = tender_info.get("opr_positions", 0)
        if not positions:
            # v6.8: Fallback на rm_total для ОПР
            if tender_info.get("rm_total"):
                positions = tender_info["rm_total"]
                logger.info(
                    f"[{self.VERSION}] ОПР: используем rm_total={positions} как opr_positions"
                )
            else:
                return self._create_manual_review_result(
                    "Не определено количество должностей ОПР"
                )

        result = self.calculator.calculate_opr(
            positions=positions,
            persons=tender_info.get("opr_persons", positions),
        )
        return result

    def _calculate_plk(self, tender_info: Dict[str, Any]) -> Dict[str, Any]:
        """Расчёт ПЛК."""
        points = tender_info.get("measurement_points", 0)
        if not points:
            return self._create_manual_review_result(
                "Не определено количество точек замера"
            )

        result = self.calculator.calculate_plk(
            points=points,
            measurement_types=tender_info.get("measurement_types", []),
        )
        return result

    def _calculate_combined(self, tender_info: Dict[str, Any]) -> Dict[str, Any]:
        """Расчёт комбинированного тендера."""
        # Расчитываем каждую часть отдельно
        results = []

        if tender_info.get("rm_total"):
            sout_result = self._calculate_sout(tender_info)
            results.append(sout_result)

        if tender_info.get("students_count"):
            edu_result = self._calculate_education(tender_info, "")
            results.append(edu_result)

        if not results:
            return self._create_manual_review_result(
                "Не определены параметры комбинированного тендера"
            )

        # Суммируем
        total_cost = sum(r["cost_price"] for r in results)
        total_recommended = sum(r["recommended_price"] for r in results)

        return {
            "cost_price": total_cost,
            "recommended_price": total_recommended,
            "margin_percent": 10.0,
            "needs_manual_review": True,
            "review_reason": "Комбинированный тендер - требуется ручная проверка",
            "details": {"parts": results},
        }

    def _create_manual_review_result(self, reason: str) -> Dict[str, Any]:
        """Создаёт результат с требованием ручной проверки."""
        return {
            "cost_price": 0.0,
            "recommended_price": 0.0,
            "margin_percent": 0.0,
            "needs_manual_review": True,
            "review_reason": reason,
            "details": {},
        }

    # ==================== v6.8: КОММЕНТАРИЙ ====================

    def _build_comment(
        self,
        tender_type: str,
        result: Dict[str, Any],
        type_source: str,
        classification_method: str,
        guards: List[str],
    ) -> str:
        """Строит детальный комментарий."""
        lines = [
            f"Анализ тендера типа «{tender_type}»",
            "",
            f"Расчётная себестоимость: {result['cost_price']:,.0f} ₽",
            f"Рекомендуемая цена: {result['recommended_price']:,.0f} ₽",
            f"Маржа: {result['margin_percent']:.1f}%",
            "",
            f"Определение типа: {type_source} ({classification_method})",
        ]

        if guards:
            lines.append("")
            lines.append("Сработавшие guard'ы:")
            for guard in guards:
                lines.append(f"  • {guard}")

        if result.get("review_reason"):
            lines.append("")
            lines.append(f"⚠️ {result['review_reason']}")

        return "\n".join(lines)
