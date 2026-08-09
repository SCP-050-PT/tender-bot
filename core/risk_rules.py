"""
core/risk_rules.py
Анализ рисков тендера.

ИСПРАВЛЕНО (v6.8):
- Guard: ОПР с малой себестоимостью -> не считать маржу аномалией
- Guard: цена > НМЦК -> HIGH риск
- Guard: маржа > 200% -> HIGH (с исключением для ОПР)
- Улучшенное логирование
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from loguru import logger


@dataclass
class RiskResult:
    """Результат анализа рисков."""

    risk_level: str  # low, medium, high
    decision: str  # рекомендуется, не рекомендуется, осторожно
    flags: list  # список флагов рисков
    needs_manual_review: bool
    review_reason: str = ""


class RiskAnalyzer:
    """Анализатор рисков тендеров."""

    VERSION = "v6.8"

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.thresholds = {
            "min_margin_percent": 5.0,
            "max_margin_percent": 200.0,
            "min_deadline_days": 3,
            "max_nmck_ratio": 1.5,  # рекомендуемая цена не более 150% НМЦК
            "opr_cost_threshold": 50000.0,  # v6.8: порог для ОПР
        }
        logger.info(f"RiskAnalyzer инициализирован ({self.VERSION})")

    def analyze(
        self,
        tender_type: str,
        nmck: float,
        cost_price: float,
        margin_percent: float,
        deadline_days: int = 30,
        region: str = "",
        needs_manual_review: bool = False,
    ) -> Dict[str, Any]:
        """Анализирует риски тендера."""
        flags = []
        risk_level = "low"
        decision = "рекомендуется"

        # v6.8: Guard - цена не должна превышать НМЦК
        recommended_price = cost_price * (1 + margin_percent / 100)
        if recommended_price > nmck:
            flags.append(
                f"Рекомендуемая цена ({recommended_price:,.0f}₽) превышает НМЦК ({nmck:,.0f}₽)"
            )
            risk_level = "high"
            decision = "не рекомендуется"
            logger.error(f"[{self.VERSION}] GUARD: цена > НМЦК")

        # v6.8: Guard - маржа > 200%
        if margin_percent > self.thresholds["max_margin_percent"]:
            # v6.8: Исключение для ОПР с малой себестоимостью
            if (
                tender_type == "opr"
                and cost_price < self.thresholds["opr_cost_threshold"]
            ):
                logger.info(
                    f"[{self.VERSION}] ОПР с себестоимостью {cost_price:,.0f}₽ - "
                    f"маржа {margin_percent:.1f}% не считаем аномалией"
                )
            else:
                flags.append(f"Аномально высокая маржа: {margin_percent:.1f}%")
                risk_level = "high"
                decision = "не рекомендуется"
                logger.error(
                    f"[{self.VERSION}] GUARD: маржа {margin_percent:.1f}% > {self.thresholds['max_margin_percent']}%"
                )

        # v6.8: Guard - маржа < 5% (убыточно)
        if margin_percent < self.thresholds["min_margin_percent"]:
            flags.append(f"Низкая маржа: {margin_percent:.1f}%")
            if risk_level == "low":
                risk_level = "medium"
            logger.warning(f"[{self.VERSION}] Низкая маржа: {margin_percent:.1f}%")

        # Guard - короткий дедлайн
        if deadline_days < self.thresholds["min_deadline_days"]:
            flags.append(f"Короткий срок: {deadline_days} дней")
            if risk_level == "low":
                risk_level = "medium"
            logger.warning(f"[{self.VERSION}] Короткий срок: {deadline_days} дней")

        # v6.8: Guard - needs_manual_review поднимает риск
        if needs_manual_review:
            flags.append("Требуется ручная проверка")
            if risk_level == "low":
                risk_level = "medium"
            logger.info(
                f"[{self.VERSION}] needs_manual_review -> риск повышен до {risk_level}"
            )

        return {
            "risk_level": risk_level,
            "decision": decision,
            "flags": flags,
            "needs_manual_review": needs_manual_review or len(flags) > 0,
        }
