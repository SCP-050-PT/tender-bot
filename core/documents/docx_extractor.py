"""
core/documents/docx_extractor.py
Извлечение текста из DOCX/DOC файлов.

ИСПРАВЛЕНО (v6.8):
- Улучшенное извлечение таблиц с сохранением структуры
- Fallback для бинарных .doc файлов через antiword/catdoc
- Поддержка вложенных таблиц
- Очистка мусора из DOCX-конвертаций
"""

import re
import zipfile
import os
import subprocess
from typing import Optional, List, Dict
from pathlib import Path

from loguru import logger


class DocxExtractor:
    """Извлекает текст из DOCX/DOC файлов."""

    VERSION = "v6.8"

    def __init__(self):
        logger.info(f"DocxExtractor инициализирован ({self.VERSION})")

    def extract(self, file_path: str) -> str:
        """Извлекает текст из файла."""
        path = Path(file_path)

        if not path.exists():
            logger.error(f"[{self.VERSION}] Файл не найден: {file_path}")
            return ""

        # v6.8: Проверяем magic bytes
        if not self._is_valid_docx(file_path):
            # Пробуем fallback для старых .doc
            if path.suffix.lower() == ".doc":
                return self._extract_doc_fallback(file_path)
            logger.error(
                f"[{self.VERSION}] Файл не является валидным DOCX: {file_path}"
            )
            return ""

        try:
            return self._extract_docx(file_path)
        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка извлечения DOCX: {e}")
            # Fallback на zipfile
            return self._extract_via_zipfile(file_path)

    def _is_valid_docx(self, file_path: str) -> bool:
        """Проверяет, является ли файл валидным DOCX (ZIP с XML)."""
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                # Проверяем наличие основного XML
                if "word/document.xml" in z.namelist():
                    return True
                # Проверяем content_types
                if "[Content_Types].xml" in z.namelist():
                    content = z.read("[Content_Types].xml").decode(
                        "utf-8", errors="ignore"
                    )
                    if "wordprocessingml" in content:
                        return True
            return False
        except zipfile.BadZipFile:
            return False
        except Exception as e:
            logger.warning(f"[{self.VERSION}] Ошибка проверки DOCX: {e}")
            return False

    def _extract_docx(self, file_path: str) -> str:
        """Извлекает текст из DOCX с сохранением структуры таблиц."""
        try:
            from docx import Document

            doc = Document(file_path)

            parts = []

            # v6.8: Параграфы
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    parts.append(text)

            # v6.8: Таблицы с сохранением структуры
            for i, table in enumerate(doc.tables):
                table_text = self._format_table(table, i)
                if table_text:
                    parts.append(table_text)

            return "\n\n".join(parts)

        except ImportError:
            logger.warning(
                f"[{self.VERSION}] python-docx не установлен, используем zipfile"
            )
            return self._extract_via_zipfile(file_path)
        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка python-docx: {e}")
            return self._extract_via_zipfile(file_path)

    def _format_table(self, table, table_index: int) -> str:
        """Форматирует таблицу в читаемый вид."""
        rows_data = []

        for row in table.rows:
            cells = []
            for cell in row.cells:
                # v6.8: Рекурсивно извлекаем текст из вложенных таблиц
                cell_text = self._extract_cell_text(cell)
                cells.append(cell_text)
            rows_data.append(cells)

        if not rows_data:
            return ""

        # v6.8: Определяем, является ли таблица "данными" или "макетом"
        # Если >50% ячеек пустые или содержат только цифры -> данные
        is_data_table = self._is_data_table(rows_data)

        if is_data_table:
            # Форматируем как key-value или таблицу
            return self._format_data_table(rows_data, table_index)
        else:
            # Просто текст
            return "\n".join(" | ".join(c for c in row if c) for row in rows_data)

    def _extract_cell_text(self, cell) -> str:
        """Извлекает текст из ячейки, включая вложенные таблицы."""
        texts = []

        # Параграфы ячейки
        for para in cell.paragraphs:
            if para.text.strip():
                texts.append(para.text.strip())

        # Вложенные таблицы
        for nested_table in cell.tables:
            nested_text = self._format_table(nested_table, -1)
            if nested_text:
                texts.append(nested_text)

        return " ".join(texts)

    def _is_data_table(self, rows_data: List[List[str]]) -> bool:
        """Определяет, содержит ли таблица структурированные данные."""
        if len(rows_data) < 2:
            return False

        # Проверяем заголовок
        header = rows_data[0]
        data_keywords = [
            "№",
            "наименование",
            "количество",
            "ед.",
            "цена",
            "сумма",
            "должность",
            "рабочее место",
            "адрес",
            "кол-во",
        ]
        header_text = " ".join(h.lower() for h in header)
        has_header = any(kw in header_text for kw in data_keywords)

        # Проверяем, что есть числовые данные
        numeric_count = 0
        total_cells = 0
        for row in rows_data[1:]:
            for cell in row:
                total_cells += 1
                if cell and re.search(r"\d+", cell):
                    numeric_count += 1

        has_numbers = numeric_count > 0 and (numeric_count / max(total_cells, 1)) > 0.2

        return has_header or has_numbers

    def _format_data_table(self, rows_data: List[List[str]], table_index: int) -> str:
        """Форматирует таблицу данных в читаемый вид."""
        lines = [f"=== ТАБЛИЦА {table_index + 1} ==="]

        # Заголовок
        if rows_data:
            header = [h.strip() if h else "" for h in rows_data[0]]
            lines.append(" | ".join(header))
            lines.append("-" * (sum(len(h) for h in header) + 3 * len(header)))

        # Данные
        for row in rows_data[1:]:
            cells = [c.strip() if c else "" for c in row]
            # Пропускаем полностью пустые строки
            if any(cells):
                lines.append(" | ".join(cells))

        lines.append("=== КОНЕЦ ТАБЛИЦЫ ===")
        return "\n".join(lines)

    def _extract_via_zipfile(self, file_path: str) -> str:
        """Fallback: извлекает текст через zipfile."""
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                if "word/document.xml" not in z.namelist():
                    logger.error(f"[{self.VERSION}] Нет word/document.xml в архиве")
                    return ""

                xml_content = z.read("word/document.xml").decode(
                    "utf-8", errors="ignore"
                )

                # v6.8: Улучшенная очистка XML
                # Удаляем теги
                text = re.sub(r"<[^>]+>", " ", xml_content)
                # Удаляем лишние пробелы
                text = re.sub(r"\s+", " ", text)
                # Восстанавливаем переносы строк
                text = re.sub(r" (\d+\.) ", r"\n\1 ", text)

                return text.strip()

        except Exception as e:
            logger.error(f"[{self.VERSION}] Ошибка zip-извлечения: {e}")
            return ""

    def _extract_doc_fallback(self, file_path: str) -> str:
        """Fallback для старых бинарных .doc файлов."""
        # v6.8: Пробуем antiword
        try:
            result = subprocess.run(
                ["antiword", file_path], capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info(
                    f"[{self.VERSION}] Извлечено через antiword: {len(result.stdout)} символов"
                )
                return result.stdout
        except FileNotFoundError:
            logger.warning(f"[{self.VERSION}] antiword не установлен")
        except Exception as e:
            logger.warning(f"[{self.VERSION}] Ошибка antiword: {e}")

        # v6.8: Пробуем catdoc
        try:
            result = subprocess.run(
                ["catdoc", file_path], capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info(
                    f"[{self.VERSION}] Извлечено через catdoc: {len(result.stdout)} символов"
                )
                return result.stdout
        except FileNotFoundError:
            logger.warning(f"[{self.VERSION}] catdoc не установлен")
        except Exception as e:
            logger.warning(f"[{self.VERSION}] Ошибка catdoc: {e}")

        # v6.8: Пробуем textract
        try:
            import textract

            text = textract.process(file_path).decode("utf-8", errors="ignore")
            logger.info(
                f"[{self.VERSION}] Извлечено через textract: {len(text)} символов"
            )
            return text
        except ImportError:
            logger.warning(f"[{self.VERSION}] textract не установлен")
        except Exception as e:
            logger.warning(f"[{self.VERSION}] Ошибка textract: {e}")

        logger.error(f"[{self.VERSION}] Не удалось извлечь .doc файл: {file_path}")
        return ""
