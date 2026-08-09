"""
core/calculation/plk_opr_calculators.py
Расчёт цены для клиента на ПЛК и ОПР.
Вынесено из calculator.py (v6.5).
ИСПРАВЛЕНО (v6.7.3):
  - Убран дублирующийся CalculationResult (импорт из calculation_result.py)
"""

from loguru import logger

from core.calculation.cost_loader import load_costs
from core.calculation.calculation_result import CalculationResult


class PlkCalculator:
    """Расчёт цены для клиента на ПЛК."""

    def __init__(self):
        self.costs = load_costs()["plk"]

    def calculate(
        self,
        points_count: int,
        factors_count: int = 0,
        delivery_count: int = 1,
        is_annual: bool = False,
        needs_subcontractor: bool = False,
        transport_cost: float = 0,
        accommodation_cost: float = 0,
    ) -> CalculationResult:
        """Расчёт цены для клиента на ПЛК."""
        points_cost = points_count * self.costs["base_cost_per_point"]["cost"]
        measurer_cost = points_count * self.costs["labor"]["measurer_per_point"]["cost"]

        materials_cost = (
            self.costs["materials"]["paper_a4"]["cost"]
            * self.costs["materials"]["paper_a4"]["default_quantity"]
            + self.costs["materials"]["ink_per_page"]["cost"]
            * self.costs["materials"]["ink_per_page"]["default_quantity"]
        )

        actual_delivery = 12 if is_annual else delivery_count
        delivery_cost = actual_delivery * self.costs["delivery"]["post_russia"]["cost"]

        subcontractor_cost = (
            self.costs["subcontractor"]["default_cost"] if needs_subcontractor else 0
        )

        travel = self.costs["travel"]
        if transport_cost == 0 and points_count > 0:
            transport_cost = travel["transport_default"]
        if accommodation_cost == 0:
            accommodation_cost = travel["accommodation_default"]
        daily_allowance = travel["daily_allowance"]

        cost_price = (
            points_cost
            + measurer_cost
            + materials_cost
            + delivery_cost
            + subcontractor_cost
            + transport_cost
            + accommodation_cost
            + daily_allowance
        )

        margin_percent = 10.0
        margin_rub = cost_price * 0.1
        recommended_price = cost_price + margin_rub

        if recommended_price < 15000:
            recommended_price = 15000
            margin_rub = recommended_price - cost_price
            margin_percent = (margin_rub / cost_price) * 100 if cost_price > 0 else 0

        return CalculationResult(
            cost_price=cost_price,
            recommended_price=recommended_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            transport_cost=transport_cost,
            subcontractor_cost=subcontractor_cost,
            guarantee_cost=0,
            details={
                "type": "plk",
                "points_count": points_count,
                "factors_count": factors_count,
                "points_cost": points_cost,
                "measurer_cost": measurer_cost,
                "materials_cost": materials_cost,
                "delivery_cost": delivery_cost,
                "is_annual": is_annual,
                "needs_subcontractor": needs_subcontractor,
            },
        )


class OprCalculator:
    """Расчёт цены для клиента на ОПР."""

    def __init__(self):
        self.costs = load_costs()["opr"]

    def calculate(
        self,
        rm_count: int,
        delivery_count: int = 1,
        needs_siz_norms: bool = False,
        needs_dsiz_norms: bool = False,
        needs_iot_norms: bool = False,
        transport_cost: float = 0,
    ) -> CalculationResult:
        """Расчёт цены для клиента на ОПР."""
        materials_cost = (
            self.costs["materials"]["paper_a4"]["cost"]
            * self.costs["materials"]["paper_a4"]["default_quantity"]
            + self.costs["materials"]["ink_per_page"]["cost"]
            * self.costs["materials"]["ink_per_page"]["default_quantity"]
        )

        sot_cost = rm_count * self.costs["labor"]["sot_per_rm"]["cost"]
        processing_cost = rm_count * self.costs["labor"]["processing_per_rm"]["cost"]
        program_cost = self.costs["labor"]["program_per_day"]["cost"]

        additional_cost = 0
        if needs_siz_norms:
            additional_cost += rm_count * 200
        if needs_dsiz_norms:
            additional_cost += rm_count * 200
        if needs_iot_norms:
            additional_cost += rm_count * 200

        delivery_cost = delivery_count * self.costs["delivery"]["post_russia"]["cost"]

        cost_price = (
            materials_cost
            + sot_cost
            + processing_cost
            + program_cost
            + additional_cost
            + delivery_cost
            + transport_cost
        )

        margin_percent = 30.0
        margin_rub = cost_price * 0.3
        recommended_price = cost_price + margin_rub

        if recommended_price < 15000:
            recommended_price = 15000
            margin_rub = recommended_price - cost_price
            margin_percent = (margin_rub / cost_price) * 100 if cost_price > 0 else 0

        return CalculationResult(
            cost_price=cost_price,
            recommended_price=recommended_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            transport_cost=transport_cost,
            subcontractor_cost=0,
            guarantee_cost=0,
            details={
                "type": "opr",
                "rm_count": rm_count,
                "materials_cost": materials_cost,
                "sot_cost": sot_cost,
                "processing_cost": processing_cost,
                "additional_cost": additional_cost,
                "delivery_cost": delivery_cost,
                "needs_siz_norms": needs_siz_norms,
                "needs_dsiz_norms": needs_dsiz_norms,
                "needs_iot_norms": needs_iot_norms,
            },
        )
