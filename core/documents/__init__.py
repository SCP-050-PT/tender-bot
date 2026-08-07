"""
core/documents/__init__.py
Пакет обработки документов тендера.
"""

from core.documents.document_processor import DocumentProcessor, DocumentInfo
from core.documents.docx_extractor import DocxExtractor
from core.documents.excel_extractor import ExcelExtractor
from core.documents.pdf_extractor import PdfExtractor
from core.documents.zip_extractor import ZipExtractor

__all__ = [
    "DocumentProcessor",
    "DocumentInfo",
    "DocxExtractor",
    "ExcelExtractor",
    "PdfExtractor",
    "ZipExtractor",
]
