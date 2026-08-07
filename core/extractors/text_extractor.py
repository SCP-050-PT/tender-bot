"""
Экстрактор текста из простых текстовых файлов (TXT, RTF).
"""

from pathlib import Path
from loguru import logger

from core.extractors.base_extractor import BaseExtractor


class TextExtractor(BaseExtractor):
    """Извлекает текст из TXT и RTF файлов."""

    SUPPORTED_EXTENSIONS = ["txt", "rtf"]

    def extract(self, file_path: Path, doc_name: str = "") -> str:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            logger.error(f"[TextExtractor] Ошибка {file_path}: {e}")
            return ""
