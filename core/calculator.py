"""
core/calculator.py
Калькулятор себестоимости и цены предложения для тендеров.
Все формулы взяты из Excel-калькуляторов заказчицы.
"""

import json
from pathlib import Path
from typing import Optional, Literal
from dataclasses import dataclass
from loguru import logger

from config.settings import settings

# === ЛЕНИВАЯ ЗАГРУЗКА БАЗЫ СЕБЕСТОИМОСТЕЙ ===
COSTS: Optional[dict] = None


def _load_costs() -> dict:
    """Ленивая загрузка базы себестоимостей."""
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
            logger.info(f"✅ База себестоимостей загружена: {costs_db_path}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки costs_db.json: {e}")
            COSTS = _get_default_costs()
    else:
        logger.warning(f"⚠️ Файл costs_db.json не найден: {costs_db_path}")
        logger.warning(f"⚠️ Используются значения по умолчанию")
        COSTS = _get_default_costs()

    return COSTS


def _get_default_costs() -> dict:
    """Встроенные значения по умолчанию (fallback)."""
    return {
        "education": {
            "documents": {
                "certificate": {"cost": 150},
                "diploma": {"cost": 200},
                "certificate_worker": {"cost": 100},
                "certificate_qualification": {"cost": 180},
            },
            "materials": {
                "paper_a4": {"cost": 2},
                "ink_per_page": {"cost": 3},
                "lamination": {"cost": 50},
            },
            "labor": {
                "methodist_hour": {"cost": 500},
                "ro_hour": {"cost": 700},
                "portal_access": {"cost": 500},
            },
            "delivery": {
                "post_russia": {"cost": 350},
            },
            "overhead": {
                "base": {"cost": 2000},
            },
            "forms": {
                "full_time": {
                    "fuel_cost_per_km": 55,
                    "accommodation_per_night": 2500,
                    "daily_allowance": 500,
                }
            },
        },
        "sout": {
            "category_rates": {
                "1": {
                    "full_cost": 3500,  # Полная карта
                    "analogy_cost": 700,  # Аналогия
                    "card_cost": 500,  # Только карта
                },
                "2": {
                    "full_cost": 4500,
                    "analogy_cost": 900,
                    "card_cost": 700,
                },
            },
            "analogy_protocol_set": {"cost": 1500},
            "materials": {
                "paper_a4": {"cost": 2, "default_quantity": 100},
                "ink_per_page": {"cost": 3, "default_quantity": 100},
            },
            "labor": {
                "expert_fixed": {"cost": 3600},
                "expert_per_rm": {"cost": 360},
                "measurer_per_rm": {"cost": 200},
            },
            "delivery": {
                "post_russia": {"cost": 350},
            },
        },
        "plk": {
            "base_cost_per_point": {"cost": 41.9},
            "labor": {
                "measurer_per_point": {"cost": 150},
            },
            "materials": {
                "paper_a4": {"cost": 2, "default_quantity": 50},
                "ink_per_page": {"cost": 3, "default_quantity": 50},
            },
            "delivery": {
                "post_russia": {"cost": 350},
            },
            "subcontractor": {
                "default_cost": 5000,
            },
            "travel": {
                "transport_default": 5000,
                "accommodation_default": 2500,
                "daily_allowance": 500,
            },
        },
        "opr": {
            "materials": {
                "paper_a4": {"cost": 2, "default_quantity": 50},
                "ink_per_page": {"cost": 3, "default_quantity": 50},
            },
            "labor": {
                "sot_per_rm": {"cost": 40},
                "processing_per_rm": {"cost": 30},
                "program_per_day": {"cost": 2000},
            },
            "delivery": {
                "post_russia": {"cost": 350},
            },
        },
        "guarantees": {
            "application": {
                "bank_guarantee_cost": {
                    "ranges": [
                        {"max_contract": 100000, "real_cost": 1000},
                        {"max_contract": 500000, "real_cost": 2000},
                        {"max_contract": 1000000, "real_cost": 3000},
                        {"max_contract": 5000000, "real_cost": 5000},
                        {"max_contract": 10000000, "real_cost": 7000},
                        {"max_contract": 999999999999, "real_cost": 10000},
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
    """Результат расчёта тендера."""

    cost_price: float  # Себестоимость
    recommended_price: float  # Рекомендуемая цена (с маржой)
    margin_percent: float  # Маржа (%)
    margin_rub: float  # Маржа (руб)
    transport_cost: float  # Транспортные расходы
    subcontractor_cost: float  # Субподряд
    guarantee_cost: float  # Обеспечение (БГ)
    details: dict  # Детализация

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
    Калькулятор для всех типов тендеров.
    Поддерживает: обучение, СОУТ, ПЛК, ОПР.
    """

    def __init__(self):
        self.costs = _load_costs()
        logger.info("TenderCalculator инициализирован")

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
        days_full_time: int = 0,
        accommodation_nights: int = 0,
        transport_km: int = 0,
        venue_rent_days: int = 0,
        teacher_days: int = 0,
        teacher_rate: int = 4000,
        manikin_days: int = 0,
        delivery_count: int = 1,
        has_lamination: bool = False,
    ) -> CalculationResult:
        """
        Расчёт тендера на обучение.

        Args:
            students_count: Общее количество слушателей
            certificates: Количество удостоверений (если 0 — берётся students_count)
            diplomas: Количество дипломов специалистов
            worker_certs: Свидетельства рабочим
            qual_certs: Свидетельства повышения квалификации
            protocols_count: Протоколы по охране труда
            is_distance: Дистанционная форма (True) или очная (False)
            days_full_time: Дней очного обучения
            accommodation_nights: Ночей проживания
            transport_km: Километраж (для расчёта бензина)
            venue_rent_days: Дней аренды помещения
            teacher_days: Дней работы преподавателя
            teacher_rate: Ставка преподавателя за день
            manikin_days: Дней аренды манекена
            delivery_count: Количество отправок почтой
            has_lamination: Нужна ламинация удостоверений
        """
        edu = self.costs["education"]

        # === Документы ===
        # Если certificates не указаны — считаем по students_count
        actual_certificates = certificates if certificates > 0 else students_count

        docs_cost = (
            actual_certificates * edu["documents"]["certificate"]["cost"]
            + diplomas * edu["documents"]["diploma"]["cost"]
            + worker_certs * edu["documents"]["certificate_worker"]["cost"]
            + qual_certs * edu["documents"]["certificate_qualification"]["cost"]
        )

        # Протоколы — бесплатно (входят в стоимость)
        # Но если прописаны удостоверения к протоколам — уже учтены в certificates

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
        # Методист: 3 часа на тендер
        methodist_cost = 3 * edu["labor"]["methodist_hour"]["cost"]
        # РО: 3 часа на тендер
        ro_cost = 3 * edu["labor"]["ro_hour"]["cost"]
        portal_cost = edu["labor"]["portal_access"]["cost"]

        # === Доставка ===
        delivery_cost = delivery_count * edu["delivery"]["post_russia"]["cost"]

        # === Накладные ===
        overhead_cost = edu["overhead"]["base"]["cost"]

        # === Очная часть (если есть) ===
        full_time_cost = 0
        transport_cost = 0

        if not is_distance and days_full_time > 0:
            # Транспорт: бензин
            fuel_liters = (
                (transport_km / 100)
                * edu["forms"]["full_time"]["fuel_cost_per_km"]
                * 100
                / 55
            )  # 11л/100км
            # Или проще: (km / 100) * 11 * 55
            fuel_liters = (transport_km / 100) * 11
            transport_cost = fuel_liters * 55

            # Преподаватель
            teacher_cost = teacher_days * teacher_rate

            # Проживание
            accommodation_cost = (
                accommodation_nights
                * edu["forms"]["full_time"]["accommodation_per_night"]
            )

            # Суточные
            daily_allowance = (teacher_days + 2) * edu["forms"]["full_time"][
                "daily_allowance"
            ]  # +2 дня дороги

            # Аренда помещения
            venue_cost = venue_rent_days * 5000  # минимум из диапазона

            # Манекен
            manikin_cost = manikin_days * 2000  # минимум из диапазона

            full_time_cost = (
                teacher_cost
                + accommodation_cost
                + daily_allowance
                + venue_cost
                + manikin_cost
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

        # === Маржа 10% ===
        margin_percent = 10.0
        margin_rub = cost_price * (margin_percent / 100)
        recommended_price = cost_price + margin_rub

        # Минимум 10 000₽
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
                "days_full_time": days_full_time,
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
        transport_cost: float = 0,
        accommodation_nights: int = 0,
        expert_days: int = 1,
    ) -> CalculationResult:
        """
        Расчёт тендера СОУТ. 3 варианта расчёта.

        Args:
            rm_total: Общее количество рабочих мест
            rm_category_1: РМ 1 категории
            rm_category_2: РМ 2 категории
            rm_with_iii: РМ с ионизирующими излучениями
            variant: Вариант расчёта (1, 2, 3)
            delivery_count: Количество отправок документов
            is_annual: Годовой тендер (12 отправок)
            transport_cost: Транспортные расходы (ручной ввод)
            accommodation_nights: Ночей проживания
            expert_days: Дней работы эксперта
        """
        sout = self.costs["sout"]
        cat = sout["category_rates"]

        # === Субподряд ИИИ ===
        subcontractor_cost = 0
        if rm_with_iii > 0:
            if rm_with_iii <= 10:
                subcontractor_cost = 5000
            elif rm_with_iii <= 15:
                subcontractor_cost = 6000
            elif rm_with_iii <= 20:
                subcontractor_cost = 7000
            else:
                subcontractor_cost = 7000 + (rm_with_iii - 20) * 350  # Экстраполяция

        # === Расчёт по вариантам ===
        if variant == 1:
            # 20% основных РМ + аналогия полностью
            main_rm = max(2, int(rm_total * 0.2))  # 20%, но не менее 2
            analogy_rm = rm_total - main_rm

            cost = (
                main_rm * cat["1"]["full_cost"]  # Предполагаем 1 категорию для основных
                + analogy_rm * cat["1"]["analogy_cost"]
            )

        elif variant == 2:
            # 1 карта + остаток из аналогии
            cards_cost = (
                rm_category_1 * cat["1"]["card_cost"]
                + rm_category_2 * cat["2"]["card_cost"]
            )
            analogy_rm = rm_total - (rm_category_1 + rm_category_2)
            analogy_cost = analogy_rm * cat["1"]["analogy_cost"]
            cost = cards_cost + analogy_cost

        else:  # variant == 3
            # Кол-во карт + комплекты протоколов (20%, минимум 2)
            cards_cost = (
                rm_category_1 * cat["1"]["card_cost"]
                + rm_category_2 * cat["2"]["card_cost"]
            )
            # ИСПРАВЛЕНО: защита от отрицательного значения
            remaining_rm = max(0, rm_total - rm_category_1 - rm_category_2)
            protocol_sets = max(2, int(remaining_rm * 0.2))
            protocol_cost = protocol_sets * sout["analogy_protocol_set"]["cost"]
            cost = cards_cost + protocol_cost

        # === Материалы ===
        materials_cost = (
            sout["materials"]["paper_a4"]["cost"]
            * sout["materials"]["paper_a4"]["default_quantity"]
            + sout["materials"]["ink_per_page"]["cost"]
            * sout["materials"]["ink_per_page"]["default_quantity"]
        )

        # === Труд ===
        # Эксперт: фиксированно 3600 или по РМ
        if rm_total <= 10:
            expert_cost = sout["labor"]["expert_fixed"]["cost"]
        else:
            expert_cost = rm_total * sout["labor"]["expert_per_rm"]["cost"]

        # Замерщик
        measurer_cost = rm_total * sout["labor"]["measurer_per_rm"]["cost"]

        # === Доставка ===
        actual_delivery = 12 if is_annual else delivery_count
        delivery_cost = actual_delivery * sout["delivery"]["post_russia"]["cost"]

        # === Проживание ===
        accommodation_cost = accommodation_nights * 2500

        # === Суточные эксперта ===
        daily_allowance = expert_days * 5000

        # === Итого ===
        cost_price = (
            cost
            + materials_cost
            + expert_cost
            + measurer_cost
            + delivery_cost
            + transport_cost
            + accommodation_cost
            + daily_allowance
            + subcontractor_cost
        )

        # Маржа 10%
        margin_percent = 10.0
        margin_rub = cost_price * 0.1
        recommended_price = cost_price + margin_rub

        # Минимум 20 000₽ для СОУТ
        if recommended_price < 20000:
            recommended_price = 20000

        return CalculationResult(
            cost_price=cost_price,
            recommended_price=recommended_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            transport_cost=transport_cost,
            subcontractor_cost=subcontractor_cost,
            guarantee_cost=0,
            details={
                "type": "sout",
                "variant": variant,
                "rm_total": rm_total,
                "rm_category_1": rm_category_1,
                "rm_category_2": rm_category_2,
                "rm_with_iii": rm_with_iii,
                "main_calculation": cost,
                "materials_cost": materials_cost,
                "expert_cost": expert_cost,
                "measurer_cost": measurer_cost,
                "delivery_cost": delivery_cost,
                "accommodation_cost": accommodation_cost,
                "daily_allowance": daily_allowance,
                "is_annual": is_annual,
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
        """
        Расчёт тендера ПЛК (производственный лабораторный контроль).

        Args:
            points_count: Количество точек замеров
            factors_count: Количество наименований факторов
            delivery_count: Количество отправок документов
            is_annual: Годовой тендер (12 отправок)
            needs_subcontractor: Нужен субподряд (факторы вне аккредитации)
            transport_cost: Транспортные расходы
            accommodation_cost: Проживание
        """
        plk = self.costs["plk"]

        # === Себестоимость точек ===
        points_cost = points_count * plk["base_cost_per_point"]["cost"]

        # === Труд замерщика ===
        measurer_cost = points_count * plk["labor"]["measurer_per_point"]["cost"]

        # === Материалы ===
        materials_cost = (
            plk["materials"]["paper_a4"]["cost"]
            * plk["materials"]["paper_a4"]["default_quantity"]
            + plk["materials"]["ink_per_page"]["cost"]
            * plk["materials"]["ink_per_page"]["default_quantity"]
        )

        # === Доставка ===
        actual_delivery = 12 if is_annual else delivery_count
        delivery_cost = actual_delivery * plk["delivery"]["post_russia"]["cost"]

        # === Субподряд ===
        subcontractor_cost = (
            plk["subcontractor"]["default_cost"] if needs_subcontractor else 0
        )

        # === Транспортные и проживание ===
        travel = plk["travel"]
        if transport_cost == 0 and points_count > 0:
            transport_cost = travel["transport_default"]  # Базовая закладка
        if accommodation_cost == 0:
            accommodation_cost = travel["accommodation_default"]
        daily_allowance = travel["daily_allowance"]

        # === Итого ===
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

        # Маржа 10%
        margin_percent = 10.0
        margin_rub = cost_price * 0.1
        recommended_price = cost_price + margin_rub

        # Минимум 15 000₽ для ПЛК
        if recommended_price < 15000:
            recommended_price = 15000

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
        """
        Расчёт тендера ОПР (оценка профессиональных рисков).

        Args:
            rm_count: Количество рабочих мест
            delivery_count: Количество отправок документов
            needs_siz_norms: Нужны нормы СИЗ
            needs_dsiz_norms: Нужны нормы ДСИЗ
            needs_iot_norms: Нужны ИОТ
            transport_cost: Транспортные расходы
        """
        opr = self.costs["opr"]

        # === Материалы ===
        materials_cost = (
            opr["materials"]["paper_a4"]["cost"]
            * opr["materials"]["paper_a4"]["default_quantity"]
            + opr["materials"]["ink_per_page"]["cost"]
            * opr["materials"]["ink_per_page"]["default_quantity"]
        )

        # === Труд ===
        sot_cost = rm_count * opr["labor"]["sot_per_rm"]["cost"]
        processing_cost = rm_count * opr["labor"]["processing_per_rm"]["cost"]
        program_cost = opr["labor"]["program_per_day"]["cost"]  # 1 день программы

        # === Доп. документы ===
        additional_cost = 0
        if needs_siz_norms:
            additional_cost += rm_count * 200
        if needs_dsiz_norms:
            additional_cost += rm_count * 200
        if needs_iot_norms:
            additional_cost += rm_count * 200

        # === Доставка ===
        delivery_cost = delivery_count * 1000  # Базовая стоимость

        # === Итого ===
        cost_price = (
            materials_cost
            + sot_cost
            + processing_cost
            + program_cost
            + additional_cost
            + delivery_cost
            + transport_cost
        )

        # Маржа 30% для ОПР
        margin_percent = 30.0
        margin_rub = cost_price * 0.3
        recommended_price = cost_price + margin_rub

        # Минимум 15 000₽ для ОПР
        if recommended_price < 15000:
            recommended_price = 15000

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
        """
        Расчёт стоимости банковской гарантии.
        """
        guarantees = self.costs["guarantees"]

        if guarantee_type == "application":
            guarantee_sum = contract_sum * 0.05
        else:
            guarantee_sum = contract_sum * 0.10

        # Находим диапазон по СУММЕ КОНТРАКТА (не по guarantee_sum!)
        bg_cost = 1000
        for range_info in guarantees["application"]["bank_guarantee_cost"]["ranges"]:
            if (
                contract_sum <= range_info["max_contract"]
            ):  # ← Исправлено: contract_sum вместо guarantee_sum
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
        """
        Расчёт транспортных расходов.

        Returns:
            dict: {fuel_cost, accommodation_cost, daily_allowance, total}
        """
        travel = self.costs["travel"]

        # Бензин
        fuel_liters = (distance_km / 100) * travel["fuel"]["consumption_l_per_100km"]
        fuel_cost = fuel_liters * travel["fuel"]["price_per_liter"]

        # Или авиабилеты
        if needs_flight and flight_cost > 0:
            fuel_cost = flight_cost * 2  # Туда-обратно

        # Проживание
        accommodation_cost = (
            accommodation_nights * travel["accommodation"]["standard_per_night"]
        )

        # Суточные
        daily_allowance = expert_days * travel["daily_allowance"]["standard"]

        return {
            "fuel_cost": round(fuel_cost, 2),
            "accommodation_cost": round(accommodation_cost, 2),
            "daily_allowance": round(daily_allowance, 2),
            "total": round(fuel_cost + accommodation_cost + daily_allowance, 2),
        }
