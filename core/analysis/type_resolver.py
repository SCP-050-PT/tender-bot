"""
core/analysis/type_resolver.py
Каскадное определение типа тендера.
Вынесено из analyzer.py (v6.8.6-r3).
ПАТЧ v6.8.6-r4: Исправлено определение ОПР vs СОУТ (title имеет приоритет над КТРУ)
"""

from typing import Optional, Dict, Any, Tuple
from loguru import logger


class TypeResolver:
    """Определяет тип тендера по каскадной логике."""

    VERSION = "v6.8.6-r4"

    # Ключевые слова для текстовой эвристики
    KEYWORDS = {
        "education": [
            "обучение",
            "слушатели",
            "программа",
            "удостоверение",
            "повышение квалификации",
            "переподготовка",
            "инструктаж",
            "стажировка",
        ],
        "sout": [
            "специальная оценка",
            "соут",
            "вредные факторы",
            "класс условий труда",
            "оценка условий труда",
            "оценка рабочих мест",
        ],
        "opr": [
            "профессиональный риск",
            "опр",
            "оценка рисков",
            "идентификация опасностей",
            "мероприятия по снижению рисков",
        ],
        "plk": [
            "производственный контроль",
            "плк",
            "лабораторные исследования",
            "лабораторный контроль",
            "замеры шума",
            "замеры вибрации",
            "санитарно-гигиенические исследования",
        ],
    }

    # === НОВОЕ v6.8.6-r4: Ключевые слова для title (имеют приоритет) ===
    TITLE_KEYWORDS = {
        "opr": [
            "оценка профессиональных рисков",
            "оценки профессиональных рисков",
            "оценке профессиональных рисков",
            "оценкой профессиональных рисков",
            "опр",
            "профессиональный риск",
            "профессиональных рисков",
            "профессиональные риски",
        ],
        "sout": [
            "специальная оценка условий труда",
            "специальной оценки условий труда",
            "специальной оценке условий труда",
            "специальной оценкой условий труда",
            "соут",
            "оценка условий труда",
            "оценки условий труда",
            "оценке условий труда",
            "специальная оценка рабочих мест",
        ],
        "education": [
            "обучение по охране труда",
            "обучение охране труда",
            "повышение квалификации",
            "переподготовка",
            "профессиональное обучение",
            "программа обучения",
            "курсы повышения квалификации",
        ],
        "plk": [
            "производственный лабораторный контроль",
            "производственного лабораторного контроля",
            "лабораторный контроль",
            "плк",
            "лабораторные исследования",
            "лабораторные испытания",
        ],
    }

    # Нормализация псевдонимов типов
    TYPE_ALIASES = {
        "sout": "sout",
        "соут": "sout",
        "специальная оценка": "sout",
        "специальной оценки": "sout",
        "education": "education",
        "обучение": "education",
        "обучения": "education",
        "opr": "opr",
        "опр": "opr",
        "оценка профессиональных рисков": "opr",
        "оценки профессиональных рисков": "opr",
        "plk": "plk",
        "плк": "plk",
        "производственный контроль": "plk",
        "производственного контроля": "plk",
        "combined": "combined",
        "комбинированный": "combined",
        "комбинированного": "combined",
    }

    def resolve(
        self,
        tender_info: Dict[str, Any],
        documents_text: str,
        llm_classification: Optional[str],
        llm_confidence: float,
        tender_type_hint: Optional[str],
    ) -> Tuple[str, str, str]:
        """
        Каскадное определение типа тендера.

        Returns:
            (type, source, method)
            type: sout | education | plk | opr | combined | unknown
            source: откуда определён тип
            method: каким методом
        """
        # Шаг 1: hint из detailed_parser (самый надёжный для 223-ФЗ)
        if tender_type_hint:
            normalized = self._normalize(tender_type_hint)
            logger.info(f"[{self.VERSION}] Тип из detailed_parser hint: {normalized}")
            return normalized, "detailed_parser_hint", "hint"

        # === НОВОЕ v6.8.6-r4: Шаг 1.5 — ПРОВЕРКА TITLE (приоритет над КТРУ) ===
        purchase_name = tender_info.get("purchase_name", "")
        if purchase_name:
            name_lower = purchase_name.lower()
            for ttype, keywords in self.TITLE_KEYWORDS.items():
                if any(kw in name_lower for kw in keywords):
                    logger.info(
                        f"[{self.VERSION}] Тип из title: {ttype} ('{purchase_name[:60]}...')"
                    )
                    return ttype, "title_heuristic", "heuristic"

        # Шаг 2: LLM классификация (если confidence высокий)
        if llm_classification and llm_confidence >= 0.7:
            normalized = self._normalize(llm_classification)
            logger.info(f"[{self.VERSION}] Тип из LLM классификации: {normalized}")
            return normalized, "llm_classification", "classify"

        # Шаг 3: КТРУ-данные (для 44-ФЗ)
        has_rm = bool(tender_info.get("rm_total") and tender_info["rm_total"] > 0)
        has_students = bool(
            tender_info.get("students_count") and tender_info["students_count"] > 0
        )

        if has_rm and has_students:
            logger.info(f"[{self.VERSION}] Тип из КТРУ: combined (РМ + слушатели)")
            return "combined", "ktru", "data"
        if has_rm:
            logger.info(
                f"[{self.VERSION}] Тип из КТРУ: sout ({tender_info['rm_total']} РМ)"
            )
            return "sout", "ktru", "data"
        if has_students:
            logger.info(
                f"[{self.VERSION}] Тип из КТРУ: education ({tender_info['students_count']} слушателей)"
            )
            return "education", "ktru", "data"

        # Шаг 4: Эвристика по тексту документов
        text_lower = documents_text.lower()
        for ttype, keywords in self.KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                # Дополнительная проверка для education + охрана труда
                if ttype == "education" and (
                    "охрана труда" in text_lower or "охране труда" in text_lower
                ):
                    logger.info(f"[{self.VERSION}] Тип из текста: education (ОТ)")
                    return "education", "text_heuristic", "heuristic"
                logger.info(f"[{self.VERSION}] Тип из текста: {ttype}")
                return ttype, "text_heuristic", "heuristic"

        # Шаг 5: Fallback — неизвестный тип
        logger.warning(f"[{self.VERSION}] Тип не определён, будет ручная проверка")
        return "unknown", "fallback", "none"

    def _normalize(self, raw_type: str) -> str:
        """Нормализует строковый тип тендера."""
        if not raw_type:
            return "unknown"
        return self.TYPE_ALIASES.get(raw_type.lower().strip(), raw_type.lower().strip())
