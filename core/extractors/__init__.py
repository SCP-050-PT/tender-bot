"""
Экстракторы документов и параметров для TENDER-BOT.
"""

from core.extractors.base_extractor import BaseExtractor
from core.extractors.docx_extractor import DocxExtractor
from core.extractors.pdf_extractor import PdfExtractor
from core.extractors.excel_extractor import ExcelExtractor
from core.extractors.zip_extractor import ZipExtractor
from core.extractors.text_extractor import TextExtractor
from core.extractors.regex_extractor import RegexExtractor
from core.extractors.table_extractor import TableExtractor

__all__ = [
    "BaseExtractor",
    "DocxExtractor",
    "PdfExtractor",
    "ExcelExtractor",
    "ZipExtractor",
    "TextExtractor",
    "RegexExtractor",
    "TableExtractor",
]
