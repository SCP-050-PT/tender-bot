"""
Парсеры для TENDER-BOT.
"""

from core.parsers.detailed_parser import DetailedParser, TenderDocument, TenderDetail
from core.parsers.address_parser import AddressParser
from core.parsers.html_parsers import Html44Parser, Html223Parser

__all__ = [
    "DetailedParser",
    "TenderDocument",
    "TenderDetail",
    "AddressParser",
    "Html44Parser",
    "Html223Parser",
]
