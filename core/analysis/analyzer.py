"""
core/analysis/analyzer.py
Фасад для анализа тендеров.

ИСПРАВЛЕНО (v6.9.0):
- Добавлен расчёт глобальных затрат (ЭТП, обеспечение, специалист, срочность)
- _build_comment расширен: ЭТП, обеспечение, специалист, срочность
- ЭТП определяется по platform_name с fallback mapping

ИСПРАВЛЕНО (v6.9.1):
- Добавлен вызов apply_global_limits() после расчёта
- Guard'ы: min_contract_sum, min_margin_percent, max_cost_to_nmck_ratio
- Violations добавляются в comment и review_reason

ИСПРАВЛЕНО (v6.9.2):
- FIX: _apply_extra_costs() теперь правильно добавляет global_costs к base cost_price
  (раньше: result.cost_price = extra["total_extra"] → теперь: +=)
- FIX: Guard 4 фантомных students_count теперь проверяет source, не только confidence
- FIX: min_margin_percent guard использует round(margin, 2) >= min_margin
- FIX: margin > 200% не срабатывает при limit_applied (цена поднята лимитом)
- FIX: cost/НМЦК guard: порог 0.85 → 0.90 для тендеров > 100K
"""

import json
import re
from typing import Optional, Dict, Any, List
from loguru import logger

from core.calculation.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer
from core.tender_type import TenderTypeDetector
from utils.llm_client import YandexGPTClient
from core.calculation.calculation_result import CalculationResult
from core.analysis.result import AnalysisResult
from core.analysis.type_resolver import TypeResolver
from core.analysis.guard_engine import GuardEngine
from core.analysis.calculator_router import CalculatorRouter


class TenderAnalyzer:
    """Фасад для анализа тендеров."""

    VERSION = "v6.9.2"

    # Mapping ЭТП → комиссия (%)
    ETP_COMMISSION_RATES = {
        "ртс-тендер": 1.0,
        "ртс": 1.0,
        "сбербанк-аст": 0.5,
        "аст": 0.5,
        "фабрикант": 1.5,
        "еэтп": 1.0,
        "электронная площадка": 1.0,
        "этп гпб": 1.0,
        "агора": 0.8,
    }

    def __init__(
        self,
        calculator: TenderCalculator,
        risk_analyzer: RiskAnalyzer,
        type_detector: TenderTypeDetector,
        llm_client: Optional[YandexGPTClient] = None,
    ):
        self.calculator = calculator
        self.risk_analyzer = risk_analyzer
        self.type_resolver = TypeResolver()
        self.guard_engine = GuardEngine()
        self.calculator_router = CalculatorRouter(calculator)
        self.llm_client = llm_client or YandexGPTClient()
        logger.info(f"TenderAnalyzer инициализирован ({self.VERSION})")

    def analyze(
        self,
        tender_info: Dict[str, Any],
        documents_text: str = "",
        llm_classification: Optional[str] = None,
        llm_confidence: float = 0.0,
        tender_type_hint: Optional[str] = None,
    ) -> AnalysisResult:
        """Анализирует тендер полностью."""
        logger.info(f"[{self.VERSION}] Начинаю анализ тендера")

        # Шаг 1: Определение типа
        tender_type, type_source, method = self.type_resolver.resolve(
            tender_info=tender_info,
            documents_text=documents_text,
            llm_classification=llm_classification,
            llm_confidence=llm_confidence,
            tender_type_hint=tender_type_hint,
        )

        # Шаг 2: Guard'ы
        tender_info, guards = self.guard_engine.apply(tender_info, tender_type)

        # Шаг 3: Извлечение параметров через LLM
        self._extract_if_needed(tender_info, documents_text, tender_type)

        # Шаг 3.5: Глобальные затраты (v6.9.0)
        nmck = tender_info.get("nmck", 0)
        deadline_days = tender_info.get("deadline_days", 30)

        # ЭТП комиссия
        etp_commission = self._resolve_etp_commission(tender_info)

        # Обеспечение
        app_guarantee = self._parse_guarantee_percent(
            tender_info.get("application_guarantee", "")
        )
        contract_guarantee = self._parse_guarantee_percent(
            tender_info.get("contract_guarantee", "")
        )

        extra_costs = self.calculator.apply_global_costs(
            nmck=nmck,
            etp_commission_percent=etp_commission,
            application_guarantee_percent=app_guarantee,
            contract_guarantee_percent=contract_guarantee,
            deadline_days=deadline_days,
        )

        # Шаг 4: Расчёт БАЗОВОЙ себестоимости (education/sout/opr/plk)
        base_result = self.calculator_router.calculate(
            tender_info, tender_type, documents_text
        )

        # v6.9.2 FIX: Применяем глобальные затраты К базовой себестоимости, не ЗАМЕНЯЕМ
        result = self._apply_extra_costs(base_result, extra_costs)

        # === ШАГ 4.5: ГЛОБАЛЬНЫЕ ЛИМИТЫ / GUARD'Ы (v6.9.1) ===
        limits_result = self.calculator.apply_global_limits(
            cost_price=result.cost_price,
            recommended_price=result.recommended_price,
            nmck=nmck,
            tender_type=tender_type,
        )

        # Если лимиты нарушены — корректируем результат
        if not limits_result["is_valid"]:
            logger.warning(
                f"[{self.VERSION}] GUARD: обнаружены нарушения лимитов: "
                f"{limits_result['violations']}"
            )
            old_price = result.recommended_price
            result = CalculationResult(
                cost_price=result.cost_price,
                recommended_price=limits_result["adjusted_price"],
                margin_percent=limits_result["adjusted_margin_percent"],
                margin_rub=limits_result["adjusted_price"] - result.cost_price,
                transport_cost=result.transport_cost,
                subcontractor_cost=result.subcontractor_cost,
                guarantee_cost=result.guarantee_cost,
                needs_manual_review=result.needs_manual_review
                or limits_result["needs_manual_review"],
                review_reason=self._merge_review_reasons(
                    result.review_reason,
                    limits_result["review_reason"],
                    limits_result["violations"],
                ),
                details={
                    **(result.details or {}),
                    "global_limits_violations": limits_result["violations"],
                    "original_recommended_price": old_price,
                    "limit_applied": True,  # v6.9.2: флаг для risk_rules
                },
            )

        # Шаг 5: Анализ рисков
        deadline_days = tender_info.get("deadline_days")
        if deadline_days is None or (
            isinstance(deadline_days, (int, float)) and deadline_days <= 0
        ):
            deadline_days = 30
            logger.info(
                f"[{self.VERSION}] deadline_days не указан, используем дефолт 30"
            )

        # v6.9.2: Передаём limit_applied в risk_analyzer
        risk_result = self.risk_analyzer.analyze(
            tender_type=tender_type,
            nmck=nmck,
            cost_price=result.cost_price,
            margin_percent=result.margin_percent,
            deadline_days=deadline_days,
            region=tender_info.get("region", ""),
            needs_manual_review=result.needs_manual_review,
            limit_applied=result.details.get("limit_applied", False),
            cost_to_nmck_ratio=limits_result.get("cost_to_nmck_ratio", 0),
        )

        # Если лимиты дали HIGH — не понижаем риск
        limits_risk = limits_result.get("risk_level", "low")
        if limits_risk == "high" and risk_result.get("risk_level") != "high":
            risk_result["risk_level"] = "high"
            risk_result["flags"] = risk_result.get("flags", []) + [
                f"Себестоимость составляет >{self.calculator.costs.get('global_limits', {}).get('max_cost_to_nmck_ratio', 0.85)*100:.0f}% от НМЦК — высокий риск убыточности"
            ]

        # Шаг 6: Комментарий
        comment = self._build_comment(
            tender_type=tender_type,
            result=result,
            type_source=type_source,
            method=method,
            guards=guards,
            extra_costs=extra_costs,
            limits_result=limits_result,
        )

        return AnalysisResult(
            tender_type=tender_type,
            cost_price=result.cost_price,
            recommended_price=result.recommended_price,
            margin_percent=result.margin_percent,
            risk_level=risk_result["risk_level"],
            decision=risk_result["decision"],
            needs_manual_review=result.needs_manual_review,
            llm_confidence=llm_confidence,
            details=result.details,
            comment=comment,
            review_reason=result.review_reason,
            type_detection_source=type_source,
            classification_method=method,
            guards_triggered=guards,
            nmck=nmck,
            red_flags=risk_result.get("flags", []),
        )

    # ==================== Утилиты для guard'ов (v6.9.1) ====================

    def _merge_review_reasons(
        self, existing: str, limits_reason: str, violations: List[str]
    ) -> str:
        """Объединяет причины ручной проверки."""
        parts = []
        if existing:
            parts.append(existing)
        if limits_reason:
            parts.append(limits_reason)
        if violations:
            parts.append("Нарушения лимитов: " + "; ".join(violations))
        return " | ".join(parts) if parts else ""

    # ==================== ЭТП и обеспечение ====================

    def _resolve_etp_commission(self, tender_info: Dict[str, Any]) -> float:
        """Определяет комиссию ЭТП."""
        # Явно указана
        etp = tender_info.get("etp_commission_percent", 0)
        if etp > 0:
            return etp

        # Определяем по названию площадки
        platform = tender_info.get("platform_name", "").lower()
        for key, val in self.ETP_COMMISSION_RATES.items():
            if key in platform:
                logger.info(
                    f"[{self.VERSION}] ЭТП определена по площадке '{platform}': {val}%"
                )
                return val

        return 0.0

    def _parse_guarantee_percent(self, guarantee_raw: str) -> float:
        """Парсит процент обеспечения из строки."""
        if not guarantee_raw:
            return 0.0
        text = str(guarantee_raw).lower()
        if "не требуется" in text or "нет" in text:
            return 0.0
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return float(m.group(1))
        return 0.0

    def _apply_extra_costs(
        self, base_result: CalculationResult, extra_costs: dict
    ) -> CalculationResult:
        """Добавляет глобальные затраты к БАЗОВОЙ себестоимости.

        v6.9.2 FIX: Раньше result.cost_price = total_extra (450₽),
        теперь result.cost_price = base_result.cost_price + total_extra.
        """
        total_extra = extra_costs.get("total_extra", 0)

        # v6.9.2: Если base_result пустой (cost_price=0) — значит не удалось рассчитать
        # Не добавляем global_costs к нулевой базе
        if base_result.cost_price <= 0:
            logger.warning(
                f"[{self.VERSION}] Базовая себестоимость = 0, "
                f"global_costs не применяются. Причина: {base_result.review_reason}"
            )
            return base_result

        new_cost = base_result.cost_price + total_extra
        new_recommended = base_result.recommended_price + total_extra

        # Пересчитываем маржу относительно новой себестоимости
        margin_rub = new_recommended - new_cost
        margin_percent = (margin_rub / new_cost * 100) if new_cost > 0 else 0.0

        # Мержим детали
        details = dict(base_result.details) if base_result.details else {}
        details.update(
            {
                "etp_commission": extra_costs.get("etp_commission", 0),
                "application_guarantee": extra_costs.get("application_guarantee", 0),
                "contract_guarantee": extra_costs.get("contract_guarantee", 0),
                "specialist_cost": extra_costs.get("specialist_cost", 0),
                "urgency_multiplier": extra_costs.get("urgency_multiplier", 1.0),
                "urgency_note": extra_costs.get("urgency_note", ""),
                "base_cost_price": base_result.cost_price,  # v6.9.2: для отладки
                "global_costs_total": total_extra,
            }
        )

        return CalculationResult(
            cost_price=new_cost,
            recommended_price=new_recommended,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            transport_cost=base_result.transport_cost,
            subcontractor_cost=base_result.subcontractor_cost,
            guarantee_cost=extra_costs.get("application_guarantee", 0),
            needs_manual_review=base_result.needs_manual_review,
            review_reason=base_result.review_reason,
            details=details,
        )

    # ==================== LLM-извлечение параметров ====================

    def _extract_if_needed(
        self, tender_info: Dict[str, Any], documents_text: str, tender_type: str
    ) -> None:
        """Извлекает параметры через LLM если недостаточно данных."""
        has_params = self._has_sufficient_params(tender_info, tender_type)
        if has_params:
            logger.info(
                f"[{self.VERSION}] Параметров достаточно, LLM-извлечение не требуется"
            )
            return

        prompt = self._build_extraction_prompt(tender_type, documents_text)
        try:
            response = self.llm_client.send(
                system_prompt="Ты — аналитик тендеров. Извлеки параметры из текста и верни JSON.",
                user_message=prompt,
                temperature=0.1,
                max_tokens=2000,
            )
            raw_text_for_debug = str(response)
            logger.debug(f"[LLM RAW] {raw_text_for_debug[:2000]}")

            if isinstance(response, dict) and "raw_text" in response:
                extracted = self._parse_json(response["raw_text"])
            elif isinstance(response, dict):
                extracted = response
            else:
                extracted = self._parse_json(str(response))

            for key, value in extracted.items():
                if value is not None and tender_info.get(key) is None:
                    tender_info[key] = value
                    logger.info(f"[{self.VERSION}] Извлечено: {key}={value}")

        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка LLM-извлечения: {e}")

    def _has_sufficient_params(self, info: Dict[str, Any], tender_type: str) -> bool:
        if tender_type == "sout":
            return bool(info.get("rm_total") and info["rm_total"] > 0)
        elif tender_type == "education":
            return bool(info.get("students_count") and info["students_count"] > 0)
        elif tender_type == "opr":
            has_opr = bool(info.get("opr_positions") and info["opr_positions"] > 0)
            has_rm = bool(info.get("rm_total") and info["rm_total"] > 0)
            has_persons = bool(info.get("opr_persons") and info["opr_persons"] > 0)

            if has_persons and not has_opr:
                info["opr_positions"] = info["opr_persons"]
                has_opr = True
                logger.info(
                    f"[{self.VERSION}] Fallback: opr_persons={info['opr_persons']} → opr_positions"
                )

            return has_opr or has_rm
        elif tender_type == "plk":
            return bool(
                info.get("measurement_points") and info["measurement_points"] > 0
            )
        return False

    def _build_extraction_prompt(self, tender_type: str, documents_text: str) -> str:
        """Строит тип-специфичный промпт для LLM-извлечения."""
        parts = [
            f"Тендер типа: {tender_type}",
            "Извлеки ТОЛЬКО параметры для этого типа:",
        ]

        type_prompts = {
            "sout": (
                "- rm_total: количество рабочих мест (число)\n"
                "- variant: вариант расчёта (1, 2, 3)\n"
                "- addresses_count: количество адресов (число)\n"
                "- has_iii: есть ли вредные факторы 3-4 класса (true/false)"
            ),
            "education": (
                "- students_count: количество слушателей (число)\n"
                "- protocols_count: количество протоколов (число)\n"
                "- qual_certs: удостоверений о повышении квалификации (число)\n"
                "- is_distance: дистанционное обучение (true/false)\n"
                "- teacher_days: дней преподавателя (число)"
            ),
            "opr": (
                "- opr_positions: количество должностей (число)\n"
                "- opr_persons: количество работников (число)"
            ),
            "plk": (
                "- measurement_points: количество точек замера (число)\n"
                "- measurement_types: типы замеров (список)"
            ),
        }

        parts.append(type_prompts.get(tender_type, ""))
        parts.append(
            f"Текст тендера:\n{documents_text[:15000]}\nВерни результат в формате JSON."
        )
        return "\n".join(parts)

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """Парсит JSON из текста LLM-ответа."""
        if not text:
            return {}

        cleaned = text.strip()
        cleaned = re.sub(r"^```\w*\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*$", "", cleaned)
        cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        for pattern in [r"\{[\s\S]*\}", r"\{.*\}"]:
            match = re.search(pattern, cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    continue

        logger.warning(
            f"[{self.VERSION}] Не удалось распарсить JSON, пробуем key-value"
        )
        return {}

    # ==================== Комментарий ====================

    def _build_comment(
        self,
        tender_type: str,
        result,
        type_source: str,
        method: str,
        guards: List[str],
        extra_costs: dict = None,
        limits_result: dict = None,
    ) -> str:
        """Строит детальный комментарий к результату анализа."""
        lines = [
            f"Анализ тендера типа «{tender_type}»",
            "",
            f"Расчётная себестоимость: {result.cost_price:,.0f} ₽",
            f"Рекомендуемая цена: {result.recommended_price:,.0f} ₽",
            f"Маржа: {result.margin_percent:.1f}%",
            "",
            f"Определение типа: {type_source} ({method})",
        ]

        if guards:
            lines.append("")
            lines.append("Сработавшие guard'ы:")
            for guard in guards:
                lines.append(f"  • {guard}")

        # v6.9.0: Глобальные затраты
        if extra_costs:
            lines.append("")
            lines.append("Глобальные затраты:")
            if extra_costs.get("etp_commission", 0) > 0:
                lines.append(
                    f"  • Комиссия ЭТП: {extra_costs['etp_commission']:,.0f} ₽"
                )
            if extra_costs.get("application_guarantee", 0) > 0:
                lines.append(
                    f"  • Обеспечение заявки (БГ): {extra_costs['application_guarantee']:,.0f} ₽"
                )
            if extra_costs.get("contract_guarantee", 0) > 0:
                lines.append(
                    f"  • Обеспечение контракта: {extra_costs['contract_guarantee']:,.0f} ₽"
                )
            if extra_costs.get("specialist_cost", 0) > 0:
                lines.append(
                    f"  • Нагрузка специалиста: {extra_costs['specialist_cost']} ₽"
                )
            if extra_costs.get("urgency_note"):
                lines.append(f"  • {extra_costs['urgency_note']}")

        # v6.9.2: Базовая себестоимость (для отладки)
        if result.details and result.details.get("base_cost_price"):
            lines.append("")
            lines.append(
                f"  • Базовая себестоимость: {result.details['base_cost_price']:,.0f} ₽"
            )
            lines.append(
                f"  • Global costs: {result.details.get('global_costs_total', 0):,.0f} ₽"
            )

        # v6.9.1: Лимиты / guard'ы
        if limits_result and not limits_result.get("is_valid", True):
            lines.append("")
            lines.append("⚠️ Нарушения глобальных лимитов:")
            for v in limits_result.get("violations", []):
                lines.append(f"  • {v}")
            if limits_result.get("original_recommended_price"):
                lines.append(
                    f"  • Цена скорректирована: "
                    f"{limits_result['original_recommended_price']:,.0f} ₽ → "
                    f"{limits_result['adjusted_price']:,.0f} ₽"
                )

        if result.review_reason:
            lines.append("")
            lines.append(f"⚠️ {result.review_reason}")

        return "\n".join(lines)
