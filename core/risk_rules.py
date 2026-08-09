"""
core/risk_rules.py
Анализ рисков тендера.
ИСПРАВЛЕНО (v6.7.3):
  - Убрано дублирование поднятия risk_level при needs_manual_review
  - GUARD margin>200%: исключение для ОПР с cost_price < 50000
  - needs_manual_review не поднимает risk с low до medium при КТРУ (confidence=1.0)
"""

import re
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from loguru import logger

import yaml


@dataclass
class RiskResult:
    decision: str
    risk_level: str
    flags: list
    comment: str
    needs_manual_review: bool = False
    review_reason: str = ""


class RiskAnalyzer:
    """Анализ рисков тендера."""

    def __init__(self):
        self.rules_path = (
            Path(__file__).resolve().parent.parent / "knowledge" / "risk_rules.yaml"
        )
        self.rules = self._load_rules()
        self.thresholds = self.rules.get("thresholds", {})
        logger.info(f"RiskAnalyzer инициализирован (v6.7.3)")

    def _load_rules(self) -> dict:
        """Загружает правила рисков из YAML."""
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                rules = yaml.safe_load(f)
            logger.info(f"✅ Правила рисков загружены: {self.rules_path}")
            return rules
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки risk_rules.yaml: {e}")
            return self._get_default_rules()

    def _get_default_rules(self) -> dict:
        """Встроенные правила по умолчанию."""
        return {
            "thresholds": {
                "min_nmck": 100000,
                "min_contract_sum": 10000,
                "min_margin_percent": 10.0,
                "max_cost_to_nmck_ratio": 0.85,
                "min_execution_days": 15,
            },
            "forbidden_directions": [],
            "red_flags": [],
        }

    def analyze(
        self,
        tender_type: str,
        nmck: float,
        cost_price: float,
        margin_percent: float,
        deadline_days: Optional[int] = None,
        region: str = "",
        has_forbidden: bool = False,
        needs_manual_review: bool = False,
        review_reason: str = "",
        llm_confidence: float = 0.0,
        quantity_source: str = "",
    ) -> RiskResult:
        """
        Анализ рисков тендера.
        v6.7.3: needs_manual_review не поднимает risk при КТРУ (confidence=1.0).
        """
        flags = []
        risk_level = "low"

        # Запрещённые направления
        if has_forbidden:
            return RiskResult(
                decision="не участвуем",
                risk_level="high",
                flags=["Запрещённое направление"],
                comment="Тендер содержит запрещённые направления",
                needs_manual_review=False,
            )

        # Красные флаги
        red_flags = self.check_red_flags(
            nmck, cost_price, margin_percent, deadline_days, tender_type
        )
        flags.extend(red_flags)

        # Определяем уровень риска
        if any(f["level"] == "high" for f in flags):
            risk_level = "high"
        elif any(f["level"] == "medium" for f in flags):
            risk_level = "medium"

        # v6.7.3-fix: needs_manual_review НЕ поднимает risk при высоком confidence и КТРУ
        if needs_manual_review and risk_level == "low":
            if llm_confidence >= 0.9 and quantity_source == "ktru":
                logger.info(
                    f"[v6.7.3-fix] needs_manual_review при КТРУ (confidence={llm_confidence}) — "
                    f"risk_level остаётся low"
                )
            else:
                risk_level = "medium"
                logger.info(
                    f"[v6.7.3] needs_manual_review поднимает risk с low до medium"
                )

        # Решение
        if risk_level == "high" or margin_percent < self.thresholds.get(
            "min_margin_percent", 10
        ):
            decision = "не участвуем"
        else:
            decision = "рекомендуется"

        # Комментарий
        comment = self._build_comment(
            tender_type,
            cost_price,
            recommended_price=None,
            margin_percent=margin_percent,
        )

        return RiskResult(
            decision=decision,
            risk_level=risk_level,
            flags=[f["message"] for f in flags],
            comment=comment,
            needs_manual_review=needs_manual_review,
            review_reason=review_reason,
        )

    def check_red_flags(
        self,
        nmck: float,
        cost_price: float,
        margin_percent: float,
        deadline_days: Optional[int],
        tender_type: str = "",
    ) -> list:
        """
        Проверка красных флагов.
        v6.7.3-fix: Для ОПР с малой себестоимостью высокая маржа не считается аномалией.
        """
        flags = []

        # Маржа ниже минимума
        min_margin = self.thresholds.get("min_margin_percent", 10)
        if margin_percent < min_margin:
            flags.append(
                {
                    "level": "high",
                    "message": f"Маржа {margin_percent:.1f}% ниже минимума {min_margin}%",
                }
            )

        # Себестоимость близка к НМЦК
        max_ratio = self.thresholds.get("max_cost_to_nmck_ratio", 0.85)
        if nmck > 0 and cost_price / nmck > max_ratio:
            flags.append(
                {
                    "level": "high",
                    "message": f"Себестоимость {cost_price:,.0f}₽ > {max_ratio*100:.0f}% от НМЦК",
                }
            )

        # НМЦК ниже себестоимости
        if cost_price > nmck and nmck > 0:
            flags.append(
                {
                    "level": "high",
                    "message": "НМЦК ниже расчётной себестоимости",
                }
            )

        # v6.7.3-fix: Маржа > 200% — аномалия, НО не для ОПР с cost_price < 50000
        max_margin = self.thresholds.get("max_margin_percent", 200.0)
        if margin_percent > max_margin:
            if tender_type and "опр" in tender_type.lower() and cost_price < 50000:
                logger.info(
                    f"[v6.7.3-fix] ОПР с себестоимостью {cost_price:,.0f}₽ — "
                    f"маржа {margin_percent:.1f}% не считаем аномалией"
                )
            else:
                flags.append(
                    {
                        "level": "high",
                        "message": f"Аномально высокая маржа: {margin_percent:.1f}%",
                    }
                )

        # Сжатые сроки
        if deadline_days is not None:
            if deadline_days < 5:
                flags.append(
                    {
                        "level": "high",
                        "message": f"Сверхсжатые сроки: {deadline_days} дней",
                    }
                )
            elif deadline_days < 15:
                flags.append(
                    {
                        "level": "medium",
                        "message": f"Сжатые сроки: {deadline_days} дней",
                    }
                )

        return flags

    def check_forbidden(self, text: str, tender_type: str = "") -> tuple:
        """Проверка запрещённых направлений."""
        forbidden = self.rules.get("forbidden_directions", [])
        for rule in forbidden:
            pattern = rule.get("pattern", "")
            if not pattern:
                continue
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    # Проверяем исключения по типу тендера
                    allow_if_types = rule.get("allow_if_types", [])
                    if tender_type.lower() in [t.lower() for t in allow_if_types]:
                        continue
                    return True, rule.get("reason", "Запрещённое направление")
            except re.error:
                logger.warning(f"Некорректный regex в forbidden: {pattern}")
                continue
        return False, ""

    def _build_comment(
        self,
        tender_type: str,
        cost_price: float,
        recommended_price: Optional[float],
        margin_percent: float,
    ) -> str:
        """Формирует комментарий к анализу."""
        lines = [f"Анализ тендера типа «{tender_type}»", ""]
        lines.append(f"Расчётная себестоимость: {cost_price:,.0f} ₽")
        if recommended_price:
            lines.append(f"Рекомендуемая цена: {recommended_price:,.0f} ₽")
        lines.append(f"Маржа: {margin_percent:.1f}%")
        return "\n".join(lines)
