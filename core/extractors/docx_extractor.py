"""
Экстрактор текста из DOCX/DOC файлов.
Багфиксы v6.6-r2:
  - Корректное определение расширения при сохранении
  - Fallback на zipfile при зависании python-docx
  - try/except вокруг table.rows/row.cells
"""

import re
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Optional
from loguru import logger

from core.config.document_config import MAX_DOCX_TIMEOUT, QUANTITY_COLUMN_KEYWORDS
from core.extractors.base_extractor import BaseExtractor

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    logger.warning("python-docx не установлен, используем zipfile fallback")


class DocxExtractor(BaseExtractor):
    """Извлекает текст из DOCX/DOC файлов."""

    SUPPORTED_EXTENSIONS = ["docx", "doc"]

    def extract(self, file_path: Path, doc_name: str = "") -> str:
        # Сначала пробуем python-docx с таймаутом
        text = self._extract_with_docx(file_path, doc_name)
        if text:
            return text

        # Fallback на zipfile (если python-docx не сработал или завис)
        logger.info(f"[DocxExtractor] Fallback на zipfile для {doc_name}")
        return self._extract_via_zip(file_path)

    def _extract_with_docx(self, file_path: Path, doc_name: str) -> str:
        if not HAS_DOCX:
            return ""

        def _do_extract():
            try:
                document = docx.Document(file_path)
                paragraphs = []
                for para in document.paragraphs:
                    if para.text.strip():
                        paragraphs.append(para.text.strip())

                tables_text = self._extract_tables(document)
                all_text = "\n".join(paragraphs + tables_text)
                return all_text
            except Exception as e:
                logger.error(f"[DocxExtractor] Ошибка python-docx: {e}")
                return ""

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_extract)
                return future.result(timeout=MAX_DOCX_TIMEOUT)
        except FutureTimeoutError:
            logger.error(
                f"[DocxExtractor] Таймаут {MAX_DOCX_TIMEOUT}с на {doc_name}, переключаемся на zipfile"
            )
            return ""
        except Exception as e:
            logger.error(f"[DocxExtractor] Ошибка при таймауте: {e}")
            return ""

    def _extract_tables(self, document) -> list[str]:
        """Извлекает таблицы с заголовками колонок."""
        tables_text = []

        for table in document.tables:
            try:
                headers = self._extract_headers(table)
                rows_text = self._extract_rows(table, headers)
                tables_text.extend(rows_text)
            except Exception as e:
                logger.debug(f"[DocxExtractor] Ошибка таблицы: {e}")
                continue

        return tables_text

    def _extract_headers(self, table) -> list[str]:
        """Извлекает заголовки таблицы (первая строка)."""
        headers = []
        try:
            if table.rows:
                header_row = table.rows[0]
                for cell in header_row.cells:
                    headers.append(cell.text.strip().lower())
        except Exception as e:
            logger.debug(f"[DocxExtractor] Ошибка чтения заголовков: {e}")
        return headers

    def _extract_rows(self, table, headers: list[str]) -> list[str]:
        """Извлекает строки таблицы с подписями заголовков."""
        rows_text = []

        for row_idx, row in enumerate(table.rows):
            if row_idx == 0 and headers:
                continue  # Пропускаем строку заголовков

            try:
                row_text = []
                for col_idx, cell in enumerate(row.cells):
                    cell_text = cell.text.strip()
                    if not cell_text:
                        continue

                    # Добавляем заголовок колонки к ячейке с количеством
                    if col_idx < len(headers) and headers[col_idx]:
                        header = headers[col_idx]
                        if self._looks_like_quantity(header, cell_text):
                            row_text.append(f"{header}: {cell_text}")
                        else:
                            row_text.append(cell_text)
                    else:
                        row_text.append(cell_text)

                if row_text:
                    rows_text.append(" | ".join(row_text))
            except Exception as e:
                logger.debug(f"[DocxExtractor] Ошибка чтения строки: {e}")
                continue

        return rows_text

    def _looks_like_quantity(self, header: str, cell_text: str) -> bool:
        """Проверяет, похоже ли содержимое на количество по заголовку."""
        if not cell_text:
            return False
        # Должно быть число
        if not re.match(r"^[\d\s.,]+$", cell_text.replace(" ", "")):
            return False
        header_lower = header.lower()
        return any(kw in header_lower for kw in QUANTITY_COLUMN_KEYWORDS)

    def _extract_via_zip(self, file_path: Path) -> str:
        """Fallback: извлечение через zipfile (без python-docx)."""
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                with z.open("word/document.xml") as f:
                    xml_content = f.read().decode("utf-8", errors="ignore")
                    text = re.sub(r"<[^>]+>", "", xml_content)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text
        except Exception as e:
            logger.error(f"[DocxExtractor] Ошибка zip-извлечения: {e}")
            return ""
