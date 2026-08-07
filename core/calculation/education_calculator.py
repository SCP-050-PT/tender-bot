"""
core/calculation/education_calculator.py
Расчёт цены для клиента на обучение.
v6.7.1: Исправлено ложное needs_manual_review, логика else-ветки,
        протоколы через costs_db, llm_confidence влияет на review.
"""

import math
from typing import Optional
from dataclasses import dataclass, field
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
    needs_manual_review: bool = False
    review_reason: str = ""

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


class EducationCalculator:
    """Расчёт цены для клиента на обучение."""

    def __init__(self):
        self.costs = load_costs()["education"]
        self.rates = self.costs.get("rates", {})

    def calculate(
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
        llm_confidence: float = 0.0,  # v6.7.1: новый параметр
    ) -> CalculationResult:
        """
        Расчёт цены для клиента на обучение.
        v6.7.1: llm_confidence влияет на needs_manual_review.
        """
        # Защита от None
        students_count = students_count or 0

        # Авто-определение students_count из текста если не передан
        if students_count == 0 and tender_text:
            import re

            patterns = [
                r"(\d+)\s*слушат",
                r"(\d+)\s*чел[.\s]",
                r"(\d+)\s*человек",
                r"кол\s*[-]?\s*во\s*обучаемых[:\s]*(\d+)",
                r"обучаемых[:\s]*(\d+)",
                r"количество\s*обучаемых[:\s]*(\d+)",
            ]
            for pat in patterns:
                m = re.search(pat, tender_text.lower())
                if m:
                    students_count = int(m.group(1))
                    logger.info(
                        f"[EducationCalc] Авто-определение students_count={students_count} из текста"
                    )
                    break

        certificates = certificates or 0
        diplomas = diplomas or 0
        worker_certs = worker_certs or 0
        qual_certs = qual_certs or 0
        protocols_count = protocols_count or 0
        teacher_days = teacher_days or 0
        accommodation_nights = accommodation_nights or 0
        transport_km = transport_km or 0
        venue_rent_days = venue_rent_days or 0
        manikin_days = manikin_days or 0
        delivery_count = delivery_count or 1
        is_distance = is_distance if is_distance is not None else True
        text_lower = tender_text.lower()

        actual_certificates = certificates

        # v6.7.1: Улучшенное авто-определение документов
        needs_manual_review = False
        review_reason = ""
        auto_detected = False

        total_explicit_docs = (
            actual_certificates + diplomas + worker_certs + qual_certs + protocols_count
        )

        if total_explicit_docs == 0:
            # Ни один тип документов не передан явно — авто-определяем
            auto_detected = True

            # v6.7.1: Если llm_confidence высокий — НЕ ставим review (LLM осознанно выбрал)
            if llm_confidence >= 0.5:
                needs_manual_review = False
                review_reason = ""
                logger.info(
                    f"[EducationCalc v6.7.1] Авто-определение при llm_confidence={llm_confidence:.2f} ≥ 0.5 — review НЕ требуется"
                )
            else:
                needs_manual_review = True
                review_reason = f"Авто-определение документов для {students_count} слушателей (confidence={llm_confidence:.2f}). Требуется проверка ТЗ."

            # Расширенное авто-определение
            is_ot = (
                "охрана труда" in text_lower
                or "обучение по охране труда" in text_lower
                or "дополнительная профессиональная программа" in text_lower
                or "программа обучения" in text_lower
            )
            is_pered = (
                "переподготовка" in text_lower or "диплом специалиста" in text_lower
            )
            is_worker = (
                "рабочая профессия" in text_lower or "рабочих профессий" in text_lower
            )
            is_qual = "повышение квалификации" in text_lower
            is_height = (
                "работы на высоте" in text_lower
                or "ограниченные пространства" in text_lower
                or "газоопасные" in text_lower
            )

            # ПОРЯДОК ВАЖЕН: специфичные → общее (ОТ)
            if is_height:
                actual_certificates = students_count
                logger.info(
                    f"[EducationCalc] Авто: высота/газ → certificates={students_count}"
                )
            elif is_worker:
                worker_certs = students_count
                logger.info(
                    f"[EducationCalc] Авто: рабочая профессия → worker_certs={students_count}"
                )
            elif is_qual:
                qual_certs = students_count
                logger.info(
                    f"[EducationCalc] Авто: повышение квалификации → qual_certs={students_count}"
                )
            elif is_pered and not is_ot:
                # v6.7.1: Переподготовка = diplomas ТОЛЬКО если НЕ ОТ
                diplomas = students_count
                logger.info(
                    f"[EducationCalc] Авто: переподготовка (не ОТ) → diplomas={students_count}"
                )
            elif is_ot:
                # v6.7.1: ОТ ВСЕГДА протоколы, даже при "переподготовка"
                protocols_count = students_count
                logger.info(
                    f"[EducationCalc] Авто: ОТ → protocols_count={students_count}"
                )
            else:
                # По умолчанию — ОТ (протоколы)
                protocols_count = students_count
                logger.info(
                    f"[EducationCalc] Авто: тип неясен → protocols_count={students_count} (предполагаем ОТ)"
                )
        else:
            # Документы переданы явно — авто-определение НЕ нужно
            auto_detected = False
            needs_manual_review = False
            review_reason = ""
            logger.info(
                f"[EducationCalc v6.7.1] Документы переданы явно: "
                f"certs={actual_certificates}, diplomas={diplomas}, "
                f"worker={worker_certs}, qual={qual_certs}, protocols={protocols_count}"
            )

        # === Логирование входных параметров ===
        logger.info(
            f"[EducationCalc] ВХОД: students={students_count}, certs={actual_certificates}, "
            f"diplomas={diplomas}, worker_certs={worker_certs}, qual_certs={qual_certs}, "
            f"protocols={protocols_count}, is_distance={is_distance}, "
            f"teacher_days={teacher_days}, acc_nights={accommodation_nights}, "
            f"transport_km={transport_km}, venue_days={venue_rent_days}, "
            f"manikin_days={manikin_days}, delivery={delivery_count}, auto={auto_detected}, "
            f"needs_review={needs_manual_review}, llm_confidence={llm_confidence:.2f}"
        )

        # === Документы ===
        docs_cost = (
            actual_certificates * self.costs["documents"]["certificate"]["cost"]
            + diplomas * self.costs["documents"]["diploma"]["cost"]
            + worker_certs * self.costs["documents"]["certificate_worker"]["cost"]
            + qual_certs * self.costs["documents"]["certificate_qualification"]["cost"]
        )

        # v6.7.1: Протоколы через costs_db (protocol.cost = 3.65₽)
        protocol_docs_cost = (
            protocols_count * self.costs["documents"]["protocol"]["cost"]
        )

        # === Материалы (только для не-протокольных документов) ===
        total_non_protocol_docs = (
            actual_certificates + diplomas + worker_certs + qual_certs
        )
        paper_cost = (
            total_non_protocol_docs * self.costs["materials"]["paper_a4"]["cost"]
        )
        ink_cost = (
            total_non_protocol_docs * self.costs["materials"]["ink_per_page"]["cost"]
        )
        lamination_cost = (
            actual_certificates * self.costs["materials"]["lamination"]["cost"]
            if has_lamination
            else 0
        )

        # === Труд ===
        methodist_cost = 3 * self.costs["labor"]["methodist_hour"]["cost"]
        ro_cost = 3 * self.costs["labor"]["ro_hour"]["cost"]
        portal_cost = self.costs["labor"]["portal_access"]["cost"]

        # === Доставка ===
        delivery_cost = delivery_count * self.costs["delivery"]["post_russia"]["cost"]

        # === Накладные ===
        overhead_cost = self.costs["overhead"]["base"]["cost"]

        # === Очная часть ===
        full_time_cost, transport_cost, teacher_cost = 0, 0, 0
        accommodation_cost, daily_allowance_cost = 0, 0
        venue_cost, manikin_cost = 0, 0

        if not is_distance:
            teacher_cost, teacher_days = self._calc_teacher(
                students_count, teacher_days, teacher_rate
            )
            transport_cost = self._calc_transport(transport_km)
            accommodation_cost = self._calc_accommodation(
                accommodation_nights, teacher_days
            )
            daily_allowance_cost = (teacher_days + 2) * self.costs["forms"][
                "full_time"
            ]["daily_allowance"]
            venue_cost = self._calc_venue(venue_rent_days, teacher_days)
            manikin_cost = self._calc_manikin(manikin_days, text_lower)

            full_time_cost = (
                teacher_cost
                + accommodation_cost
                + daily_allowance_cost
                + venue_cost
                + manikin_cost
            )
            logger.info(
                f"[Education] Очные затраты: препод={teacher_cost}, проезд={transport_cost}, "
                f"прожив={accommodation_cost}, суточные={daily_allowance_cost}, "
                f"аренда={venue_cost}, манекен={manikin_cost}"
            )

        # === Итого ===
        cost_price = (
            docs_cost
            + protocol_docs_cost
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

        margin_percent = 10.0
        margin_rub = cost_price * (margin_percent / 100)
        recommended_price = cost_price + margin_rub

        # v6.4.1: Минимум 10 000₽ только для дистанционного
        if is_distance and recommended_price < 10000:
            recommended_price = 10000
            margin_rub = recommended_price - cost_price
            margin_percent = (margin_rub / cost_price) * 100 if cost_price > 0 else 0

        # === Логирование результата ===
        logger.info(
            f"[EducationCalc] РЕЗУЛЬТАТ: cost_price={cost_price:,.0f}, "
            f"recommended={recommended_price:,.0f}, margin={margin_percent:.1f}%, "
            f"docs={docs_cost:,.0f}, protocol_docs={protocol_docs_cost:,.0f}, "
            f"full_time={full_time_cost:,.0f}, transport={transport_cost:,.0f}, "
            f"needs_review={needs_manual_review}"
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
                "type": "education",
                "students_count": students_count,
                "actual_certificates": actual_certificates,
                "diplomas": diplomas,
                "worker_certs": worker_certs,
                "qual_certs": qual_certs,
                "protocols_count": protocols_count,
                "protocol_docs_cost": protocol_docs_cost,
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
                "auto_detected": auto_detected,
                "llm_confidence": llm_confidence,
            },
            needs_manual_review=needs_manual_review,
            review_reason=review_reason,
        )

    def _calc_teacher(
        self, students_count: int, teacher_days: int, teacher_rate: int
    ) -> tuple:
        """Расчёт стоимости преподавателя."""
        if teacher_days > 0:
            return teacher_days * teacher_rate, teacher_days
        if students_count > 0:
            estimated_days = max(1, math.ceil(students_count / 25))
            logger.info(
                f"[Education] Авто-оценка teacher_days={estimated_days} ({students_count} слуш.)"
            )
            return estimated_days * teacher_rate, estimated_days
        logger.info(f"[Education] teacher_days не указан → fallback 1 день")
        return teacher_rate, 1

    def _calc_transport(self, transport_km: int) -> float:
        """Расчёт транспортных расходов."""
        if transport_km > 0:
            fuel_liters = (transport_km / 100) * 11
            cost = fuel_liters * 55
            logger.info(f"[EducationCalc] Транспорт: {transport_km}km → {cost:,.0f}₽")
            return cost
        transport_fixed = self.rates.get("transport_fixed", {}).get("cost", 10000)
        logger.info(
            f"[EducationCalc] Транспорт fallback: {transport_fixed:,.0f}₽ (km=0)"
        )
        return transport_fixed

    def _calc_accommodation(
        self, accommodation_nights: int, teacher_days: int
    ) -> float:
        """Расчёт проживания."""
        nights = accommodation_nights if accommodation_nights > 0 else teacher_days
        return nights * self.costs["forms"]["full_time"]["accommodation_per_night"]

    def _calc_venue(self, venue_rent_days: int, teacher_days: int) -> float:
        """Расчёт аренды помещения."""
        days = venue_rent_days if venue_rent_days > 0 else teacher_days
        venue_daily = self.rates.get("venue_daily", {}).get("cost", 3000)
        return days * venue_daily

    def _calc_manikin(self, manikin_days: int, text_lower: str) -> float:
        """Расчёт манекена (первая помощь)."""
        if manikin_days == 0 and (
            "первая помощь" in text_lower or "манекен" in text_lower
        ):
            manikin_days = 1
            logger.info("[Education] Авто-определение manikin_days=1")
        manikin_daily = self.rates.get("manikin_daily", {}).get("cost", 15000)
        return manikin_days * manikin_daily
