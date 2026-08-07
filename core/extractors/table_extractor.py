"""
Экстрактор параметров из структурированных таблиц (DOCX, Excel).
Вынесено из param_extractor.py и document_processor (v6.6-r2).
"""

import re
from typing import Optional, List, Dict
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class TableExtractedValue:
    """Значение, извлечённое из таблицы."""
    field: str
    value: int
    source: str  # 'ktru', 'xls', 'docx_table'
    confidence: float = 1.0


class TableExtractor:
    """Извлекает параметры из структурированных таблиц с confidence=1.0."""

    # Ключевые слова для колонок количества
    QUANTITY_HEADERS = [
        "количество", "кол-во", "кол.", "объем", "объём",
        "планируемое кол-во", "кол-во обучаемых", "обучаемых",
        "чел.", "человек", "слушателей",
        "количество рабочих мест", "кол-во рабочих мест",
    ]

    # Ключевые слова для колонок услуг
    SERVICE_HEADERS = [
        "наименование", "услуга", "работа", "предмет", "наименование работ",
    ]

    # Ключевые слова для типов услуг
    SERVICE_KEYWORDS = [
        "соут", "специальная оценка", "оценка условий труда",
        "обучение", "обучению", "повышение квалификации",
        "плк", "лабораторный контроль", "опр", "оценка профессиональных рисков",
    ]

    def extract_from_excel_rows(self, rows: List[Dict[str, str]]) -> List[TableExtractedValue]:
        """
        Извлекает параметры из строк Excel (уже распарсенных).
        rows: [{"количество": "50", "наименование": "СОУТ 50 РМ"}, ...]
        """
        results = []
        for row in rows:
            qty = self._find_quantity_in_row(row)
            service = self._find_service_in_row(row)
            if qty is not None:
                field = self._determine_field(service)
                results.append(TableExtractedValue(
                    field=field,
                    value=qty,
                    source="xls",
                    confidence=1.0,
                ))
        return results

    def extract_from_docx_table(self, headers: List[str], rows: List[List[str]]) -> List[TableExtractedValue]:
        """
        Извлекает параметры из таблицы DOCX.
        headers: ["Наименование", "Количество", ...]
        rows: [["СОУТ", "50"], ...]
        """
        results = []
        headers_lower = [h.lower().strip() for h in headers]
        qty_idx = self._find_quantity_column(headers_lower)
        svc_idx = self._find_service_column(headers_lower)

        if qty_idx < 0:
            return results

        for row in rows:
            if qty_idx >= len(row):
                continue
            qty_str = row[qty_idx].strip()
            qty = self._parse_quantity(qty_str)
            if qty is None:
                continue

            service = ""
            if svc_idx >= 0 and svc_idx < len(row):
                service = row[svc_idx].lower()

            field = self._determine_field(service)
            results.append(TableExtractedValue(
                field=field,
                value=qty,
                source="docx_table",
                confidence=1.0,
            ))

        return results

    def _find_quantity_column(self, headers: List[str]) -> int:
        """Находит индекс колонки с количеством."""
        for idx, h in enumerate(headers):
            if any(kw in h for kw in self.QUANTITY_HEADERS):
                return idx
        return -1

    def _find_service_column(self, headers: List[str]) -> int:
        """Находит индекс колонки с наименованием услуги."""
        for idx, h in enumerate(headers):
            if any(kw in h for kw in self.SERVICE_HEADERS):
                return idx
        return -1

    def _find_quantity_in_row(self, row: Dict[str, str]) -> Optional[int]:
        """Ищет количество в строке словаря."""
        for key, value in row.items():
            key_lower = key.lower()
            if any(kw in key_lower for kw in self.QUANTITY_HEADERS):
                return self._parse_quantity(value)
        return None

    def _find_service_in_row(self, row: Dict[str, str]) -> str:
        """Ищет наименование услуги в строке словаря."""
        for key, value in row.items():
            key_lower = key.lower()
            if any(kw in key_lower for kw in self.SERVICE_HEADERS):
                return value.lower()
        return ""

    def _parse_quantity(self, text: str) -> Optional[int]:
        """Парсит число из строки."""
        if not text:
            return None
        text = str(text).replace(" ", "").replace(",", ".")
        try:
            val = int(float(text))
            if 1 <= val <= 10000:
                return val
        except (ValueError, TypeError):
            pass
        return None

    def _determine_field(self, service_text: str) -> str:
        """Определяет поле по тексту услуги."""
        text_lower = service_text.lower()
        if any(kw in text_lower for kw in ["соут", "специальная оценка", "оценка условий"]):
            return "rm_total"
        elif any(kw in text_lower for kw in ["обучение", "обучению", "повышение квалификации"]):
            return "students_count"
        elif any(kw in text_lower for kw in ["плк", "лабораторный контроль", "точек"]):
            return "points_count"
        elif any(kw in text_lower for kw in ["опр", "оценка профессиональных рисков"]):
            return "opr_positions"
        return "quantity"  # generic
