"""
core/calculator.py
Калькулятор ЦЕНЫ ДЛЯ КЛИЕНТА (не себестоимости).
Все формулы взяты из Excel-калькуляторов заказчицы.
ИСПРАВЛЕНО (26.07.2026 v5.5):
  - calculate_sout(): addresses_count → cities_count для выездов (БАГ 4.1)
  - calculate_sout(): замерщик = trip_days × 5000, не × trips (БАГ 4.2)
  - calculate_sout(): для 1 адреса авто-расчёт выезда (БАГ 4.3)
  - calculate_education(): teacher_days из ТЗ, не авто ceil/25
  - calculate_combined(): ОПР считает rm_count (sot + processing)
  - calculate_education(): минимум 10000 и для очного
"""

import json
import math
from pathlib import Path
from typing import Optional, Literal
from dataclasses import dataclass
from loguru import logger

from config.settings import settings

# === ЛЕНИВАЯ ЗАГРУЗКА БАЗЫ ЦЕН ===
COSTS: Optional[dict] = None


def _load_costs() -> dict:
    """Ленивая загрузка базы цен."""
    global COSTS
    if COSTS is not None:
        return COSTS

    costs_db_path = (
        Path(__file__).resolve().parent.parent / "knowledge" / "costs_db.json"
    )

    if costs_db_path.exists():
        try:
            with open(costs_db_path, "r", encoding="utf-8") as f:
                COSTS = json.load(f)
            logger.info(f"✅ База цен загружена: {costs_db_path}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки costs_db.json: {e}")
            COSTS = _get_default_costs()
    else:
        logger.warning(f"⚠️ Файл costs_db.json не найден: {costs_db_path}")
        logger.warning(f"⚠️ Используются значения по умолчанию")
        COSTS = _get_default_costs()

    return COSTS


def _get_default_costs() -> dict:
    """Встроенные значения по умолчанию (fallback) — ЦЕНЫ ДЛЯ КЛИЕНТА."""
    return {
        "education": {
            "documents": {
                "certificate": {"cost": 60},
                "diploma": {"cost": 265},
                "certificate_worker": {"cost": 80},
                "certificate_qualification": {"cost": 130},
            },
            "materials": {
                "paper_a4": {"cost": 1.15},
                "ink_per_page": {"cost": 2.5},
                "lamination": {"cost": 10},
            },
            "labor": {
                "methodist_hour": {"cost": 227},
                "ro_hour": {"cost": 600},
                "portal_access": {"cost": 57},
            },
            "delivery": {
                "post_russia": {"cost": 300},
            },
            "overhead": {
                "base": {"cost": 100},
            },
            "rates": {
                "teacher_daily": {"cost": 8000, "unit": "день"},
                "manikin_daily": {"cost": 15000, "unit": "день"},
                "venue_daily": {"cost": 3000, "unit": "день"},
                "transport_fixed": {
                    "cost": 10000,
                    "unit": "выезд",
                    "range": [5000, 15000],
                },
            },
            "forms": {
                "full_time": {
                    "fuel_cost_per_km": 5,
                    "accommodation_per_night": 2500,
                    "daily_allowance": 1000,
                }
            },
        },
        "sout": {
            "category_rates": {
                "1": {"full_cost": 900, "card_cost": 1500, "analogy_cost": 100},
                "2": {"full_cost": 1800, "card_cost": 2000, "analogy_cost": 100},
            },
            "analogy_protocol_set": {"cost": 1000},
            "materials": {
                "paper_a4": {"cost": 1.15, "default_quantity": 20},
                "ink_per_page": {"cost": 2.5, "default_quantity": 20},
            },
            "delivery": {
                "post_russia": {"cost": 500},
                "post_russia_high": {"cost": 1000},
            },
            "iii_subcontractor": {
                "ranges": [
                    {"max_rm": 10, "cost": 5000},
                    {"max_rm": 15, "cost": 6000},
                    {"max_rm": 20, "cost": 7000},
                ]
            },
            "travel": {
                "fixed_trip_cost": 12000,
                "trip_days_default": 3,
                "daily_allowance": 500,
                "accommodation_per_night": 2500,
                "measurer_daily_rate": 5000,
                "seasonal_multiplier": 2,
            },
        },
        "plk": {
            "base_cost_per_point": {"cost": 41.9},
            "labor": {
                "measurer_per_point": {"cost": 20},
            },
            "materials": {
                "paper_a4": {"cost": 1.15, "default_quantity": 6},
                "ink_per_page": {"cost": 2.5, "default_quantity": 6},
            },
            "delivery": {
                "post_russia": {"cost": 500},
            },
            "subcontractor": {
                "default_cost": 10000,
            },
            "travel": {
                "transport_default": 40000,
                "accommodation_default": 4000,
                "daily_allowance": 4000,
            },
        },
        "opr": {
            "rates": {
                "per_position": {"cost": 500, "unit": "должность"},
                "per_person": {"cost": 150, "unit": "человек"},
            },
            "materials": {
                "paper_a4": {"cost": 1.15, "default_quantity": 16},
                "ink_per_page": {"cost": 2.5, "default_quantity": 16},
            },
            "labor": {
                "sot_per_rm": {"cost": 40},
                "processing_per_rm": {"cost": 10},
                "program_per_day": {"cost": 10},
            },
            "delivery": {
                "post_russia": {"cost": 1000},
            },
        },
        "guarantees": {
            "application": {
                "bank_guarantee_cost": {
                    "ranges": [
                        {"max_contract": 50000, "real_cost": 1000},
                        {"max_contract": 100000, "real_cost": 1200},
                        {"max_contract": 500000, "real_cost": 2000},
                        {"max_contract": 1000000, "real_cost": 4000},
                        {"max_contract": 5000000, "real_cost": 10000},
                    ]
                }
            }
        },
        "travel": {
            "fuel": {
                "consumption_l_per_100km": 11,
                "price_per_liter": 55,
            },
            "accommodation": {
                "standard_per_night": 2500,
            },
            "daily_allowance": {
                "standard": 500,
            },
        },
    }


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


class TenderCalculator:
    """
    Калькулятор ЦЕНЫ ДЛЯ КЛИЕНТА для всех типов тендеров.
    Поддерживает: обучение, СОУТ (3 варианта), ПЛК, ОПР, combined.
    """

    def __init__(self):
        self.costs = _load_costs()
        logger.info("TenderCalculator инициализирован (ЦЕНА ДЛЯ КЛИЕНТА)")

    # ==================== ОБУЧЕНИЕ ====================

    def calculate_education(
        self,
        students_count: int,
        certificates: int = 0,
        diplomas: int = 0,
        worker_certs: int = 0,
        qual_certs: int = 0,
        protocols_count: int = 0,
        is_distance: bool = True,
        teacher_days: int = 0,
        teacher_rate: int = 8000,
        accommodation_nights: int = 0,
        transport_km: int = 0,
        venue_rent_days: int = 0,
        manikin_days: int = 0,
        delivery_count: int = 1,
        has_lamination: bool = False,
        tender_text: str = "",
    ) -> CalculationResult:
        """
        Расчёт цены для клиента на обучение.
        ИСПРАВЛЕНО v5.5:
        - teacher_days из ТЗ (не авто-расчёт ceil/25)
        - Минимум 10000₽ и для очного обучения
        """
        edu = self.costs["education"]
        rates = edu.get("rates", {})
        text_lower = tender_text.lower()

        # === Документы ===
        actual_certificates = certificates if certificates > 0 else students_count

        docs_cost = (
            actual_certificates * edu["documents"]["certificate"]["cost"]
            + diplomas * edu["documents"]["diploma"]["cost"]
            + worker_certs * edu["documents"]["certificate_worker"]["cost"]
            + qual_certs * edu["documents"]["certificate_qualification"]["cost"]
        )

        # === Материалы ===
        total_docs = (
            actual_certificates + diplomas + worker_certs + qual_certs + protocols_count
        )
        paper_cost = total_docs * edu["materials"]["paper_a4"]["cost"]
        ink_cost = total_docs * edu["materials"]["ink_per_page"]["cost"]
        lamination_cost = (
            actual_certificates * edu["materials"]["lamination"]["cost"]
            if has_lamination
            else 0
        )

        # === Труд ===
        methodist_cost = 3 * edu["labor"]["methodist_hour"]["cost"]
        ro_cost = 3 * edu["labor"]["ro_hour"]["cost"]
        portal_cost = edu["labor"]["portal_access"]["cost"]

        # === Доставка ===
        delivery_cost = delivery_count * edu["delivery"]["post_russia"]["cost"]

        # === Накладные ===
        overhead_cost = edu["overhead"]["base"]["cost"]

        # === Очная часть ===
        full_time_cost = 0
        transport_cost = 0
        teacher_cost = 0
        accommodation_cost = 0
        daily_allowance_cost = 0
        venue_cost = 0
        manikin_cost = 0

        if not is_distance:
            # --- Преподаватель: из ТЗ, не авто ---
            if teacher_days > 0:
                teacher_cost = teacher_days * teacher_rate
            else:
                # ← v6.2: Улучшенный fallback: оценка по слушателям и городам
                if students_count > 0:
                    # ~25 слушателей в день, минимум 1 день
                    estimated_days = max(1, math.ceil(students_count / 25))
                    teacher_days = estimated_days
                    logger.info(
                        f"[v6.2] Авто-оценка teacher_days={teacher_days} "
                        f"({students_count} слуш., ~25/день)"
                    )
                else:
                    teacher_days = 1
                    logger.info(
                        f"[v5.5] teacher_days не указан → fallback 1 день × {teacher_rate}"
                    )
                teacher_cost = teacher_days * teacher_rate

            # --- Проезд: если transport_km=0 — fallback фиксированный ---
            if transport_km > 0:
                fuel_liters = (transport_km / 100) * 11
                transport_cost = fuel_liters * 55
            else:
                transport_fixed = rates.get("transport_fixed", {}).get("cost", 10000)
                transport_cost = transport_fixed
                logger.info(f"[v5.5] Fallback transport_cost={transport_cost} (km=0)")

            # --- Проживание: из ТЗ, не авто ---
            if accommodation_nights > 0:
                accommodation_cost = (
                    accommodation_nights
                    * edu["forms"]["full_time"]["accommodation_per_night"]
                )
            else:
                # Fallback: если не указано — teacher_days ночей
                accommodation_nights = teacher_days
                accommodation_cost = (
                    accommodation_nights
                    * edu["forms"]["full_time"]["accommodation_per_night"]
                )

            # --- Суточные ---
            daily_allowance_cost = (teacher_days + 2) * edu["forms"]["full_time"][
                "daily_allowance"
            ]

            # --- Аренда: из ТЗ, не авто ---
            if venue_rent_days > 0:
                venue_daily = rates.get("venue_daily", {}).get("cost", 3000)
                venue_cost = venue_rent_days * venue_daily
            else:
                # Fallback: если не указана — teacher_days дней
                venue_rent_days = teacher_days
                venue_daily = rates.get("venue_daily", {}).get("cost", 3000)
                venue_cost = venue_rent_days * venue_daily

            # --- Манекен: авто-определение по тексту ТЗ ---
            if manikin_days == 0 and (
                "первая помощь" in text_lower or "манекен" in text_lower
            ):
                manikin_days = 1
                logger.info(
                    f"[v5.5] Авто-определение manikin_days=1 (первая помощь/манекен в ТЗ)"
                )
            manikin_daily = rates.get("manikin_daily", {}).get("cost", 15000)
            manikin_cost = manikin_days * manikin_daily

            full_time_cost = (
                teacher_cost
                + accommodation_cost
                + daily_allowance_cost
                + venue_cost
                + manikin_cost
            )
            logger.info(
                f"[v5.5] Очные затраты: препод={teacher_cost}, проезд={transport_cost}, "
                f"прожив={accommodation_cost}, суточные={daily_allowance_cost}, "
                f"аренда={venue_cost}, манекен={manikin_cost}"
            )

        # === Итого себестоимость ===
        cost_price = (
            docs_cost
            + paper_cost
            + ink_cost
            + lamination_cost
            + methodist_cost
            + ro_cost
            + portal_cost
            + delivery_cost
            + overhead_cost
            + full_time_cost
            + transport_cost
        )

        # === Цена для клиента = себестоимость + маржа 10% ===
        margin_percent = 10.0
        margin_rub = cost_price * (margin_percent / 100)
        recommended_price = cost_price + margin_rub

        # === v5.5: Минимум 10 000₽ для ВСЕХ типов обучения ===
        if recommended_price < 10000:
            recommended_price = 10000
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
                "type": "education",
                "students_count": students_count,
                "actual_certificates": actual_certificates,
                "documents_cost": docs_cost,
                "materials_cost": paper_cost + ink_cost + lamination_cost,
                "labor_cost": methodist_cost + ro_cost + portal_cost,
                "delivery_cost": delivery_cost,
                "overhead_cost": overhead_cost,
                "full_time_cost": full_time_cost,
                "is_distance": is_distance,
                "teacher_days": teacher_days,
                "teacher_cost": teacher_cost,
                "accommodation_nights": accommodation_nights,
                "accommodation_cost": accommodation_cost,
                "daily_allowance_cost": daily_allowance_cost,
                "transport_km": transport_km,
                "transport_cost": transport_cost,
                "venue_rent_days": venue_rent_days,
                "venue_cost": venue_cost,
                "manikin_days": manikin_days,
                "manikin_cost": manikin_cost,
            },
        )

    # ==================== СОУТ ====================

    def calculate_sout(
        self,
        rm_total: int,
        rm_category_1: int = 0,
        rm_category_2: int = 0,
        rm_with_iii: int = 0,
        variant: Literal[1, 2, 3] = 1,
        delivery_count: int = 1,
        is_annual: bool = False,
        # v5.5: addresses_count → cities_count (уникальные города)
        cities_count: int = 1,
        addresses_count: int = 1,  # для обратной совместимости
        trip_days: int = 3,
        transport_cost: float = 0,
        accommodation_nights: int = 0,
        expert_days: int = 1,
        is_seasonal: bool = False,
    ) -> CalculationResult:
        """
        Расчёт ЦЕНЫ ДЛЯ КЛИЕНТА на СОУТ. 3 варианта расчёта.

        ИСПРАВЛЕНО v5.5:
        - cities_count (уникальные города) для выездов, не addresses_count (БАГ 4.1)
        - Замерщик = trip_days × 5000, не × trips (БАГ 4.2)
        - Для 1 адреса: авто-расчёт выезда 12000 + trip_days×500 + (trip_days-1)×2500 + trip_days×5000 (БАГ 4.3)
        """
        sout = self.costs["sout"]
        cat = sout["category_rates"]
        sout_travel = sout.get("travel", {})

        # === Субподряд ИИИ ===
        subcontractor_cost = 0
        if rm_with_iii > 0:
            for range_info in sout["iii_subcontractor"]["ranges"]:
                if rm_with_iii <= range_info["max_rm"]:
                    subcontractor_cost = range_info["cost"]
                    break
            else:
                subcontractor_cost = 7000 + (rm_with_iii - 20) * 350

        # === Расчёт по вариантам (ЦЕНА ДЛЯ КЛИЕНТА) ===
        if variant == 1:
            main_rm = max(2, int(rm_total * 0.2))
            if rm_category_1 + rm_category_2 > 0:
                ratio_c1 = rm_category_1 / (rm_category_1 + rm_category_2)
                main_c1 = int(main_rm * ratio_c1)
                main_c2 = main_rm - main_c1
            else:
                main_c1 = main_rm
                main_c2 = 0

            analogy_rm = rm_total - main_rm
            price = (
                main_c1 * cat["1"]["full_cost"]
                + main_c2 * cat["2"]["full_cost"]
                + analogy_rm * cat["1"]["analogy_cost"]
            )

        elif variant == 2:
            cards_cost = (
                rm_category_1 * cat["1"]["full_cost"]
                + rm_category_2 * cat["2"]["full_cost"]
            )
            analogy_rm = rm_total - (rm_category_1 + rm_category_2)
            analogy_cost = analogy_rm * 200
            price = cards_cost + analogy_cost

        else:  # variant == 3
            cards_cost = (
                rm_category_1 * cat["1"]["card_cost"]
                + rm_category_2 * cat["2"]["card_cost"]
            )
            remaining_rm = max(0, rm_total - rm_category_1 - rm_category_2)
            protocol_sets = max(2, int(remaining_rm * 0.2))
            protocol_cost = protocol_sets * sout["analogy_protocol_set"]["cost"]
            price = cards_cost + protocol_cost

        # === Материалы ===
        materials_cost = (
            sout["materials"]["paper_a4"]["cost"]
            * sout["materials"]["paper_a4"]["default_quantity"]
            + sout["materials"]["ink_per_page"]["cost"]
            * sout["materials"]["ink_per_page"]["default_quantity"]
        )

        # === Доставка ===
        actual_delivery = 12 if is_annual else delivery_count
        delivery_cost = actual_delivery * sout["delivery"]["post_russia"]["cost"]

        # === v5.5: Командировочные расходы ===
        seasonal_mult = sout_travel.get("seasonal_multiplier", 2) if is_seasonal else 1
        fixed_trip = sout_travel.get("fixed_trip_cost", 12000)
        daily_allowance_rate = sout_travel.get("daily_allowance", 500)
        accommodation_rate = sout_travel.get("accommodation_per_night", 2500)
        measurer_rate = sout_travel.get("measurer_daily_rate", 5000)

        # v5.5: Используем cities_count (уникальные города), не addresses_count
        if cities_count > 1 or is_seasonal:
            # Многогородний или сезонный СОУТ
            trips = cities_count * seasonal_mult
            travel_cost_auto = trips * fixed_trip
            daily_allowance_auto = trip_days * trips * daily_allowance_rate
            accommodation_cost_auto = (trip_days - 1) * trips * accommodation_rate
            # v5.5: Замерщик = trip_days × measurer_rate (убран trips)
            measurer_cost_auto = trip_days * measurer_rate
            logger.info(
                f"[v5.5] СОУТ командировочные: городов={cities_count}, сезон={is_seasonal}, "
                f"дней={trip_days}, выезды={travel_cost_auto}, суточные={daily_allowance_auto}, "
                f"прожив={accommodation_cost_auto}, замерщик={measurer_cost_auto}"
            )
        else:
            # v5.5: Для 1 города — авто-расчёт (БАГ 4.3)
            # Раньше: travel_cost_auto = transport_cost (часто 0), daily_allowance=0
            # Сейчас: минимальный выезд
            travel_cost_auto = fixed_trip  # 12000 — минимальный выезд
            daily_allowance_auto = trip_days * daily_allowance_rate
            accommodation_cost_auto = (trip_days - 1) * accommodation_rate
            measurer_cost_auto = trip_days * measurer_rate
            logger.info(
                f"[v5.5] СОУТ 1 город: выезд={travel_cost_auto}, суточные={daily_allowance_auto}, "
                f"прожив={accommodation_cost_auto}, замерщик={measurer_cost_auto}"
            )

        # === ИТОГО: цена для клиента ===
        cost_price = (
            price
            + materials_cost
            + delivery_cost
            + travel_cost_auto
            + daily_allowance_auto
            + accommodation_cost_auto
            + measurer_cost_auto
            + subcontractor_cost
        )

        # Маржа 10%
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
            transport_cost=travel_cost_auto,
            subcontractor_cost=subcontractor_cost,
            guarantee_cost=0,
            details={
                "type": "sout",
                "variant": variant,
                "rm_total": rm_total,
                "rm_category_1": rm_category_1,
                "rm_category_2": rm_category_2,
                "rm_with_iii": rm_with_iii,
                "main_calculation": price,
                "materials_cost": materials_cost,
                "delivery_cost": delivery_cost,
                "travel_cost": travel_cost_auto,
                "daily_allowance": daily_allowance_auto,
                "accommodation_cost": accommodation_cost_auto,
                "measurer_cost": measurer_cost_auto,
                "cities_count": cities_count,  # v5.5
                "addresses_count": addresses_count,
                "trip_days": trip_days,
                "is_annual": is_annual,
                "is_seasonal": is_seasonal,
            },
        )

    # ==================== COMBINED (СОУТ + ОПР) ====================

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
        cities_count: int = 1,  # v5.5
        addresses_count: int = 1,
        trip_days: int = 3,
        transport_cost: float = 0,
        accommodation_nights: int = 0,
        expert_days: int = 1,
        is_seasonal: bool = False,
    ) -> CalculationResult:
        """
        Расчёт ЦЕНЫ ДЛЯ КЛИЕНТА на combined (СОУТ + ОПР).
        Считаем СОУТ и ОПР отдельно, суммируем.
        """
        # Считаем СОУТ
        sout_result = self.calculate_sout(
            rm_total=rm_total,
            rm_category_1=rm_category_1,
            rm_category_2=rm_category_2,
            rm_with_iii=rm_with_iii,
            variant=variant,
            delivery_count=delivery_count,
            is_annual=is_annual,
            cities_count=cities_count,  # v5.5
            addresses_count=addresses_count,
            trip_days=trip_days,
            transport_cost=transport_cost,
            accommodation_nights=accommodation_nights,
            expert_days=expert_days,
            is_seasonal=is_seasonal,
        )

        # v5.5: ОПР считаем через calculate_opr (с rm_count)
        opr_result = self.calculate_opr(
            rm_count=rm_total,
            delivery_count=delivery_count,
            transport_cost=transport_cost,
        )

        # Итого
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
            },
        )

    # ==================== ПЛК ====================

    def calculate_plk(
        self,
        points_count: int,
        factors_count: int,
        delivery_count: int = 1,
        is_annual: bool = False,
        needs_subcontractor: bool = False,
        transport_cost: float = 0,
        accommodation_cost: float = 0,
    ) -> CalculationResult:
        """Расчёт цены для клиента на ПЛК."""
        plk = self.costs["plk"]

        points_cost = points_count * plk["base_cost_per_point"]["cost"]
        measurer_cost = points_count * plk["labor"]["measurer_per_point"]["cost"]

        materials_cost = (
            plk["materials"]["paper_a4"]["cost"]
            * plk["materials"]["paper_a4"]["default_quantity"]
            + plk["materials"]["ink_per_page"]["cost"]
            * plk["materials"]["ink_per_page"]["default_quantity"]
        )

        actual_delivery = 12 if is_annual else delivery_count
        delivery_cost = actual_delivery * plk["delivery"]["post_russia"]["cost"]

        subcontractor_cost = (
            plk["subcontractor"]["default_cost"] if needs_subcontractor else 0
        )

        travel = plk["travel"]
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

    # ==================== ОПР ====================

    def calculate_opr(
        self,
        rm_count: int,
        delivery_count: int = 1,
        needs_siz_norms: bool = False,
        needs_dsiz_norms: bool = False,
        needs_iot_norms: bool = False,
        transport_cost: float = 0,
    ) -> CalculationResult:
        """Расчёт цены для клиента на ОПР."""
        opr = self.costs["opr"]

        materials_cost = (
            opr["materials"]["paper_a4"]["cost"]
            * opr["materials"]["paper_a4"]["default_quantity"]
            + opr["materials"]["ink_per_page"]["cost"]
            * opr["materials"]["ink_per_page"]["default_quantity"]
        )

        sot_cost = rm_count * opr["labor"]["sot_per_rm"]["cost"]
        processing_cost = rm_count * opr["labor"]["processing_per_rm"]["cost"]
        program_cost = opr["labor"]["program_per_day"]["cost"]

        additional_cost = 0
        if needs_siz_norms:
            additional_cost += rm_count * 200
        if needs_dsiz_norms:
            additional_cost += rm_count * 200
        if needs_iot_norms:
            additional_cost += rm_count * 200

        delivery_cost = delivery_count * opr["delivery"]["post_russia"]["cost"]

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
