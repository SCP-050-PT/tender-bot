"""
core/calculation/sout_calculator.py
Расчёт цены для клиента на СОУТ (3 варианта).
Вынесено из calculator.py (v6.5).
"""

from typing import Literal
from dataclasses import dataclass
from loguru import logger

from core.calculation.cost_loader import load_costs


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
        }


class SoutCalculator:
    """Расчёт цены для клиента на СОУТ."""

    def __init__(self):
        self.costs = load_costs()["sout"]
        self.cat = self.costs["category_rates"]
        self.travel = self.costs.get("travel", {})

    def calculate(
        self,
        rm_total: int,
        rm_category_1: int = 0,
        rm_category_2: int = 0,
        rm_with_iii: int = 0,
        variant: Literal[1, 2, 3] = 1,
        delivery_count: int = 1,
        is_annual: bool = False,
        needs_subcontractor: bool = False,
        cities_count: int = 1,
        addresses_count: int = 1,
        trip_days: int = 3,
        regions_count: int = 1,
        transport_cost: float = 0,
        is_seasonal: bool = False,
    ) -> CalculationResult:
        """
        Расчёт цены для клиента на СОУТ.
        v6.4.2: trips = regions_count, унифицированные командировочные.
        """
        # === Субподряд ИИИ ===
        subcontractor_cost, needs_manual_review_iii = self._calc_subcontractor(
            rm_with_iii, needs_subcontractor
        )

        # === Основной расчёт по варианту ===
        price = self._calc_main_price(rm_total, rm_category_1, rm_category_2, variant)

        # === Материалы и доставка ===
        materials_cost = self._calc_materials()
        delivery_cost = self._calc_delivery(delivery_count, is_annual)

        # === Командировочные ===
        travel_cost_auto, measurer_and_daily, accommodation_cost_auto, flight_cost = \
            self._calc_travel(trip_days, regions_count, transport_cost, is_seasonal, cities_count)

        # === Итого ===
        cost_price = (
            price + materials_cost + delivery_cost
            + travel_cost_auto + measurer_and_daily + accommodation_cost_auto
            + flight_cost + subcontractor_cost
        )

        margin_percent = 10.0
        margin_rub = cost_price * 0.1
        recommended_price = cost_price + margin_rub

        # Минимум 20 000₽ для СОУТ
        if recommended_price < 20000:
            recommended_price = 20000
            margin_rub = recommended_price - cost_price
            margin_percent = (margin_rub / cost_price) * 100 if cost_price > 0 else 0

        return CalculationResult(
            cost_price=cost_price,
            recommended_price=recommended_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            transport_cost=travel_cost_auto + flight_cost,
            subcontractor_cost=subcontractor_cost,
            guarantee_cost=0,
            details={
                "type": "sout",
                "variant": variant,
                "rm_total": rm_total,
                "rm_category_1": rm_category_1,
                "rm_category_2": rm_category_2,
                "rm_with_iii": rm_with_iii,
                "needs_manual_review_iii": needs_manual_review_iii,
                "main_calculation": price,
                "materials_cost": materials_cost,
                "delivery_cost": delivery_cost,
                "travel_cost": travel_cost_auto,
                "measurer_and_daily": measurer_and_daily,
                "accommodation_cost": accommodation_cost_auto,
                "flight_cost": flight_cost,
                "cities_count": cities_count,
                "regions_count": regions_count,
                "addresses_count": addresses_count,
                "trip_days": trip_days,
                "is_annual": is_annual,
                "is_seasonal": is_seasonal,
            },
        )

    def _calc_subcontractor(self, rm_with_iii: int, needs_subcontractor: bool) -> tuple:
        """Расчёт субподряда ИИИ."""
        if rm_with_iii > 0:
            for range_info in self.costs["iii_subcontractor"]["ranges"]:
                if rm_with_iii <= range_info["max_rm"]:
                    return range_info["cost"], False
            return 7000 + (rm_with_iii - 20) * 350, False
        elif needs_subcontractor:
            min_cost = self.costs["iii_subcontractor"]["ranges"][0]["cost"]
            logger.warning(
                f"[SoutCalc] ИИИ в ТЗ, но кол-во РМ не указано. "
                f"Заложен мин. субподряд {min_cost}₽. ТРЕБУЕТСЯ РУЧНАЯ ПРОВЕРКА."
            )
            return min_cost, True
        return 0, False

    def _calc_main_price(self, rm_total: int, rm_cat_1: int, rm_cat_2: int, variant: int) -> float:
        """Основной расчёт по варианту СОУТ."""
        if variant == 1:
            main_rm = max(2, int(rm_total * 0.2))
            if rm_cat_1 + rm_cat_2 > 0:
                ratio_c1 = rm_cat_1 / (rm_cat_1 + rm_cat_2)
                main_c1 = int(main_rm * ratio_c1)
                main_c2 = main_rm - main_c1
            else:
                main_c1, main_c2 = main_rm, 0
            analogy_rm = rm_total - main_rm
            return (
                main_c1 * self.cat["1"]["full_cost"]
                + main_c2 * self.cat["2"]["full_cost"]
                + analogy_rm * self.cat["1"]["analogy_cost"]
            )
        elif variant == 2:
            cards_cost = rm_cat_1 * self.cat["1"]["full_cost"] + rm_cat_2 * self.cat["2"]["full_cost"]
            analogy_rm = rm_total - (rm_cat_1 + rm_cat_2)
            return cards_cost + analogy_rm * 200
        else:  # variant == 3
            cards_cost = rm_cat_1 * self.cat["1"]["card_cost"] + rm_cat_2 * self.cat["2"]["card_cost"]
            remaining_rm = max(0, rm_total - rm_cat_1 - rm_cat_2)
            protocol_sets = max(2, int(remaining_rm * 0.2))
            return cards_cost + protocol_sets * self.costs["analogy_protocol_set"]["cost"]

    def _calc_materials(self) -> float:
        """Расчёт материалов."""
        return (
            self.costs["materials"]["paper_a4"]["cost"] * self.costs["materials"]["paper_a4"]["default_quantity"]
            + self.costs["materials"]["ink_per_page"]["cost"] * self.costs["materials"]["ink_per_page"]["default_quantity"]
        )

    def _calc_delivery(self, delivery_count: int, is_annual: bool) -> float:
        """Расчёт доставки."""
        actual_delivery = 12 if is_annual else delivery_count
        return actual_delivery * self.costs["delivery"]["post_russia"]["cost"]

    def _calc_travel(
        self, trip_days: int, regions_count: int, transport_cost: float,
        is_seasonal: bool, cities_count: int
    ) -> tuple:
        """Расчёт командировочных. Возвращает (travel_auto, measurer_daily, accommodation, flight)."""
        seasonal_mult = self.travel.get("seasonal_multiplier", 2) if is_seasonal else 1
        fixed_trip = self.travel.get("fixed_trip_cost", 12000)
        accommodation_rate = self.travel.get("accommodation_per_night", 2500)
        daily_measurer_rate = self.travel.get("daily_measurer_rate", 5000)

        trips = max(1, regions_count)

        travel_cost_auto = fixed_trip * trips * seasonal_mult
        measurer_and_daily = daily_measurer_rate * trip_days * trips * seasonal_mult
        accommodation_cost_auto = max(0, trip_days - 1) * trips * accommodation_rate * seasonal_mult
        flight_cost = transport_cost if transport_cost > 0 else 0

        if cities_count > 5 and regions_count == 1:
            logger.warning(
                f"[SoutCalc] Много адресов ({cities_count}) в 1 регионе — "
                f"проверьте маршрут выезда"
            )

        logger.info(
            f"[SoutCalc] Командировочные: регионов={regions_count}, выездов={trips}, "
            f"дней={trip_days}, сезон={is_seasonal}, бензин/выезд={travel_cost_auto}, "
            f"суточные+замерщик={measurer_and_daily}, прожив={accommodation_cost_auto}, "
            f"билеты={flight_cost}"
        )

        return travel_cost_auto, measurer_and_daily, accommodation_cost_auto, flight_cost
