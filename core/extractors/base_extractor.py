"""
Базовый класс для всех экстракторов документов.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from loguru import logger


class BaseExtractor(ABC):
    """Базовый класс экстрактора текста из файла."""

    SUPPORTED_EXTENSIONS: list[str] = []

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def can_extract(self, file_path: Path, file_type: str = "") -> bool:
        """Проверяет, может ли экстрактор обработать этот файл."""
        ext = (file_type.lower() if file_type else file_path.suffix.lower()).lstrip(".")
        return ext in self.SUPPORTED_EXTENSIONS

    @abstractmethod
    def extract(self, file_path: Path, doc_name: str = "") -> str:
        """Извлекает текст из файла. Возвращает пустую строку при ошибке."""
        pass

    def _detect_by_magic(self, file_path: Path) -> Optional[str]:
        """Определяет тип файла по магическим байтам."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)

            from core.config.document_config import PDF_MAGIC, ZIP_MAGIC, OLE2_MAGIC

            if header.startswith(PDF_MAGIC):
                return "pdf"
            elif header.startswith(ZIP_MAGIC):
                return "zip"
            elif header.startswith(OLE2_MAGIC):
                return "doc"
            elif b"<html" in header or b"<!DOCTYPE" in header:
                return "html"
        except Exception as e:
            logger.debug(f"Ошибка определения типа: {e}")
        return None
