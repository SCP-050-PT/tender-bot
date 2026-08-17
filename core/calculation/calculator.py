"""
core/calculation/calculator.py
Главный калькулятор — фасад для всех типов тендеров.
ИСПРАВЛЕНО (v6.9.0):
  - Убран @deprecated с calculate_guarantee() — теперь активно используется
  - Добавлен apply_global_costs(): ЭТП, обеспечение, специалист, срочность
  - Добавлен _calc_bg_cost() для банковских гарантий
  - Убран @deprecated с calculate_transport()

ИСПРАВЛЕНО (v6.9.1):
  - Добавлен apply_global_limits(): min_contract_sum, min_margin_percent, max_cost_to_nmck_ratio

ИСПРАВЛЕНО (v6.9.2):
  - FIX: apply_global_limits() — round(margin, 2) >= min_margin_percent
  - FIX: max_cost_to_nmck_ratio: 0.85 для <100K, 0.90 для >=100K
  - FIX: is_valid=True при margin == min_margin (раньше: <, теперь: round <)
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
        logger.info("TenderCalculator инициализирован (v6.9.2)")

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

    # ==================== Глобальные затраты (v6.9.0) ====================

    def apply_global_costs(
        self,
        nmck: float,
        etp_commission_percent: float = 0.0,
        application_guarantee_percent: float = 0.0,
        contract_guarantee_percent: float = 0.0,
        deadline_days: int = 30,
    ) -> dict:
        """Применяет глобальные затраты: ЭТП, обеспечение, специалист, срочность.

        Returns:
            dict с ключами: etp_commission, application_guarantee, contract_guarantee,
            specialist_cost, urgency_multiplier, urgency_note, total_extra
        """
        extra_costs = {}

        # 1. Комиссия ЭТП
        if etp_commission_percent > 0:
            etp_cost = nmck * (etp_commission_percent / 100)
            extra_costs["etp_commission"] = round(etp_cost, 2)
            logger.info(
                f"[TenderCalculator v6.9.0] ЭТП комиссия: {etp_commission_percent}% = {etp_cost:,.0f}₽"
            )
        else:
            extra_costs["etp_commission"] = 0

        # 2. Обеспечение заявки
        if application_guarantee_percent > 0:
            app_guarantee = nmck * (application_guarantee_percent / 100)
            bg_cost = self._calc_bg_cost(app_guarantee)
            extra_costs["application_guarantee"] = round(bg_cost, 2)
            logger.info(
                f"[TenderCalculator v6.9.0] Обеспечение заявки: {application_guarantee_percent}% = {app_guarantee:,.0f}₽, БГ={bg_cost:,.0f}₽"
            )
        else:
            extra_costs["application_guarantee"] = 0

        # 3. Обеспечение контракта
        if contract_guarantee_percent > 0:
            contract_guarantee = nmck * (contract_guarantee_percent / 100)
            bg_cost = self._calc_bg_cost(contract_guarantee)
            extra_costs["contract_guarantee"] = round(bg_cost, 2)
        else:
            extra_costs["contract_guarantee"] = 0

        # 4. Нагрузка специалиста (макс 3 часа × 100 ₽)
        limits = self.costs.get("global_limits", {})
        max_hours = limits.get("max_tender_preparation_hours", 3)
        rate_per_hour = limits.get("tender_specialist_rate_per_hour", 100)
        specialist_cost = max_hours * rate_per_hour
        extra_costs["specialist_cost"] = specialist_cost
        logger.info(
            f"[TenderCalculator v6.9.0] Специалист: {max_hours}ч × {rate_per_hour}₽ = {specialist_cost}₽"
        )

        # 5. Сжатые сроки
        urgency_mult = 1.0
        urgency_note = ""
        if deadline_days < 5:
            urgency_mult = 1.5
            urgency_note = "Срок < 5 дней: +50% к накладным"
        elif deadline_days < 14:
            urgency_mult = 1.2
            urgency_note = "Срок < 14 дней: +20% к накладным"
        extra_costs["urgency_multiplier"] = urgency_mult
        extra_costs["urgency_note"] = urgency_note

        total_extra = sum(
            v
            for k, v in extra_costs.items()
            if k not in ("urgency_multiplier", "urgency_note")
            and isinstance(v, (int, float))
        )
        extra_costs["total_extra"] = round(total_extra * urgency_mult, 2)

        return extra_costs

    # ==================== Глобальные лимиты (v6.9.1) ====================

    def apply_global_limits(
        self,
        cost_price: float,
        recommended_price: float,
        nmck: float,
        tender_type: str,
    ) -> dict:
        """Применяет global_limits: min_contract_sum, min_margin_percent, max_cost_to_nmck_ratio.

        v6.9.2 FIX:
        - round(margin, 2) >= min_margin_percent (раньше: точное сравнение давало 9.9 < 10.0)
        - max_cost_to_nmck_ratio: 0.85 для <100K, 0.90 для >=100K
        - is_valid=True если нет нарушений

        Returns:
            dict с ключами: is_valid, adjusted_price, adjusted_margin_percent,
            violations, review_reason, needs_manual_review, risk_level, cost_to_nmck_ratio
        """
        limits = self.costs.get("global_limits", {})
        violations = []
        review_reason = ""
        adjusted_price = recommended_price
        adjusted_margin_percent = 0.0
        needs_manual_review = False
        risk_level = "low"

        # v6.9.2: Динамический порог cost/НМЦК
        max_ratio = limits.get("max_cost_to_nmck_ratio", 0.85)
        if nmck >= 100000:
            max_ratio = 0.90  # Для крупных тендеров допускаем выше
            logger.info(
                f"[TenderCalculator v6.9.2] НМЦК >= 100K, max_ratio={max_ratio}"
            )

        cost_to_nmck_ratio = (cost_price / nmck) if nmck > 0 else 0

        # 1. min_contract_sum — минимальная цена предложения по типу
        min_sum = limits.get("min_contract_sum", 10000)
        if recommended_price < min_sum:
            adjusted_price = min_sum
            violations.append(
                f"Цена предложения {recommended_price:,.0f}₽ < min_contract_sum={min_sum:,.0f}₽"
            )
            review_reason += f"Мин. цена по ТЗ: {min_sum:,.0f}₽. "
            logger.warning(
                f"[TenderCalculator v6.9.1] GUARD: цена {recommended_price:,.0f}₽ "
                f"ниже min_contract_sum={min_sum:,.0f}₽ → поднято до {min_sum:,.0f}₽"
            )

        # 2. min_margin_percent — проверка маржи
        min_margin = limits.get("min_margin_percent", 10.0)
        if cost_price > 0:
            actual_margin = ((recommended_price - cost_price) / cost_price) * 100
            adjusted_margin_percent = actual_margin
            # v6.9.2 FIX: round до 2 знаков, иначе 9.9999% → "ниже 10%"
            if round(actual_margin, 2) < min_margin:
                adjusted = cost_price * (1 + min_margin / 100)
                adjusted_price = max(adjusted_price, adjusted)
                adjusted_margin_percent = min_margin
                violations.append(
                    f"Маржа {actual_margin:.1f}% < min_margin_percent={min_margin}%"
                )
                review_reason += (
                    f"Мин. маржа {min_margin}% (было {actual_margin:.1f}%). "
                )
                logger.warning(
                    f"[TenderCalculator v6.9.1] GUARD: маржа {actual_margin:.1f}% "
                    f"ниже min_margin_percent={min_margin}% → цена {adjusted:,.0f}₽"
                )
        else:
            adjusted_margin_percent = 0.0

        # 3. max_cost_to_nmck_ratio — guard (cost_price / НМЦК)
        if nmck > 0 and cost_to_nmck_ratio > max_ratio:
            violations.append(
                f"cost_price/НМЦК = {cost_to_nmck_ratio*100:.1f}% "
                f"превышает лимит {max_ratio*100:.0f}%"
            )
            review_reason += (
                f"Себестоимость/НМЦК = {cost_to_nmck_ratio*100:.1f}% "
                f"превышает лимит {max_ratio*100:.0f}%. РИСК: цена завышена. "
            )
            risk_level = "high"
            needs_manual_review = True
            logger.error(
                f"[TenderCalculator v6.9.1] GUARD: cost_price={cost_price:,.0f}₽ / "
                f"nmck={nmck:,.0f}₽ = {cost_to_nmck_ratio*100:.1f}% > {max_ratio*100:.0f}%"
            )

        is_valid = len(violations) == 0

        return {
            "is_valid": is_valid,
            "adjusted_price": adjusted_price,
            "adjusted_margin_percent": adjusted_margin_percent,
            "violations": violations,
            "review_reason": review_reason,
            "needs_manual_review": needs_manual_review,
            "risk_level": risk_level,
            "cost_to_nmck_ratio": cost_to_nmck_ratio,
            "max_ratio_used": max_ratio,
        }

    def _calc_bg_cost(self, guarantee_amount: float) -> float:
        """Расчёт стоимости банковской гарантии по диапазонам."""
        ranges = (
            self.costs.get("guarantees", {})
            .get("application", {})
            .get("bank_guarantee_cost", {})
            .get("ranges", [])
        )
        for r in ranges:
            if guarantee_amount <= r["max_contract"]:
                return r["real_cost"]
        return ranges[-1]["real_cost"] if ranges else 0

    # ==================== Утилиты ====================

    def calculate_guarantee(
        self, contract_sum: float, guarantee_type: str = "contract"
    ) -> float:
        """Расчёт стоимости банковской гарантии.
        v6.9.0: Восстановлен из @deprecated, теперь используется.
        """
        return self._calc_bg_cost(contract_sum)

    def calculate_transport(
        self,
        distance_km: float = 0,
        accommodation_nights: int = 0,
        expert_days: int = 0,
    ) -> dict:
        """Расчёт транспортных расходов (для справки)."""
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
