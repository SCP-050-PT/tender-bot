"""
core/analysis/result.py
Результат анализа тендера (единый dataclass).
Заменяет: AnalysisResult из analyzer.py + TenderAnalysis из result_formatter.py.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List


@dataclass
class AnalysisResult:
    """Результат анализа тендера."""

    tender_type: str = ""
    cost_price: float = 0.0
    recommended_price: float = 0.0
    margin_percent: float = 0.0
    risk_level: str = "low"
    decision: str = "рекомендуется"
    needs_manual_review: bool = False
    llm_confidence: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    comment: str = ""
    review_reason: str = ""
    type_detection_source: str = ""
    classification_method: str = ""
    guards_triggered: List[str] = field(default_factory=list)
    nmck: float = 0.0
    red_flags: List[str] = field(default_factory=list)   

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tender_type": self.tender_type,
            "cost_price": self.cost_price,
            "recommended_price": self.recommended_price,
            "margin_percent": self.margin_percent,
            "risk_level": self.risk_level,
            "decision": self.decision,
            "needs_manual_review": self.needs_manual_review,
            "llm_confidence": self.llm_confidence,
            "details": self.details,
            "comment": self.comment,
            "review_reason": self.review_reason,
            "type_detection_source": self.type_detection_source,
            "classification_method": self.classification_method,
            "guards_triggered": self.guards_triggered,
            "nmck": self.nmck,
            "red_flags": self.red_flags,  # ← НОВОЕ ПОЛЕ
        }

    def _format_comment(self) -> str:
        """Форматирует комментарий для Google Sheets (одна строка)."""
        lines = [self.comment]
        if self.review_reason:
            lines.append(f"⚠️ {self.review_reason}")
        if self.guards_triggered:
            lines.append(f"Guards: {', '.join(self.guards_triggered)}")
        return " | ".join(filter(None, lines))
