"""
utils/price_parser.py
Единый парсинг и форматирование цены.
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - Консолидирована логика из searcher.py, detailed_parser.py, main.py
  - Унифицированы разделители (пробел, NBSP, narrow NBSP)
  - Поддержка запятой/точки как десятичного разделителя
  - Форматирование для Sheets (русский формат: 1 234,56)
"""

import re
from typing import Optional
from loguru import logger


class PriceParser:
    """
    Единый парсер цен.
    Заменяет: _parse_price() в searcher.py, detailed_parser.py, _format_nmck() в main.py.
    """

    # Различные пробельные символы и разделители
    WHITESPACE_CHARS = "\xa0\u202f \t"
    CURRENCY_MARKS = "₽руб.рубРУБ."

    def __init__(self):
        logger.info("PriceParser инициализирован (v6.3)")

    def parse(self, price_text: str) -> Optional[float]:
        """
        Парсит цену из строки.

        Примеры:
            "1 234 567,89 ₽" → 1234567.89
            "1\xa0234\xa0567.89" → 1234567.89
            "1234567,89 руб." → 1234567.89
            "1 234 567" → 1234567.0
        """
        if not price_text:
            return None

        # Убираем валютные обозначения
        cleaned = price_text
        for mark in self.CURRENCY_MARKS:
            cleaned = cleaned.replace(mark, "")

        # Убираем все пробельные символы (они — разделители тысяч)
        for char in self.WHITESPACE_CHARS:
            cleaned = cleaned.replace(char, "")

        # Ищем число
        # Паттерн: целая часть + опциональные десятичные
        match = re.search(r"([\d]+(?:[.,]\d{1,2})?)", cleaned)
        if not match:
            return None

        price_str = match.group(1)

        # Определяем десятичный разделитель
        # Если есть и точка и запятая — последний считаем десятичным
        if "," in price_str and "." in price_str:
            # 1.234,56 → запятая десятичная
            if price_str.rfind(",") > price_str.rfind("."):
                price_str = price_str.replace(".", "").replace(",", ".")
            else:
                price_str = price_str.replace(",", "")
        elif "," in price_str:
            # Может быть 1,234,567.89 или 1234,56
            # Если запятая с 3 цифрами после — разделитель тысяч
            if re.search(r",\d{3}(?:[.,]|$)", price_str):
                price_str = price_str.replace(",", "")
            else:
                price_str = price_str.replace(",", ".")
        # Если только точка — она уже десятичная

        try:
            return float(price_str)
        except ValueError:
            logger.warning(
                f"Не удалось распарсить цену: '{price_text}' → '{price_str}'"
            )
            return None

    def format_for_sheets(self, price: float) -> str:
        """
        Форматирует цену для Google Sheets (русский формат).

        1234567.89 → "1 234 567,89"
        """
        if not price or price == 0:
            return ""

        # Разделяем целую и дробную части
        price_str = f"{price:,.2f}"
        # Меняем формат: 1,234,567.89 → 1 234 567,89
        price_str = price_str.replace(",", " ").replace(".", ",")
        return price_str

    def format_for_display(self, price: float, currency: str = "₽") -> str:
        """
        Форматирует цену для отображения.

        1234567.89 → "1 234 567,89 ₽"
        """
        if not price or price == 0:
            return f"0 {currency}"

        formatted = self.format_for_sheets(price)
        return f"{formatted} {currency}"

    def parse_nmck(self, nmck_text: str) -> float:
        """Безопасный парсинг НМЦК (возвращает 0 при ошибке)."""
        result = self.parse(nmck_text)
        return result if result is not None else 0.0


# Глобальный инстанс
_price_parser = None


def get_price_parser() -> PriceParser:
    global _price_parser
    if _price_parser is None:
        _price_parser = PriceParser()
    return _price_parser


def parse_price(price_text: str) -> Optional[float]:
    """Удобная функция."""
    return get_price_parser().parse(price_text)


def format_for_sheets(price: float) -> str:
    """Удобная функция."""
    return get_price_parser().format_for_sheets(price)
