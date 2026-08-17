"""
Парсеры для TENDER-BOT.
"""

from core.parsers.detailed_parser import DetailedParser
from core.parsers.tender_models import TenderDocument, TenderDetail
from core.parsers.address_parser import AddressParser
from core.parsers.type_detector import TypeDetector
from core.parsers.ktru_parser import KtruParser
from core.parsers.html_parsers import Html44Parser, Html223Parser

__all__ = [
    "DetailedParser",
    "TenderDocument",
    "TenderDetail",
    "AddressParser",
    "TypeDetector",
    "KtruParser",
    "Html44Parser",
    "Html223Parser",
]
