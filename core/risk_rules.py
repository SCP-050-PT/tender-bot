"""
core/risk_rules.py
Анализ рисков тендера.
ИСПРАВЛЕНО (29.07.2026 v6.5):
  - Добавлен guard: recommended_price > nmck → HIGH risk
  - Добавлен guard: margin_percent > 200% → HIGH risk
  - Добавлен guard: margin_percent > 100% → MEDIUM risk
  - Убран парадоксальный флаг "НМЦК сильно выше расчётной"
  - margin > 30% — НЕ считаем риском (это хорошо)
"""

import re
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Literal
from loguru import logger

from config.settings import settings

RISK_RULES: Optional[dict] = None


def _load_risk_rules() -> dict:
    """Ленивая загрузка правил рисков."""
    global RISK_RULES
    if RISK_RULES is not None:
        return RISK_RULES

    risk_rules_path = (
        Path(__file__).resolve().parent.parent / "knowledge" / "risk_rules.yaml"
    )

    if risk_rules_path.exists():
        try:
            with open(risk_rules_path, "r", encoding="utf-8") as f:
                RISK_RULES = yaml.safe_load(f)
            logger.info(f"✅ Правила рисков загружены: {risk_rules_path}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки risk_rules.yaml: {e}")
            RISK_RULES = _get_default_risk_rules()
    else:
        logger.warning(f"⚠️ Файл risk_rules.yaml не найден: {risk_rules_path}")
        logger.warning(f"⚠️ Используются правила по умолчанию")
        RISK_RULES = _get_default_risk_rules()

    return RISK_RULES


def _get_default_risk_rules() -> dict:
    """Встроенные правила рисков по умолчанию."""
    return {
        "thresholds": {
            "min_margin_percent": 10.0,
            "max_cost_to_nmck_ratio": 0.85,
            "min_execution_days": 15,
            "min_execution_days_large_volume": 30,
            "min_nmck": 100000,
            "min_nmck_by_type": {
                "education": 10000,
                "sout": 20000,
                "plk": 15000,
                "opr": 15000,
                "combined": 20000,
            },
            "min_margin_by_type": {
                "education": 10.0,
                "sout": 10.0,
                "plk": 10.0,
                "opr": 30.0,
                "combined": 10.0,
            },
            # ← v6.5: Новые пороги для аномально высокой маржи
            "max_margin_percent": 200.0,  # Выше = HIGH risk
            "high_margin_percent": 100.0,  # Выше = MEDIUM risk
        },
        "forbidden_directions": [
            {
                "pattern": r"\bлицензи[яи]\s+МЧС\b",
                "name": "Лицензия МЧС",
                "reason": "Нет лицензии МЧС",
                "allow_if_types": [],
            },
            {
                "pattern": r"экспертиз[аыуеой]?\s+промышленной\s+безопасности",
                "name": "Экспертиза промбезопасности",
                "reason": "Требуется лицензия Ростехнадзора",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bпромбезопасност[иь]\b",
                "name": "Промбезопасность",
                "reason": "Требуется лицензия Ростехнадзора",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bмедицинские\s+работники\b",
                "name": "Медицинские работники",
                "reason": "Нет медицинской лицензии",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bинформационная\s+безопасность\b",
                "name": "Информационная безопасность",
                "reason": "Не наш профиль",
                "allow_if_types": ["sout", "opr", "соут", "опр"],
            },
            {
                "pattern": r"\bводительские\s+права\b",
                "name": "Водительские права",
                "reason": "Не наш профиль",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bгражданская\s+оборона\b",
                "name": "Гражданская оборона",
                "reason": "Не наш профиль",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bкатегорированные\s+организации\b",
                "name": "Категорированные организации",
                "reason": "Требуется лицензия ФСТЭК",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bохранники\s+с\s+оружием\b",
                "name": "Охранники с оружием",
                "reason": "Требуется лицензия ЧОП",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bлицензи[яи]\s+ФСБ\b",
                "name": "Лицензия ФСБ",
                "reason": "Требуется лицензия ФСБ",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bгостайна\b",
                "name": "Государственная тайна",
                "reason": "Требуется допуск к гостайне",
                "allow_if_types": [],
            },
            {
                "pattern": r"\bтехническая\s+диагностика\b",
                "name": "Техническая диагностика",
                "reason": "Требуется лицензия Ростехнадзора",
                "allow_if_types": [],
            },
            {
                "pattern": r"исследован[ио]\s*вод[ыу]|питьевая\s+вода|смыв[ыы]|гельминт[ыы]|биологи[яю]|микробиологи[яю]",
                "name": "Вода/смывы/биология",
                "reason": "Нет аккредитации",
                "allow_if_types": [],
            },
        ],
        "red_flags": [
            {
                "name": "Маржа ниже минимума",
                "condition": "margin_percent < min_margin_percent",
                "level": "high",
            },
            {
                "name": "Себестоимость близка к НМЦК",
                "condition": "cost_price / nmck > max_cost_to_nmck_ratio",
                "level": "high",
            },
            {
                "name": "Сжатые сроки",
                "condition": "volume_large and deadline_days < min_execution_days_large_volume",
                "level": "medium",
            },
            {
                "name": "Сверхсжатые сроки",
                "condition": "deadline_days < 5",
                "level": "high",
            },
            {
                "name": "Отдалённый регион",
                "condition": "region_distance > 3000",
                "level": "medium",
            },
            {
                "name": "Требуется аренда помещения",
                "condition": "venue_required",
                "level": "low",
            },
            {
                "name": "Сложная логистика",
                "condition": "cities_count > 3",
                "level": "medium",
            },
            {
                "name": "Требуется ручная проверка",
                "condition": "needs_manual_review",
                "level": "medium",
            },
            {
                "name": "Низкая уверенность ИИ",
                "condition": "llm_confidence < 0.3",
                "level": "medium",
            },
            # ← v6.5: НОВЫЕ GUARD'ы
            {
                "name": "Рекомендуемая цена превышает НМЦК",
                "condition": "recommended_price > nmck",
                "level": "high",
            },
            {
                "name": "Аномально высокая маржа",
                "condition": "margin_percent > max_margin_percent",
                "level": "high",
            },
            {
                "name": "Высокая маржа — проверьте расчёт",
                "condition": "margin_percent > high_margin_percent",
                "level": "medium",
            },
        ],
        "decision_rules": [
            {
                "condition": "not is_allowed",
                "decision": "не участвуем",
            },
            {
                "condition": "margin_percent < min_margin_percent",
                "decision": "не участвуем",
            },
            {
                "condition": "risk_level == high",
                "decision": "не участвуем",
            },
            {
                "condition": "nmck < min_nmck",
                "decision": "не участвуем",
            },
            {
                "condition": "already_submitted",
                "decision": "подано",
            },
        ],
    }


@dataclass
class RiskResult:
    """Результат анализа рисков."""

    decision: Literal["рекомендуется", "не участвуем", "подано"]
    risk_level: Literal["low", "medium", "high"]
    red_flags: list[str]
    forbidden_found: list[str]
    is_allowed: bool

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "red_flags": self.red_flags,
            "forbidden_found": self.forbidden_found,
            "is_allowed": self.is_allowed,
        }


class RiskAnalyzer:
    """Анализатор рисков тендера."""

    def __init__(self):
        self.rules = _load_risk_rules()
        self.thresholds = self.rules["thresholds"]
        self.forbidden = self.rules["forbidden_directions"]
        self.red_flags_rules = self.rules["red_flags"]
        self.decision_rules = self.rules["decision_rules"]
        logger.info("RiskAnalyzer инициализирован (v6.5)")

    def check_forbidden(
        self, tender_text: str, tender_type: Optional[str] = None
    ) -> list[str]:
        found = []
        if not tender_text:
            return found

        text_lower = tender_text.lower()
        tender_type_lower = (tender_type or "").lower()

        for rule in self.forbidden:
            pattern = rule["pattern"]
            try:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    allowed_types = rule.get("allow_if_types", [])
                    if allowed_types and tender_type_lower in allowed_types:
                        name = rule["name"]
                        logger.info(
                            'Пропущено запрещённое направление "%s" (тип тендера: %s)',
                            name,
                            tender_type,
                        )
                        continue

                    name = rule["name"]
                    reason = rule["reason"]
                    found.append(name + ": " + reason)
                    logger.warning("Найдено запрещённое направление: %s", name)
            except re.error as e:
                name = rule["name"]
                logger.error("Ошибка regex в правиле %s: %s", name, e)

        return found

    def check_red_flags(
        self,
        margin_percent: float,
        cost_price: float,
        nmck: float,
        deadline_days: int,
        volume_large: bool = False,
        region_distance: int = 0,
        venue_required: bool = False,
        addresses_count: int = 1,
        cities_count: int = 1,
        customer_complaint_rate: float = 0.0,
        needs_manual_review: bool = False,
        llm_confidence: float = 1.0,
        tender_type: Optional[str] = None,
        recommended_price: float = 0,  # ← v6.5
    ) -> list[dict]:
        """Проверяет красные флаги (риски, но не отказ)."""
        flags = []
        # Округляем маржу для избежания float-ошибок
        margin_percent = round(margin_percent + 0.001, 2)  # ← v6.6-r4: epsilon защита от float-ошибок
        # ← v6.5: GUARD — цена превышает НМЦК
        if nmck > 0 and recommended_price > nmck:
            flags.append(
                {
                    "name": "Рекомендуемая цена превышает НМЦК",
                    "level": "high",
                    "message": f"Рекомендуемая цена {recommended_price:,.0f}₽ превышает НМЦК {nmck:,.0f}₽ — заявка будет отклонена",
                }
            )

        # ← v6.5: GUARD — аномально высокая маржа
        if margin_percent > self.thresholds.get("max_margin_percent", 200.0):
            flags.append(
                {
                    "name": "Аномально высокая маржа",
                    "level": "high",
                    "message": f"Маржа {margin_percent:.1f}% аномально высокая (порог {self.thresholds.get('max_margin_percent', 200)}%) — проверьте количество и вариант расчёта",
                }
            )
        elif margin_percent > self.thresholds.get("high_margin_percent", 100.0):
            flags.append(
                {
                    "name": "Высокая маржа — проверьте расчёт",
                    "level": "medium",
                    "message": f"Маржа {margin_percent:.1f}% выше нормы (порог {self.thresholds.get('high_margin_percent', 100)}%) — проверьте корректность",
                }
            )

        # ← v6.5: НОВАЯ ЛОГИКА НМЦК vs себестоимость
        if cost_price > 0 and nmck > 0:
            actual_margin_pct = ((nmck - cost_price) / cost_price) * 100
            cost_to_nmck_ratio = cost_price / nmck

            # Проверяем: НМЦК ниже себестоимости (это реальный риск)
            if cost_price > nmck * 1.2:
                flags.append(
                    {
                        "name": "НМЦК ниже рынка",
                        "level": "medium",
                        "message": f"Себестоимость {cost_price:,.0f}₽ на {(cost_price/nmck - 1)*100:.0f}% выше НМЦК {nmck:,.0f}₽ — возможно договорная закупка",
                    }
                )

            # НМЦК выше себестоимости — НЕ риск, если маржа хорошая
            elif nmck > cost_price * 4:
                is_sout = tender_type and tender_type.lower() in (
                    "sout",
                    "соут",
                    "combined",
                    "комбинированный",
                )

                if is_sout and cost_to_nmck_ratio < 0.25:
                    logger.info(
                        f"[v6.5] СОУТ: НМЦК {nmck:,.0f}₽, себестоимость {cost_price:,.0f}₽ "
                        f"(маржа {actual_margin_pct:.0f}%) — НЕ риск"
                    )
                elif actual_margin_pct > 30:
                    logger.info(
                        f"[v6.5] Хорошая маржа {actual_margin_pct:.1f}% — не считаем риском"
                    )
                else:
                    flags.append(
                        {
                            "name": "НМЦК значительно выше себестоимости",
                            "level": "medium",
                            "message": f"НМЦК {nmck:,.0f}₽ в {nmck/cost_price:.1f} раза выше себестоимости {cost_price:,.0f}₽ (маржа {actual_margin_pct:.1f}%) — проверьте корректность расчёта",
                        }
                    )

            # НМЦК на грани себестоимости (маржа < 10%)
            elif cost_to_nmck_ratio > 0.85:
                flags.append(
                    {
                        "name": "НМЦК на грани себестоимости",
                        "level": "high",
                        "message": f"Себестоимость {cost_price:,.0f}₽ = {cost_to_nmck_ratio*100:.1f}% от НМЦК {nmck:,.0f}₽ — минимальная маржа",
                    }
                )

        if margin_percent < self.thresholds["min_margin_percent"]:
            flags.append(
                {
                    "name": "Маржа ниже минимума",
                    "level": "high",
                    "message": f"Маржа {margin_percent:.1f}% ниже {self.thresholds['min_margin_percent']}%",
                }
            )

        if nmck > 0 and cost_price / nmck > self.thresholds["max_cost_to_nmck_ratio"]:
            flags.append(
                {
                    "name": "Себестоимость близка к НМЦК",
                    "level": "high",
                    "message": f"Себестоимость {cost_price:.0f}₽ = {(cost_price/nmck*100):.1f}% от НМЦК",
                }
            )

            # ← v6.7-fix: min_days_large инициализируем ДО проверки deadline_days
        min_days_large = self.thresholds.get("min_execution_days_large_volume", 30)
        if tender_type and tender_type.lower() in ("education", "обучение"):
            min_days_large = 7

        if deadline_days and deadline_days > 0:
            if volume_large and deadline_days < min_days_large:
                flags.append(
                    {
                        "name": "Сжатые сроки",
                        "level": "medium",
                        "message": f"Срок {deadline_days} дней при большом объёме",
                    }
                )
            elif deadline_days < 5:
                flags.append(
                    {
                        "name": "Сверхсжатые сроки",
                        "level": "high",
                        "message": f"Срок исполнения {deadline_days} дней — критический риск",
                    }
                )
        else:
            flags.append(
                {
                    "name": "Сроки исполнения не определены",
                    "level": "low",
                    "message": "Нет данных о сроках исполнения (требуется детальный парсинг ТЗ)",
                }
            )

        if region_distance > 3000:
            flags.append(
                {
                    "name": "Отдалённый регион",
                    "level": "medium",
                    "message": f"Расстояние {region_distance} км — высокие транспортные",
                }
            )

        if venue_required:
            flags.append(
                {
                    "name": "Требуется аренда помещения",
                    "level": "low",
                    "message": "Доп.расходы 5000-15000₽/день",
                }
            )

        if cities_count > 3:
            flags.append(
                {
                    "name": "Сложная логистика",
                    "level": "medium",
                    "message": f"{cities_count} городов — сложная логистика",
                }
            )

        if needs_manual_review:
            flags.append(
                {
                    "name": "Требуется ручная проверка",
                    "level": "medium",
                    "message": "Данные ненадёжны — требуется ручная проверка ТЗ",
                }
            )

        if llm_confidence < 0.3:
            flags.append(
                {
                    "name": "Низкая уверенность ИИ",
                    "level": "medium",
                    "message": f"Уверенность ИИ {llm_confidence:.2f} — данные могут быть неточными",
                }
            )

        if customer_complaint_rate > 0.3:
            flags.append(
                {
                    "name": "История жалоб заказчика",
                    "level": "medium",
                    "message": f"Частота жалоб {customer_complaint_rate*100:.0f}%",
                }
            )

        return flags

    def determine_risk_level(
        self, flags: list[dict]
    ) -> Literal["low", "medium", "high"]:
        """Определяет общий уровень риска по флагам."""
        if not flags:
            return "low"

        levels = [f["level"] for f in flags]
        if "high" in levels:
            return "high"
        elif "medium" in levels:
            return "medium"
        return "low"

    def make_decision(
        self,
        is_allowed: bool,
        margin_percent: float,
        risk_level: str,
        nmck: float,
        already_submitted: bool = False,
        needs_manual_review: bool = False,
        tender_type: Optional[str] = None,
    ) -> Literal["рекомендуется", "не участвуем", "подано"]:
        """Принимает финальное решение по участию."""
        if already_submitted:
            return "подано"

        if not is_allowed:
            return "не участвуем"

        min_margin = self.thresholds.get("min_margin_percent", 10.0)
        if tender_type:
            type_margins = self.thresholds.get("min_margin_by_type", {})
            min_margin = type_margins.get(tender_type.lower(), min_margin)

        margin_percent = round(
            margin_percent + 0.001, 2
        )  # ← v6.6-r4: epsilon защита от float-ошибок
        if margin_percent < min_margin:
            return "не участвуем"

        if risk_level == "high":
            return "не участвуем"

        min_nmck = self.thresholds.get("min_nmck", 100000)
        if tender_type:
            type_nmcks = self.thresholds.get("min_nmck_by_type", {})
            min_nmck = type_nmcks.get(tender_type.lower(), min_nmck)

        if nmck < min_nmck:
            return "не участвуем"

        if needs_manual_review and risk_level == "low":
            risk_level = "medium"

        return "рекомендуется"

    def analyze(
        self,
        tender_text: str,
        margin_percent: float,
        cost_price: float,
        nmck: float,
        deadline_days: int = 30,
        volume_large: bool = False,
        region_distance: int = 0,
        venue_required: bool = False,
        addresses_count: int = 1,
        cities_count: int = 1,
        customer_complaint_rate: float = 0.0,
        already_submitted: bool = False,
        tender_type: Optional[str] = None,
        needs_manual_review: bool = False,
        llm_confidence: float = 1.0,
        recommended_price: float = 0,  # ← v6.5
    ) -> RiskResult:
        """Полный анализ рисков тендера."""
        forbidden = self.check_forbidden(tender_text, tender_type)
        is_allowed = len(forbidden) == 0

        flags_data = self.check_red_flags(
            margin_percent=margin_percent,
            cost_price=cost_price,
            nmck=nmck,
            deadline_days=deadline_days,
            volume_large=volume_large,
            region_distance=region_distance,
            venue_required=venue_required,
            addresses_count=addresses_count,
            cities_count=cities_count,
            customer_complaint_rate=customer_complaint_rate,
            needs_manual_review=needs_manual_review,
            llm_confidence=llm_confidence,
            tender_type=tender_type,
            recommended_price=recommended_price,  # ← v6.5
        )

        risk_level = self.determine_risk_level(flags_data)
        if not is_allowed:
            risk_level = "high"

        if needs_manual_review and risk_level == "low":
            risk_level = "medium"

        decision = self.make_decision(
            is_allowed=is_allowed,
            margin_percent=margin_percent,
            risk_level=risk_level,
            nmck=nmck,
            already_submitted=already_submitted,
            needs_manual_review=needs_manual_review,
            tender_type=tender_type,
        )

        red_flags = [f["message"] for f in flags_data]

        logger.info(
            f"Анализ рисков: decision={decision}, risk={risk_level}, flags={len(red_flags)}"
        )

        return RiskResult(
            decision=decision,
            risk_level=risk_level,
            red_flags=red_flags,
            forbidden_found=forbidden,
            is_allowed=is_allowed,
        )
