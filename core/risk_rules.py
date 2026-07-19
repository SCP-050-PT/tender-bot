"""
core/risk_rules.py
Анализ рисков тендера по правилам из knowledge/risk_rules.yaml.
Проверяет запрещённые направления, красные флаги, принимает решение.
"""

import re
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Literal
from loguru import logger

from config.settings import settings

# === ЛЕНИВАЯ ЗАГРУЗКА ПРАВИЛ РИСКОВ ===
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
    """Встроенные правила рисков по умолчанию (fallback)."""
    return {
        "thresholds": {
            "min_margin_percent": 5.0,
            "max_cost_to_nmck_ratio": 0.95,
            "min_execution_days_large_volume": 14,
            "min_nmck": 50000,
        },
        "forbidden_directions": [
            {
                "pattern": r"лицензи[яи]\s*мчс",
                "name": "Лицензия МЧС",
                "reason": "Нет лицензии МЧС",
            },
            {
                "pattern": r"экспертиза\s*промышленной\s*безопасности",
                "name": "Экспертиза промбезопасности",
                "reason": "Требуется лицензия Ростехнадзора",
            },
            {
                "pattern": r"медицинские\s*работники",
                "name": "Медицинские работники",
                "reason": "Нет медицинской лицензии",
            },
            {
                "pattern": r"информационная\s*безопасность",
                "name": "Информационная безопасность",
                "reason": "Не наш профиль",
            },
            {
                "pattern": r"водительские\s*права",
                "name": "Водительские права",
                "reason": "Не наш профиль",
            },
            {
                "pattern": r"гражданская\s*оборона",
                "name": "Гражданская оборона",
                "reason": "Не наш профиль",
            },
            {
                "pattern": r"категорированные\s*организации",
                "name": "Категорированные организации",
                "reason": "Требуется лицензия ФСТЭК",
            },
            {
                "pattern": r"охранники\s*с\s*оружием",
                "name": "Охранники с оружием",
                "reason": "Требуется лицензия ЧОП",
            },
            {
                "pattern": r"лицензи[яи]\s*фсб",
                "name": "Лицензия ФСБ",
                "reason": "Требуется лицензия ФСБ",
            },
            {
                "pattern": r"гостайна",
                "name": "Государственная тайна",
                "reason": "Требуется допуск к гостайне",
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
                "condition": "addresses_count > 3",
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
    """
    Анализатор рисков тендера.
    Проверяет запрещённые направления, оценивает риски, принимает решение.
    """

    def __init__(self):
        self.rules = _load_risk_rules()
        self.thresholds = self.rules["thresholds"]
        self.forbidden = self.rules["forbidden_directions"]
        self.red_flags_rules = self.rules["red_flags"]
        self.decision_rules = self.rules["decision_rules"]
        logger.info("RiskAnalyzer инициализирован")

    def check_forbidden(
        self, tender_text: str, tender_type: Optional[str] = None
    ) -> list[str]:
        """
        Проверяет тендер на запрещённые направления.

        Args:
            tender_text: Полный текст тендера (ТЗ, извещение)
            tender_type: Определённый тип тендера (опционально)

        Returns:
            list: Список найденных запрещённых направлений (пусто = можно)
        """
        found = []
        if not tender_text:
            return found

        text_lower = tender_text.lower()

        for rule in self.forbidden:
            pattern = rule["pattern"]
            if re.search(pattern, text_lower, re.IGNORECASE):
                found.append(f"{rule['name']}: {rule['reason']}")
                logger.warning(f"Найдено запрещённое направление: {rule['name']}")

        # Проверка смешанных лотов
        if tender_type and "смешанный" in tender_type.lower():
            # Если есть запрещённое — отказываем целиком
            pass  # Уже проверено выше

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
        customer_complaint_rate: float = 0.0,
    ) -> list[dict]:
        """
        Проверяет красные флаги (риски, но не отказ).

        Returns:
            list: Список активированных флагов с уровнем риска
        """
        flags = []

        # Маржа ниже минимума
        if margin_percent < self.thresholds["min_margin_percent"]:
            flags.append(
                {
                    "name": "Маржа ниже минимума",
                    "level": "high",
                    "message": f"Маржа {margin_percent:.1f}% ниже {self.thresholds['min_margin_percent']}%",
                }
            )

        # Себестоимость близка к НМЦК
        if nmck > 0 and cost_price / nmck > self.thresholds["max_cost_to_nmck_ratio"]:
            flags.append(
                {
                    "name": "Себестоимость близка к НМЦК",
                    "level": "high",
                    "message": f"Себестоимость {cost_price:.0f}₽ = {(cost_price/nmck*100):.1f}% от НМЦК",
                }
            )

        # Сжатые сроки
        if (
            volume_large
            and deadline_days < self.thresholds["min_execution_days_large_volume"]
        ):
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

        # НМЦК ниже рынка (если есть данные)
        # TODO: Интеграция с историческими данными

        # Отдалённый регион
        if region_distance > 3000:
            flags.append(
                {
                    "name": "Отдалённый регион",
                    "level": "medium",
                    "message": f"Расстояние {region_distance} км — высокие транспортные",
                }
            )

        # Аренда помещения
        if venue_required:
            flags.append(
                {
                    "name": "Требуется аренда помещения",
                    "level": "low",
                    "message": "Доп.расходы 5000-15000₽/день",
                }
            )

        # Сложная логистика
        if addresses_count > 3:
            flags.append(
                {
                    "name": "Сложная логистика",
                    "level": "medium",
                    "message": f"{addresses_count} адресов в разных городах",
                }
            )

        # История жалоб заказчика
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
    ) -> Literal["рекомендуется", "не участвуем", "подано"]:
        """
        Принимает финальное решение по участию.

        Note: already_submitted пока не используется (нет интеграции с историей).
        В будущем: проверять через Google Sheets или TenderCache.

        Returns:
            str: Решение
        """
        # ИСПРАВЛЕНО: already_submitted пока не используется — нужна интеграция с БД
        # if already_submitted:
        #     return "подано"

        if not is_allowed:
            return "не участвуем"

        if margin_percent < self.thresholds["min_margin_percent"]:
            return "не участвуем"

        if risk_level == "high":
            return "не участвуем"

        if nmck < self.thresholds["min_nmck"]:
            return "не участвуем"

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
        customer_complaint_rate: float = 0.0,
        already_submitted: bool = False,
        tender_type: Optional[str] = None,
    ) -> RiskResult:
        """
        Полный анализ рисков тендера.

        Returns:
            RiskResult: Полный результат анализа
        """
        # 1. Проверка запрещённых направлений
        forbidden = self.check_forbidden(tender_text, tender_type)
        is_allowed = len(forbidden) == 0

        # 2. Проверка красных флагов
        flags_data = self.check_red_flags(
            margin_percent=margin_percent,
            cost_price=cost_price,
            nmck=nmck,
            deadline_days=deadline_days,
            volume_large=volume_large,
            region_distance=region_distance,
            venue_required=venue_required,
            addresses_count=addresses_count,
            customer_complaint_rate=customer_complaint_rate,
        )

        # 3. Определение уровня риска
        risk_level = self.determine_risk_level(flags_data)
        if not is_allowed:
            risk_level = "high"

        # 4. Принятие решения
        decision = self.make_decision(
            is_allowed=is_allowed,
            margin_percent=margin_percent,
            risk_level=risk_level,
            nmck=nmck,
            already_submitted=already_submitted,
        )

        # 5. Формирование сообщений
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
