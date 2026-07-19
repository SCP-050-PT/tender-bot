"""
core/analyzer.py
Главный анализатор тендера.
"""

import json
from typing import Optional
from dataclasses import dataclass
from loguru import logger

from config.settings import settings
from core.calculator import TenderCalculator
from core.risk_rules import RiskAnalyzer

# Ленивая загрузка промпта (чтобы не падать при импорте)
_system_prompt = None


def get_system_prompt() -> str:
    """Ленивая загрузка системного промпта."""
    global _system_prompt
    if _system_prompt is None:
        try:
            from config.prompts import load_system_prompt

            _system_prompt = load_system_prompt()
        except Exception as e:
            logger.warning(f"Не удалось загрузить системный промпт: {e}")
            _system_prompt = ""  # Пустой промпт — будет использоваться fallback
    return _system_prompt


@dataclass
class TenderAnalysis:
    """Полный результат анализа тендера."""

    tender_id: str
    tender_type: str
    nmck: float
    calculated_price: float
    recommended_price: float
    margin_percent: float
    margin_rub: float
    cost_price: float
    risk_level: str
    decision: str
    comment: str
    red_flags: list
    transport_cost: float
    subcontractor_cost: float
    guarantee_cost: float
    details: dict
    raw_llm_response: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "tender_id": self.tender_id,
            "tender_type": self.tender_type,
            "nmck": self.nmck,
            "calculated_price": round(self.calculated_price, 2),
            "recommended_price": round(self.recommended_price, 2),
            "margin_percent": round(self.margin_percent, 2),
            "margin_rub": round(self.margin_rub, 2),
            "cost_price": round(self.cost_price, 2),
            "risk_level": self.risk_level,
            "decision": self.decision,
            "comment": self.comment,
            "red_flags": self.red_flags,
            "transport_cost": round(self.transport_cost, 2),
            "subcontractor_cost": round(self.subcontractor_cost, 2),
            "guarantee_cost": round(self.guarantee_cost, 2),
            "details": self.details,
        }

    def to_sheets_row(self, law_type: str = "44") -> dict:
        """Форматирует результат для записи в Google Sheets."""
        # ИСПРАВЛЕНО: URL зависит от закона
        if law_type == "223":
            tender_url = f"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={self.tender_id}"
        else:
            tender_url = f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={self.tender_id}"

        return {
            "Наименование услуг": self.details.get("service_name", "Не определено"),
            "Количество": self.details.get("quantity", 1),
            "Способ проведения закупки": self.details.get("procurement_method", ""),
            "НМЦК": self.nmck,
            "Ссылка на тендер": tender_url,
            "ЭТП": self.details.get("etp", ""),
            "Регион": self.details.get("region", ""),
            "Обеспечение заявки": self.details.get("application_guarantee", ""),
            "Обеспечение контракта": self.details.get("contract_guarantee", ""),
            "Срок подачи заявки до": self.details.get("deadline_date", ""),
            "Решение по участию": self.decision,
            "Цена предложения": self.recommended_price,
            "Результат": "",
            "Комментарии руководителя отдела по участию": self._format_comment(),
        }

    def _format_comment(self) -> str:
        """Форматирует подробный комментарий."""
        lines = [
            f"Тип: {self.tender_type}",
            f"НМЦК: {self.nmck:,.0f} ₽",
            f"Расчётная цена: {self.calculated_price:,.0f} ₽",
            f"Рекомендуемая цена: {self.recommended_price:,.0f} ₽",
            f"Маржа: {self.margin_percent:.1f}% ({self.margin_rub:,.0f} ₽)",
            f"Себестоимость: {self.cost_price:,.0f} ₽",
            f"Уровень риска: {self.risk_level}",
            f"Решение: {self.decision}",
            "",
            "Риски и флаги:",
        ]
        for flag in self.red_flags:
            lines.append(f"• {flag}")

        lines.extend(
            [
                "",
                f"Транспортные: {self.transport_cost:,.0f} ₽",
                f"Субподряд: {self.subcontractor_cost:,.0f} ₽",
                f"Обеспечение: {self.guarantee_cost:,.0f} ₽",
            ]
        )

        return "\n".join(lines)


class TenderAnalyzer:
    """Главный анализатор тендера."""

    def __init__(self, llm_client=None, calculator=None, risk_analyzer=None):
        # Ленивая инициализация LLM (не падаем если нет API-ключа)
        self._llm = llm_client
        self.calc = calculator or TenderCalculator()
        self.risk = risk_analyzer or RiskAnalyzer()
        logger.info("TenderAnalyzer инициализирован")

    @property
    def llm(self):
        """Ленивая инициализация LLM-клиента."""
        if self._llm is None:
            try:
                from utils.llm_client import YandexGPTClient

                self._llm = YandexGPTClient()
            except Exception as e:
                logger.warning(f"LLM недоступен: {e}")
                self._llm = False  # Флаг что LLM не работает
        return self._llm

    def _call_llm(self, tender_text: str, tender_info: dict) -> Optional[dict]:
        """
        Вызывает LLM для анализа текста тендера.
        ИСПРАВЛЕНО: использует send() вместо несуществующего analyze_tender()
        """
        if not self.llm or self.llm is False:
            return None

        system_prompt = f"""Ты — аналитик тендеров компании "АС Безопасности".
Проанализируй текст документов закупки и извлеки ключевые параметры для расчёта стоимости.

Информация о закупке:
- Название: {tender_info.get("purchase_name", "")}
- Заказчик: {tender_info.get("customer_name", "")}
- Регион: {tender_info.get("customer_region", "")}
- НМЦК: {tender_info.get("nmck", "")}
- Срок подачи заявок: {tender_info.get("deadline_date", "")}

Верни СТРОГО JSON без markdown:
{{
  "tender_type": "sout|education|plk|opr|combined|unknown",
  "confidence": 0.0,
  "students_count": 0,
  "certificates": 0,
  "is_distance": true,
  "rm_total": 0,
  "rm_category_1": 0,
  "rm_category_2": 0,
  "iii_count": 0,
  "points_count": 0,
  "factors_count": 0,
  "delivery_count": 1,
  "is_annual": false,
  "needs_siz_norms": false,
  "needs_dsiz_norms": false,
  "needs_iot_norms": false,
  "needs_subcontractor": false,
  "deadline_days": 0,
  "addresses_count": 1,
  "has_venue": false,
  "urgency": "normal|high|critical",
  "special_requirements": [],
  "red_flags": [],
  "notes": ""
}}"""

        try:
            # ИСПРАВЛЕНО: используем send() вместо analyze_tender()
            result = self.llm.send(
                system_prompt=system_prompt,
                user_message=tender_text[:15000],
                temperature=0.1,
                max_tokens=2000,
            )
            # Парсим JSON из ответа
            if isinstance(result, str):
                import json

                # Убираем markdown-обёртку если есть
                text = result.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                return json.loads(text)
            elif isinstance(result, dict):
                return result
            return None
        except Exception as e:
            logger.error(f"Ошибка LLM-анализа: {e}")
            return None

    def _extract_params_from_text(self, tender_text: str) -> dict:
        """
        Fallback: извлекает параметры из текста без LLM.
        Упрощённый парсинг ключевых слов.
        """
        text_lower = tender_text.lower()
        params = {
            "tender_type": "unknown",
            "students_count": 0,
            "rm_total": 0,
            "points_count": 0,
            "delivery_count": 1,
            "is_annual": False,
            "is_distance": True,
        }

        # Определение типа
        if "соут" in text_lower or "специальная оценка" in text_lower:
            params["tender_type"] = "sout"
        elif "обучение" in text_lower or "повышение квалификации" in text_lower:
            params["tender_type"] = "education"
        elif "плк" in text_lower or "лабораторный контроль" in text_lower:
            params["tender_type"] = "plk"
        elif "опр" in text_lower or "профессиональных рисков" in text_lower:
            params["tender_type"] = "opr"

        # Извлечение чисел
        import re

        # Количество слушателей / РМ / точек
        rm_match = re.search(r"(\d+)\s*рабочих\s*мест", text_lower)
        if rm_match:
            params["rm_total"] = int(rm_match.group(1))

        students_match = re.search(r"(\d+)\s*слушател", text_lower)
        if students_match:
            params["students_count"] = int(students_match.group(1))

        points_match = re.search(r"(\d+)\s*точек", text_lower)
        if points_match:
            params["points_count"] = int(points_match.group(1))

        return params

    def analyze(
        self,
        tender_text: str,
        tender_id: str = None,
        nmck: float = None,
        region: str = None,
        procurement_method: str = None,
        etp: str = None,
        deadline_date: str = None,
        law_type: str = "44",
        tender_info: dict = None,
    ):
        """Полный анализ тендера."""
        logger.info(f"Начинаю анализ тендера {tender_id or 'N/A'}")

        tender_info = tender_info or {}

        # === Шаг 1: LLM-анализ (если доступен) ===
        llm_result = None
        if self.llm and self.llm is not False:
            try:
                llm_result = self._call_llm(tender_text, tender_info)
            except Exception as e:
                logger.warning(f"LLM-анализ не удался: {e}")

        # === Шаг 2: Извлечение параметров ===
        if (
            llm_result
            and isinstance(llm_result, dict)
            and "parse_error" not in llm_result
        ):
            tender_type = llm_result.get("tender_type", "unknown")
            details = self._normalize_llm_params(llm_result)
        else:
            # Fallback — парсим текст вручную
            details = self._extract_params_from_text(tender_text)
            tender_type = details.get("tender_type", "unknown")

        # НМЦК: приоритет — переданный параметр, затем из LLM
        actual_nmck = nmck or 0
        if llm_result and isinstance(llm_result, dict):
            actual_nmck = actual_nmck or llm_result.get("nmck", 0)

        # === Шаг 3: Расчёт цены ===
        calc_result = self._calculate_by_type(tender_type, details)

        # === Шаг 4: Расчёт обеспечения ===
        guarantee_cost = 0
        if actual_nmck > 0:
            try:
                guarantee_cost = self.calc.calculate_guarantee(
                    contract_sum=actual_nmck, guarantee_type="application"
                )
            except Exception as e:
                logger.warning(f"Не удалось рассчитать обеспечение: {e}")

        # === Шаг 5: Анализ рисков ===
        risk_result = self.risk.analyze(
            tender_text=tender_text,
            margin_percent=calc_result.margin_percent,
            cost_price=calc_result.cost_price,
            nmck=actual_nmck or 100000,
            deadline_days=details.get("deadline_days", 30),
            volume_large=details.get("quantity_rm", 0) > 50,
            region_distance=0,
            venue_required=details.get("has_venue", False),
            addresses_count=details.get("addresses_count", 1),
            tender_type=tender_type,
        )

        # === Шаг 6: Формирование результата ===
        llm_price = 0
        if llm_result and isinstance(llm_result, dict):
            llm_price = llm_result.get("recommended_price", 0)

        final_price = (
            max(llm_price, calc_result.recommended_price)
            if llm_price > 0
            else calc_result.recommended_price
        )

        margin_rub = final_price - calc_result.cost_price
        margin_percent = (
            (margin_rub / calc_result.cost_price * 100)
            if calc_result.cost_price > 0
            else 0
        )

        comment = ""
        if llm_result and isinstance(llm_result, dict):
            comment = llm_result.get("comment", "")
        if not comment:
            comment = self._generate_comment(
                tender_type, calc_result, risk_result, details
            )

        return TenderAnalysis(
            tender_id=tender_id or "unknown",
            tender_type=tender_type,
            nmck=actual_nmck or 0,
            calculated_price=calc_result.recommended_price,
            recommended_price=final_price,
            margin_percent=margin_percent,
            margin_rub=margin_rub,
            cost_price=calc_result.cost_price,
            risk_level=risk_result.risk_level,
            decision=risk_result.decision,
            comment=comment,
            red_flags=risk_result.red_flags
            + (
                llm_result.get("red_flags", [])
                if llm_result and isinstance(llm_result, dict)
                else []
            ),
            transport_cost=calc_result.transport_cost,
            subcontractor_cost=calc_result.subcontractor_cost,
            guarantee_cost=guarantee_cost,
            details={
                **details,
                "service_name": tender_type,
                "procurement_method": procurement_method or "",
                "tender_url": (
                    f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_id}"
                    if tender_id and law_type == "44"
                    else (
                        f"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={tender_id}"
                        if tender_id and law_type == "223"
                        else ""
                    )
                ),
                "etp": etp or "",
                "region": region or "",
                "deadline_date": deadline_date or "",
                "quantity": details.get(
                    "rm_total",
                    details.get("students_count", details.get("points_count", 1)),
                ),
            },
            raw_llm_response=llm_result,
        )

    def _normalize_llm_params(self, llm_result: dict) -> dict:
        """
        Нормализует параметры из LLM в формат, понятный calculator.py.
        ИСПРАВЛЕНО: унификация имён ключей.
        """
        return {
            # Обучение
            "students_count": llm_result.get("students_count", 0),
            "certificates": llm_result.get("certificates", 0),
            "is_distance": llm_result.get("is_distance", True),
            "delivery_count": llm_result.get("delivery_count", 1),
            # СОУТ
            "rm_total": llm_result.get("rm_total", 0),
            "rm_category_1": llm_result.get("rm_category_1", 0),
            "rm_category_2": llm_result.get("rm_category_2", 0),
            "iii_count": llm_result.get(
                "iii_count", 0
            ),  # ИСПРАВЛЕНО: iii_count вместо has_iii
            "is_annual": llm_result.get("is_annual", False),
            # ПЛК
            "points_count": llm_result.get("points_count", 0),
            "factors_count": llm_result.get("factors_count", 0),
            "needs_subcontractor": llm_result.get("needs_subcontractor", False),
            # ОПР
            "needs_siz_norms": llm_result.get("needs_siz_norms", False),
            "needs_dsiz_norms": llm_result.get("needs_dsiz_norms", False),
            "needs_iot_norms": llm_result.get("needs_iot_norms", False),
            # Общее
            "deadline_days": llm_result.get("deadline_days", 30),
            "addresses_count": llm_result.get("addresses_count", 1),
            "has_venue": llm_result.get("has_venue", False),
        }

    def _calculate_by_type(self, tender_type: str, details: dict):
        """Выбирает калькулятор по типу тендера."""
        tt = tender_type.lower()

        if "обучение" in tt:
            return self.calc.calculate_education(
                students_count=details.get("students_count", 0),
                certificates=details.get("certificates", 0),
                is_distance=details.get("is_distance", True),
                delivery_count=details.get("delivery_count", 1),
            )

        elif "соут" in tt:
            return self.calc.calculate_sout(
                rm_total=details.get("rm_total", 0),
                rm_category_1=details.get("rm_category_1", 0),
                rm_category_2=details.get("rm_category_2", 0),
                rm_with_iii=details.get(
                    "iii_count", 0
                ),  # ИСПРАВЛЕНО: iii_count вместо has_iii * quantity_rm
                variant=1,
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
            )

        elif "плк" in tt:
            return self.calc.calculate_plk(
                points_count=details.get("points_count", 0),
                factors_count=details.get("factors_count", 0),
                delivery_count=details.get("delivery_count", 1),
                is_annual=details.get("is_annual", False),
                needs_subcontractor=details.get("needs_subcontractor", False),
            )

        elif "опр" in tt:
            return self.calc.calculate_opr(
                rm_count=details.get("rm_total", 0),
                delivery_count=details.get("delivery_count", 1),
                needs_siz_norms=details.get("needs_siz_norms", False),
                needs_dsiz_norms=details.get("needs_dsiz_norms", False),
                needs_iot_norms=details.get("needs_iot_norms", False),
            )

        else:
            logger.warning(f"Неизвестный тип тендера: {tender_type}")
            return self.calc.calculate_sout(
                rm_total=details.get("rm_total", 1), variant=1
            )

    def _generate_comment(self, tender_type, calc_result, risk_result, details):
        """Генерирует комментарий если LLM не дал свой."""
        lines = [
            f"Анализ тендера типа «{tender_type}»",
            f"",
            f"Расчётная себестоимость: {calc_result.cost_price:,.0f} ₽",
            f"Рекомендуемая цена: {calc_result.recommended_price:,.0f} ₽",
            f"Маржа: {calc_result.margin_percent:.1f}%",
            f"",
            f"Решение: {risk_result.decision}",
            f"Уровень риска: {risk_result.risk_level}",
        ]

        if risk_result.red_flags:
            lines.extend(["", "Выявленные риски:"])
            for flag in risk_result.red_flags:
                lines.append(f"• {flag}")

        return "\n".join(lines)
