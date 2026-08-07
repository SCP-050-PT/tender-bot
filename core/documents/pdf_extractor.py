"""
core/documents/pdf_extractor.py
Извлечение текста из PDF-файлов.
Вынесено из document_processor.py (v6.5).
"""

from pathlib import Path
from loguru import logger

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    logger.warning("PyMuPDF не установлен, PDF будут пропущены")

PDF_MAGIC = b"%PDF"


class PdfExtractor:
    """Извлекает текст из PDF-файлов с валидацией."""

    def extract(self, file_path: Path) -> str:
        """Извлекает текст из PDF с проверкой формата."""
        if not HAS_PYMUPDF:
            logger.warning("PyMuPDF не установлен, PDF пропущен")
            return ""

        try:
            # Проверка размера
            file_size = file_path.stat().st_size
            if file_size < 1000:
                logger.warning(f"PDF слишком мал ({file_size} байт)")
                return self._check_html_fallback(file_path)

            # Проверка магических байт
            with open(file_path, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                logger.warning(f"Файл не является PDF (сигнатура: {header!r})")
                return ""

            # Извлечение текста
            doc = fitz.open(file_path)
            texts = []
            for page in doc:
                text = page.get_text()
                if text.strip():
                    texts.append(text.strip())
            doc.close()
            return "\n".join(texts)

        except Exception as e:
            logger.error(f"Ошибка PDF: {e}")
            return ""

    def _check_html_fallback(self, file_path: Path) -> str:
        """Проверяет, не является ли файл HTML-страницей ошибки."""
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
                if "<html" in text.lower() or "<!doctype" in text.lower():
                    logger.warning("Файл является HTML-страницей, не PDF")
                    return ""
        except:
            pass
        return ""
