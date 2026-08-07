"""
Экстрактор текста из PDF файлов.
Багфиксы v6.6-r2:
  - Проверка магических байтов перед открытием
  - Обработка HTML-страниц ошибок, замаскированных под PDF
"""

from pathlib import Path
from loguru import logger

from core.config.document_config import PDF_MAGIC
from core.extractors.base_extractor import BaseExtractor

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    logger.warning("PyMuPDF не установлен, PDF будут пропущены")


class PdfExtractor(BaseExtractor):
    """Извлекает текст из PDF файлов."""

    SUPPORTED_EXTENSIONS = ["pdf"]

    def extract(self, file_path: Path, doc_name: str = "") -> str:
        if not HAS_PYMUPDF:
            logger.warning("[PdfExtractor] PyMuPDF не доступен")
            return ""

        # Проверяем, что файл действительно PDF
        if not self._is_valid_pdf(file_path):
            return ""

        try:
            doc = fitz.open(file_path)
            texts = []
            for page in doc:
                text = page.get_text()
                if text.strip():
                    texts.append(text.strip())
            doc.close()
            return "\n".join(texts)
        except Exception as e:
            logger.error(f"[PdfExtractor] Ошибка: {e}")
            return ""

    def _is_valid_pdf(self, file_path: Path) -> bool:
        """Проверяет, что файл действительно PDF (не HTML-страница ошибки)."""
        try:
            file_size = file_path.stat().st_size
            if file_size < 1000:
                logger.warning(f"[PdfExtractor] Файл слишком мал ({file_size} байт)")
                # Проверяем, не HTML ли это
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(200).lower()
                    if "<html" in text or "<!doctype" in text:
                        logger.warning("[PdfExtractor] Файл — HTML-страница, не PDF")
                        return False
                return False

            with open(file_path, "rb") as f:
                header = f.read(5)
                if header != b"%PDF-":
                    logger.warning(f"[PdfExtractor] Неверная сигнатура: {header!r}")
                    return False

            return True
        except Exception as e:
            logger.error(f"[PdfExtractor] Ошибка проверки: {e}")
            return False
