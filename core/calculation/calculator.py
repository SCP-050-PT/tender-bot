"""
core/calculation/calculator.py
Главный калькулятор — фасад для всех типов тендеров.
ИСПРАВЛЕНО (v6.7.3):
  - Убран дублирующийся CalculationResult (импорт из calculation_result.py)
  - Делегирует расчёты специализированным калькуляторам
"""

from loguru import logger

from core.calculation.cost_loader import load_costs
from core.calculation.calculation_result import CalculationResult
from core.calculation.education_calculator import EducationCalculator
from core.calculation.sout_calculator import SoutCalculator
from core.calculation.plk_opr_calculators import PlkCalculator, OprCalculator


class TenderCalculator:
    """Главный калькулятор — фасад для всех типов тендеров."""

    def __init__(self):
        self.costs = load_costs()
        self.education_calc = EducationCalculator()
        self.sout_calc = SoutCalculator()
        self.plk_calc = PlkCalculator()
        self.opr_calc = OprCalculator()
        logger.info("TenderCalculator инициализирован (v6.7.3)")

    def calculate_education(self, **kwargs) -> CalculationResult:
        """Расчёт обучения."""
        return self.education_calc.calculate(**kwargs)

    def calculate_sout(self, **kwargs) -> CalculationResult:
        """Расчёт СОУТ."""
        return self.sout_calc.calculate(**kwargs)

    def calculate_plk(self, **kwargs) -> CalculationResult:
        """Расчёт ПЛК."""
        return self.plk_calc.calculate(**kwargs)

    def calculate_opr(self, **kwargs) -> CalculationResult:
        """Расчёт ОПР."""
        return self.opr_calc.calculate(**kwargs)

    def calculate_guarantee(
        self, contract_sum: float, guarantee_type: str = "contract"
    ) -> float:
        """Расчёт стоимости банковской гарантии."""
        ranges = self.costs["guarantees"]["application"]["bank_guarantee_cost"][
            "ranges"
        ]
        for r in ranges:
            if contract_sum <= r["max_contract"]:
                return r["real_cost"]
        return ranges[-1]["real_cost"]

    def calculate_transport(
        self,
        distance_km: float = 0,
        accommodation_nights: int = 0,
        expert_days: int = 0,
    ) -> dict:
        """Расчёт транспортных расходов."""
        fuel = self.costs["travel"]["fuel"]
        fuel_cost = (
            distance_km
            / 100
            * fuel["consumption_l_per_100km"]
            * fuel["price_per_liter"]
        )
        accommodation_cost = (
            accommodation_nights
            * self.costs["travel"]["accommodation"]["standard_per_night"]
        )
        daily_allowance = (
            expert_days * self.costs["travel"]["daily_allowance"]["standard"]
        )
        return {
            "fuel_cost": fuel_cost,
            "accommodation_cost": accommodation_cost,
            "daily_allowance": daily_allowance,
            "total": fuel_cost + accommodation_cost + daily_allowance,
        }
