"""
core/calculation/calculation_result.py
Единый dataclass для результатов расчёта.
Вынесен из отдельных калькуляторов (v6.7.3).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CalculationResult:
    """Результат расчёта себестоимости тендера."""

    cost_price: float
    recommended_price: float
    margin_percent: float
    margin_rub: float
    transport_cost: float
    subcontractor_cost: float
    guarantee_cost: float = 0.0
    needs_manual_review: bool = False
    review_reason: str = ""
    details: Optional[dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "cost_price": round(self.cost_price, 2),
            "recommended_price": round(self.recommended_price, 2),
            "margin_percent": round(self.margin_percent, 2),
            "margin_rub": round(self.margin_rub, 2),
            "transport_cost": round(self.transport_cost, 2),
            "subcontractor_cost": round(self.subcontractor_cost, 2),
            "guarantee_cost": round(self.guarantee_cost, 2),
            "needs_manual_review": self.needs_manual_review,
            "review_reason": self.review_reason,
            "details": self.details,
        }
