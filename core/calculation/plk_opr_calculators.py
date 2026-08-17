"""
core/calculation/plk_opr_calculators.py
Расчёт цены для клиента на ПЛК и ОПР.
Вынесено из calculator.py (v6.5).
ИСПРАВЛЕНО (v6.9.0):
  - PlkCalculator: расчёт транспорта по километражу (убран transport_default=40000)
  - PlkCalculator: доступ к глобальным travel-константам через all_costs
  - OprCalculator: ИСПРАВЛЕНА формула — теперь использует rates.per_position (500 ₽)
  - OprCalculator: добавлен параметр opr_persons для rates.per_person (150 ₽)
  - OprCalculator: убраны sot_cost/processing_cost (это себестоимость, не цена клиента)
  - OprCalculator: margin_percent читается из costs_db.json
"""

from loguru import logger

from core.calculation.cost_loader import load_costs
from core.calculation.calculation_result import CalculationResult


class PlkCalculator:
    """Расчёт цены для клиента на ПЛК."""

    def __init__(self):
        self.all_costs = load_costs()
        self.costs = self.all_costs["plk"]

    def calculate(
        self,
        points_count: int,
        factors_count: int = 0,
        delivery_count: int = 1,
        is_annual: bool = False,
        needs_subcontractor: bool = False,
        distance_km: float = 0,
        transport_cost: float = 0,
        accommodation_cost: float = 0,
    ) -> CalculationResult:
        """Расчёт цены для клиента на ПЛК.
        v6.9.1: Годовые тендеры — points_cost, measurer_cost, materials_cost ×12
        """
        # v6.9.1: Годовой множитель для стоимости точек и материалов
        annual_mult = 12 if is_annual else 1

        points_cost = (
            points_count * self.costs["base_cost_per_point"]["cost"] * annual_mult
        )
        measurer_cost = (
            points_count
            * self.costs["labor"]["measurer_per_point"]["cost"]
            * annual_mult
        )

        materials_cost = (
            self.costs["materials"]["paper_a4"]["cost"]
            * self.costs["materials"]["paper_a4"]["default_quantity"]
            + self.costs["materials"]["ink_per_page"]["cost"]
            * self.costs["materials"]["ink_per_page"]["default_quantity"]
        ) * annual_mult

        actual_delivery = 12 if is_annual else delivery_count
        delivery_cost = actual_delivery * self.costs["delivery"]["post_russia"]["cost"]

        subcontractor_cost = (
            self.costs["subcontractor"]["default_cost"] if needs_subcontractor else 0
        )

        # Расчёт транспорта по километражу
        travel = self.costs["travel"]
        if distance_km > 0:
            fuel = self.all_costs["travel"]["fuel"]
            transport_cost = (
                distance_km
                / 100
                * fuel["consumption_l_per_100km"]
                * fuel["price_per_liter"]
            )
            logger.info(
                f"[PlkCalc v6.9.0] Транспорт по километражу: "
                f"{distance_km}km → {transport_cost:,.0f}₽ "
                f"({fuel['consumption_l_per_100km']}л/100км × {fuel['price_per_liter']}₽/л)"
            )
        elif transport_cost > 0:
            logger.warning(
                f"[PlkCalc v6.9.0] Использован устаревший параметр transport_cost={transport_cost:,.0f}. "
                f"Рекомендуется передавать distance_km для точного расчёта."
            )
        else:
            transport_cost = 0
            logger.info(
                f"[PlkCalc v6.9.0] Транспорт = 0 (distance_km=0, transport_cost=0)"
            )

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
                "distance_km": distance_km,
                "transport_cost": transport_cost,
                "accommodation_cost": accommodation_cost,
                "daily_allowance": daily_allowance,
            },
        )


class OprCalculator:
    """Расчёт цены для клиента на ОПР."""

    def __init__(self):
        self.all_costs = load_costs()
        self.costs = self.all_costs["opr"]

    def calculate(
        self,
        rm_count: int = 0,
        opr_positions: int = 0,
        opr_persons: int = 0,
        delivery_count: int = 1,
        needs_siz_norms: bool = False,
        needs_dsiz_norms: bool = False,
        needs_iot_norms: bool = False,
        transport_cost: float = 0,
    ) -> CalculationResult:
        """Расчёт цены для клиента на ОПР.

        v6.9.0: ИСПРАВЛЕНА формула.
        - Основная стоимость: rates.per_position (500 ₽/должность) или
          rates.per_person (150 ₽/человек)
        - Убраны sot_cost, processing_cost, program_cost (это себестоимость)
        """
        # Определяем базу для расчёта
        if opr_positions > 0:
            base_count = opr_positions
            rate = self.costs["rates"]["per_position"]["cost"]
            rate_type = "per_position"
        elif opr_persons > 0:
            base_count = opr_persons
            rate = self.costs["rates"]["per_person"]["cost"]
            rate_type = "per_person"
        elif rm_count > 0:
            # Fallback: rm_count → opr_positions
            base_count = rm_count
            rate = self.costs["rates"]["per_position"]["cost"]
            rate_type = "per_position (fallback from rm_count)"
            logger.info(
                f"[OprCalc v6.9.0] Fallback: rm_count={rm_count} → opr_positions"
            )
        else:
            return CalculationResult(
                cost_price=0,
                recommended_price=0,
                margin_percent=0,
                margin_rub=0,
                needs_manual_review=True,
                review_reason="Не указано количество должностей/работников для ОПР",
            )

        # Основная стоимость
        position_cost = base_count * rate

        # Материалы
        materials_cost = (
            self.costs["materials"]["paper_a4"]["cost"]
            * self.costs["materials"]["paper_a4"]["default_quantity"]
            + self.costs["materials"]["ink_per_page"]["cost"]
            * self.costs["materials"]["ink_per_page"]["default_quantity"]
        )

        # Доп. документы
        additional_cost = 0
        if needs_siz_norms:
            additional_cost += base_count * 200
        if needs_dsiz_norms:
            additional_cost += base_count * 200
        if needs_iot_norms:
            additional_cost += base_count * 200

        delivery_cost = delivery_count * self.costs["delivery"]["post_russia"]["cost"]

        cost_price = (
            materials_cost
            + position_cost
            + additional_cost
            + delivery_cost
            + transport_cost
        )

        # Маржа из конфига (fallback 10%)
        margin_percent = float(self.costs.get("margin_percent", 10.0))
        margin_rub = cost_price * (margin_percent / 100)
        recommended_price = cost_price + margin_rub

        if recommended_price < 15000:
            recommended_price = 15000
            margin_rub = recommended_price - cost_price
            margin_percent = (margin_rub / cost_price) * 100 if cost_price > 0 else 0

        # Guard: если margin_percent из конфига > 20%, логируем
        config_margin = float(self.costs.get("margin_percent", 10.0))
        if config_margin > 20:
            logger.warning(
                f"[OprCalc v6.9.0] ВНИМАНИЕ: margin_percent из конфига = {config_margin}%. "
                f"Ожидалось ~10%. Проверьте costs_db.json → opr.margin_percent"
            )

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
                "base_count": base_count,
                "rate_type": rate_type,
                "rate_per_unit": rate,
                "position_cost": position_cost,
                "materials_cost": materials_cost,
                "additional_cost": additional_cost,
                "delivery_cost": delivery_cost,
                "needs_siz_norms": needs_siz_norms,
                "needs_dsiz_norms": needs_dsiz_norms,
                "needs_iot_norms": needs_iot_norms,
                "config_margin_percent": config_margin,
            },
        )
