"""
core/analysis/result_formatter.py
Форматирование результатов анализа тендера.
Вынесено из analyzer.py (v6.5).
"""

from dataclasses import dataclass
from typing import Optional, List, Dict

from utils.url_builder import get_url_builder


@dataclass
class TenderAnalysis:
    """Результат анализа тендера."""

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
    red_flags: List[str]
    transport_cost: float
    subcontractor_cost: float
    guarantee_cost: float
    details: Dict
    raw_llm_response: Optional[Dict] = None
    quantity_source: str = "unknown"
    needs_manual_review: bool = False
    llm_confidence: float = 0.0

    def to_dict(self) -> Dict:
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
            "quantity_source": self.quantity_source,
            "needs_manual_review": self.needs_manual_review,
            "llm_confidence": self.llm_confidence,
        }

    def to_sheets_row(self, law_type: str = "44") -> Dict:
        """Форматирует результат для Google Sheets."""
        url_builder = get_url_builder()
        tender_url = url_builder.build_common_info_url(
            reg_number=self.tender_id, law_type=law_type
        )
        return {
            "ID тендера": self.tender_id,
            "Наименование услуг": self.details.get("service_name", "Не определено"),
            "Количество": self.details.get("quantity", 1),
            "Способ проведения закупки": self.details.get("procurement_method", ""),
            "НМЦК": self.nmck,
            "Ссылка на тендер": tender_url,
            "ЭТП": self.details.get("etp", ""),
            "Регион": self.details.get("region", ""),
            "Обеспечение заявки": self.details.get("application_guarantee") or "Не требуется",
            "Обеспечение контракта": self.details.get("contract_guarantee") or "Не требуется",
            "Способ обеспечения исполнения": self.details.get("guarantee_method") or "Не требуется",
            "Срок подачи заявки до": self.details.get("deadline_date", ""),
            "Цена предложения": self.recommended_price,
            "Комментарий от ИИ-агента": self._format_comment(),
            "Ручная проверка": "ДА" if self.needs_manual_review else "НЕТ",
            "Уверенность ИИ": round(self.llm_confidence, 2),
        }

    def _format_comment(self) -> str:
        """Форматирует многострочный комментарий."""
        lines = [
            f"Тип: {self.tender_type}",
            f"НМЦК: {self.nmck:,.0f} ₽",
            f"Расчётная цена: {self.calculated_price:,.0f} ₽",
            f"Рекомендуемая цена: {self.recommended_price:,.0f} ₽",
            f"Маржа: {self.margin_percent:.1f}% ({self.margin_rub:,.0f} ₽)",
            f"Себестоимость: {self.cost_price:,.0f} ₽",
            f"Уровень риска: {self.risk_level}",
            f"Решение: {self.decision}",
            f"Источник количества: {self.quantity_source}",
        ]
        if self.needs_manual_review:
            lines.append(
                "⚠️ ТРЕБУЕТСЯ РУЧНОЙ АНАЛИЗ: количество не определено или ненадёжно"
            )
        if self.llm_confidence > 0:
            lines.append(f"Уверенность ИИ: {self.llm_confidence:.2f}")
        lines.extend(["", "Риски и флаги:"])
        for flag in self.red_flags:
            lines.append(f"• {flag}")
        lines.extend([
            "",
            f"Транспортные: {self.transport_cost:,.0f} ₽",
            f"Субподряд: {self.subcontractor_cost:,.0f} ₽",
            f"Обеспечение: {self.guarantee_cost:,.0f} ₽",
        ])
        return "\n".join(lines)
