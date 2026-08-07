"""
core/calculation/calculator.py
Фасад калькулятора: делегирует расчёты специализированным калькуляторам.
v6.7.1: Исправлен calculate_opr (rm_count из kwargs), calculate_combined (opr_positions).
"""

from typing import Literal
from dataclasses import dataclass
from loguru import logger

from core.calculation.cost_loader import load_costs
from core.calculation.education_calculator import EducationCalculator
from core.calculation.sout_calculator import SoutCalculator
from core.calculation.plk_opr_calculators import PlkCalculator, OprCalculator


@dataclass
class CalculationResult:
    cost_price: float
    recommended_price: float
    margin_percent: float
    margin_rub: float
    transport_cost: float
    subcontractor_cost: float
    guarantee_cost: float = 0.0
    details: dict = None
    needs_manual_review: bool = False  # v6.7.1
    review_reason: str = ""  # v6.7.1

    def to_dict(self) -> dict:
        return {
            "cost_price": round(self.cost_price, 2),
            "recommended_price": round(self.recommended_price, 2),
            "margin_percent": round(self.margin_percent, 2),
            "margin_rub": round(self.margin_rub, 2),
            "transport_cost": round(self.transport_cost, 2),
            "subcontractor_cost": round(self.subcontractor_cost, 2),
            "guarantee_cost": round(self.guarantee_cost, 2),
            "details": self.details,
            "needs_manual_review": self.needs_manual_review,
            "review_reason": self.review_reason,
        }


class TenderCalculator:
    """
    Фасад калькулятора ЦЕНЫ ДЛЯ КЛИЕНТА.
    Делегирует расчёты специализированным калькуляторам.
    """

    def __init__(self):
        self.costs = load_costs()
        self.edu_calc = EducationCalculator()
        self.sout_calc = SoutCalculator()
        self.plk_calc = PlkCalculator()
        self.opr_calc = OprCalculator()
        logger.info("TenderCalculator инициализирован (v6.7.1)")

    # ==================== ОБУЧЕНИЕ ====================

    def calculate_education(self, **kwargs) -> CalculationResult:
        """Расчёт цены для клиента на обучение."""
        return self.edu_calc.calculate(**kwargs)

    # ==================== СОУТ ====================

    def calculate_sout(self, **kwargs) -> CalculationResult:
        """Расчёт цены для клиента на СОУТ."""
        return self.sout_calc.calculate(**kwargs)

    # ==================== COMBINED ====================

    def calculate_combined(
        self,
        rm_total: int,
        rm_category_1: int = 0,
        rm_category_2: int = 0,
        rm_with_iii: int = 0,
        opr_positions: int = 0,
        opr_persons: int = 0,
        variant: Literal[1, 2, 3] = 1,
        delivery_count: int = 1,
        is_annual: bool = False,
        cities_count: int = 1,
        addresses_count: int = 1,
        trip_days: int = 3,
        regions_count: int = 1,
        transport_cost: float = 0,
        is_seasonal: bool = False,
    ) -> CalculationResult:
        """Расчёт цены для клиента на combined (СОУТ + ОПР)."""
        sout_result = self.sout_calc.calculate(
            rm_total=rm_total,
            rm_category_1=rm_category_1,
            rm_category_2=rm_category_2,
            rm_with_iii=rm_with_iii,
            variant=variant,
            delivery_count=delivery_count,
            is_annual=is_annual,
            cities_count=cities_count,
            addresses_count=addresses_count,
            trip_days=trip_days,
            regions_count=regions_count,
            transport_cost=transport_cost,
            is_seasonal=is_seasonal,
        )

        # v6.7.1: ОПР считаем по opr_positions (должности), а не rm_total
        opr_rm_count = opr_positions if opr_positions > 0 else rm_total
        if opr_positions == 0 and rm_total > 0:
            logger.warning(
                f"[v6.7.1] combined: opr_positions=0, используем rm_total={rm_total} как fallback"
            )

        opr_result = self.opr_calc.calculate(
            rm_count=opr_rm_count,
            delivery_count=delivery_count,
            transport_cost=transport_cost,
        )

        combined_cost_price = sout_result.cost_price + opr_result.cost_price
        margin_percent = 10.0
        margin_rub = combined_cost_price * 0.1
        recommended_price = combined_cost_price + margin_rub

        return CalculationResult(
            cost_price=combined_cost_price,
            recommended_price=recommended_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            transport_cost=sout_result.transport_cost,
            subcontractor_cost=sout_result.subcontractor_cost,
            guarantee_cost=0,
            details={
                **sout_result.details,
                "type": "combined",
                "opr_cost_price": opr_result.cost_price,
                "opr_details": opr_result.details,
                "sout_cost_price": sout_result.cost_price,
                "combined_cost_price": combined_cost_price,
                "opr_positions_used": opr_rm_count,
            },
        )

    # ==================== ПЛК ====================

    def calculate_plk(self, **kwargs) -> CalculationResult:
        """Расчёт цены для клиента на ПЛК."""
        return self.plk_calc.calculate(**kwargs)

    # ==================== ОПР ====================

    def calculate_opr(self, **kwargs) -> CalculationResult:
        """Расчёт цены для клиента на ОПР."""
        # v6.7.1: Исправлен баг — rm_count берём из kwargs
        rm_count = kwargs.get("rm_count", 0) or 0
        logger.info(f"[v6.7.1] calculate_opr: rm_count={rm_count}")
        return self.opr_calc.calculate(**kwargs)

    # ==================== ОБЕСПЕЧЕНИЯ ====================

    def calculate_guarantee(
        self,
        contract_sum: float,
        guarantee_type: Literal["application", "contract"] = "contract",
    ) -> float:
        """Расчёт стоимости банковской гарантии."""
        guarantees = self.costs["guarantees"]

        if guarantee_type == "application":
            guarantee_sum = contract_sum * 0.05
        else:
            guarantee_sum = contract_sum * 0.10

        bg_cost = 1000
        for range_info in guarantees["application"]["bank_guarantee_cost"]["ranges"]:
            if contract_sum <= range_info["max_contract"]:
                bg_cost = range_info["real_cost"]
                break

        return bg_cost

    # ==================== ТРАНСПОРТНЫЕ ====================

    def calculate_transport(
        self,
        distance_km: int = 0,
        accommodation_nights: int = 0,
        expert_days: int = 1,
        needs_flight: bool = False,
        flight_cost: float = 0,
    ) -> dict:
        """Расчёт транспортных расходов."""
        travel = self.costs["travel"]

        fuel_liters = (distance_km / 100) * travel["fuel"]["consumption_l_per_100km"]
        fuel_cost = fuel_liters * travel["fuel"]["price_per_liter"]

        if needs_flight and flight_cost > 0:
            fuel_cost = flight_cost * 2

        accommodation_cost = (
            accommodation_nights * travel["accommodation"]["standard_per_night"]
        )
        daily_allowance = expert_days * travel["daily_allowance"]["standard"]

        return {
            "fuel_cost": round(fuel_cost, 2),
            "accommodation_cost": round(accommodation_cost, 2),
            "daily_allowance": round(daily_allowance, 2),
            "total": round(fuel_cost + accommodation_cost + daily_allowance, 2),
        }
