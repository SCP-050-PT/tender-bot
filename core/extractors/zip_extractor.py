"""
core/extractors/zip_extractor.py
Единый экстрактор текста из ZIP-архивов.

ОБЪЕДИНЁН (v6.8.6-r1):
  - База: core/extractors/ v6.6-r2 (ленивая инициализация, FILE_PRIORITY, CONTRACT_PATTERNS)
  - Добавлено из core/documents/ v6.8.6:
    * Декодирование cp1251 (3 кодировки: utf-8, cp866, cp1251)
    * max_depth для рекурсии (в дополнение к max_files)
  - Убрано:
    * Заглушки lambda по умолчанию (используем конфиг)
    * Хардкод приоритетов и паттернов контрактов

v7.2.0: Добавлена поддержка .7z через py7zr
"""

import re
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import List
from loguru import logger

from core.config.document_config import FILE_PRIORITY, CONTRACT_PATTERNS
from core.extractors.base_extractor import BaseExtractor

try:
    import py7zr

    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False


class ZipExtractor(BaseExtractor):
    """Единый экстрактор текста из ZIP и 7z архивов."""

    SUPPORTED_EXTENSIONS = ["zip", "7z"]

    # v7.2.0: Расширения файлов внутри архива, которые мы умеем читать
    READABLE_EXTENSIONS = {"docx", "doc", "xlsx", "xls", "pdf", "txt", "rtf"}

    def __init__(self, max_files: int = 3, max_depth: int = 2):
        super().__init__()
        self.max_files = max_files
        self.max_depth = max_depth
        self._sub_extractors = {}

    def _get_sub_extractor(self, ext: str):
        """Ленивая инициализация суб-экстракторов."""
        if ext not in self._sub_extractors:
            if ext in ["docx", "doc"]:
                from core.extractors.docx_extractor import DocxExtractor

                self._sub_extractors[ext] = DocxExtractor()
            elif ext == "pdf":
                from core.extractors.pdf_extractor import PdfExtractor

                self._sub_extractors[ext] = PdfExtractor()
            elif ext in ["xlsx", "xls"]:
                from core.extractors.excel_extractor import ExcelExtractor

                self._sub_extractors[ext] = ExcelExtractor()
            elif ext == "zip":
                self._sub_extractors[ext] = ZipExtractor(
                    max_files=self.max_files,
                    max_depth=self.max_depth - 1,
                )
            elif ext in ["txt", "rtf"]:
                from core.extractors.text_extractor import TextExtractor

                self._sub_extractors[ext] = TextExtractor()
        return self._sub_extractors.get(ext)

    def extract(self, file_path: Path, doc_name: str = "") -> str:
        """Распаковывает ZIP/7z и извлекает текст из вложенных файлов."""

        # v7.2.0: Маршрутизация по расширению
        ext = file_path.suffix.lower().lstrip(".")
        if ext == "7z":
            return self._extract_7z(file_path, doc_name)

        # Оригинальная логика для ZIP
        if self.max_depth <= 0:
            logger.warning(
                f"[ZipExtractor] Достигнут лимит глубины рекурсии: {doc_name}"
            )
            return ""

        temp_dir = Path(tempfile.mkdtemp(prefix="tender_zip_"))
        texts = []

        try:
            with zipfile.ZipFile(file_path, "r") as z:
                files = self._list_files(z)
                prioritized = self._prioritize_files(files)

                for priority, fname, ext, _ in prioritized[: self.max_files]:
                    try:
                        text = self._extract_file(z, fname, ext, temp_dir)
                        if text:
                            texts.append(f"=== ВЛОЖЕННЫЙ ФАЙЛ: {fname} ===\n{text}")
                    except Exception as e:
                        logger.warning(f"[ZipExtractor] Ошибка {fname}: {e}")
                        continue

        except zipfile.BadZipFile:
            logger.error(f"[ZipExtractor] Повреждённый ZIP: {doc_name}")
            return ""
        except Exception as e:
            logger.error(f"[ZipExtractor] Ошибка {doc_name}: {e}")
            return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        result = "\n\n".join(texts)
        logger.info(f"[ZipExtractor] Извлечено: {len(result)} символов")
        return result

    def _extract_7z(self, file_path: Path, doc_name: str = "") -> str:
        """Распаковывает 7z архив через py7zr."""
        if not HAS_PY7ZR:
            logger.warning(
                "[ZipExtractor] py7zr не установлен. " "Установите: pip install py7zr"
            )
            return ""

        if self.max_depth <= 0:
            logger.warning(
                f"[ZipExtractor] Достигнут лимит глубины рекурсии: {doc_name}"
            )
            return ""

        temp_dir = Path(tempfile.mkdtemp(prefix="tender_7z_"))
        texts = []

        try:
            with py7zr.SevenZipFile(file_path, mode="r") as sz:
                sz.extractall(path=temp_dir)

            # Собираем все файлы из распакованной директории
            all_files = []
            for f in temp_dir.rglob("*"):
                if f.is_file():
                    f_ext = f.suffix.lower().lstrip(".")
                    if f_ext in self.READABLE_EXTENSIONS:
                        all_files.append(f)

            # Сортируем по приоритету (ТЗ первые, контракты последние)
            all_files.sort(key=lambda f: self._get_file_priority(f.name), reverse=True)

            for fpath in all_files[: self.max_files]:
                try:
                    text = self._extract_inner_file(fpath, fpath.name)
                    if text:
                        texts.append(f"=== ВЛОЖЕННЫЙ ФАЙЛ: {fpath.name} ===\n{text}")
                except Exception as e:
                    logger.warning(f"[ZipExtractor] 7z ошибка {fpath.name}: {e}")
                    continue

        except Exception as e:
            logger.error(f"[ZipExtractor] Ошибка 7z {doc_name}: {e}")
            return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        result = "\n\n".join(texts)
        logger.info(
            f"[ZipExtractor] 7z извлечено: {len(texts)} файлов, "
            f"{len(result)} символов"
        )
        return result

    def _extract_inner_file(self, file_path: Path, original_name: str) -> str:
        """Извлекает текст из файла внутри 7z архива через суб-экстракторы."""
        ext = file_path.suffix.lower().lstrip(".")
        extractor = self._get_sub_extractor(ext)
        if extractor:
            text = extractor.extract(file_path, original_name)
            logger.info(f"[ZipExtractor] 7z {original_name}: {len(text)} симв.")
            return text
        else:
            from core.extractors.text_extractor import TextExtractor

            return TextExtractor().extract(file_path, original_name)

    def _list_files(self, z: zipfile.ZipFile) -> List[str]:
        """Возвращает список файлов, исключая служебные."""
        return [
            f
            for f in z.namelist()
            if not f.startswith("__MACOSX/")
            and not f.startswith(".")
            and not f.endswith("/")
        ]

    def _prioritize_files(self, files: List[str]) -> List[tuple]:
        """Сортирует файлы по приоритету."""
        prioritized = []
        for fname in files:
            priority = self._get_file_priority(fname)
            is_contract = self._is_contract_file(fname)
            ext = Path(fname).suffix.lower().lstrip(".")
            if is_contract:
                continue
            prioritized.append((priority, fname, ext, is_contract))
        prioritized.sort(key=lambda x: (-x[0], x[3]))
        return prioritized

    def _get_file_priority(self, filename: str) -> int:
        """Определяет приоритет файла по имени."""
        if not filename:
            return 0
        name_lower = filename.lower()
        max_priority = 0
        for pattern, priority in FILE_PRIORITY.items():
            if re.search(pattern, name_lower, re.IGNORECASE):
                max_priority = max(max_priority, priority)
        return max_priority

    def _is_contract_file(self, filename: str) -> bool:
        """Проверяет, является ли файл контрактом/договором."""
        if not filename:
            return False
        name_lower = filename.lower()
        for pattern in CONTRACT_PATTERNS:
            if re.search(pattern, name_lower, re.IGNORECASE):
                return True
        return False

    def _extract_file(
        self, z: zipfile.ZipFile, fname: str, ext: str, temp_dir: Path
    ) -> str:
        """Извлекает один файл из ZIP и обрабатывает его."""
        decoded_fname = self._decode_filename(fname)

        safe_fname = re.sub(r"[^\w\-_.]", "_", decoded_fname)[:100]
        extracted_path = temp_dir / safe_fname

        with z.open(fname) as src, open(extracted_path, "wb") as dst:
            dst.write(src.read())

        real_ext = ext
        if ext not in ["docx", "doc", "pdf", "xlsx", "xls", "txt", "rtf", "zip"]:
            real_ext = self._detect_by_content(extracted_path) or ext

        extractor = self._get_sub_extractor(real_ext)
        if extractor:
            text = extractor.extract(extracted_path, decoded_fname)
            logger.info(f"[ZipExtractor] {decoded_fname}: {len(text)} симв.")
            return text
        else:
            from core.extractors.text_extractor import TextExtractor

            return TextExtractor().extract(extracted_path, decoded_fname)

    def _decode_filename(self, fname: str) -> str:
        """Декодирует имя файла из ZIP (3 кодировки: utf-8, cp866, cp1251)."""
        for encoding in ["utf-8", "cp866", "cp1251"]:
            try:
                return fname.encode("cp437").decode(encoding)
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return fname

    def _detect_by_content(self, file_path: Path) -> str:
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
        except Exception:
            pass
        return ""
