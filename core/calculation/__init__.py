"""
core/calculation/__init__.py
Пакет калькуляторов цены для клиента.
"""

from core.calculation.calculator import TenderCalculator, CalculationResult
from core.calculation.education_calculator import EducationCalculator
from core.calculation.sout_calculator import SoutCalculator
from core.calculation.plk_opr_calculators import PlkCalculator, OprCalculator
from core.calculation.cost_loader import load_costs

__all__ = [
    "TenderCalculator",
    "CalculationResult",
    "EducationCalculator",
    "SoutCalculator",
    "PlkCalculator",
    "OprCalculator",
    "load_costs",
]
