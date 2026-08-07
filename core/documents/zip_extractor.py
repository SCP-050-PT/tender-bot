"""
core/documents/zip_extractor.py
Распаковка ZIP-архивов и извлечение текста из вложенных файлов.
Вынесено из document_processor.py (v6.5).
"""

import re
import zipfile
import tempfile
import shutil
from pathlib import Path
from loguru import logger


class ZipExtractor:
    """Распаковывает ZIP-архивы и извлекает текст из вложенных файлов."""

    def __init__(self, docx_extractor, pdf_extractor, excel_extractor):
        self.docx_extractor = docx_extractor
        self.pdf_extractor = pdf_extractor
        self.excel_extractor = excel_extractor

    def extract(self, file_path: Path, doc_name: str, file_priority_fn, is_contract_fn) -> str:
        """Распаковывает ZIP и извлекает текст из вложенных файлов."""
        logger.info(f"[ZIP] Распаковка: {doc_name}")
        texts = []
        temp_dir = Path(tempfile.mkdtemp(prefix="tender_zip_"))

        try:
            with zipfile.ZipFile(file_path, "r") as z:
                files = [
                    f for f in z.namelist()
                    if not f.startswith("__MACOSX/") and not f.startswith(".")
                ]
                logger.info(f"[ZIP] В архиве файлов: {len(files)}")

                # Приоритизация
                prioritized = []
                for fname in files:
                    if is_contract_fn(fname):
                        continue
                    priority = file_priority_fn(fname)
                    ext = Path(fname).suffix.lower().lstrip(".")
                    prioritized.append((priority, fname, ext))

                prioritized.sort(key=lambda x: -x[0])

                for priority, fname, ext in prioritized[:3]:
                    try:
                        decoded = self._decode_filename(fname)
                        safe = re.sub(r"[^\w\-_.]", "_", decoded)[:100]
                        extracted = temp_dir / safe

                        with z.open(fname) as src, open(extracted, "wb") as dst:
                            dst.write(src.read())

                        text = self._extract_by_extension(extracted, decoded, ext)
                        if text:
                            texts.append(f"=== ВЛОЖЕННЫЙ ФАЙЛ: {fname} ===\n{text}")
                            logger.info(f"[ZIP] Извлечено из {fname}: {len(text)} симв.")
                    except Exception as e:
                        logger.warning(f"[ZIP] Ошибка обработки {fname}: {e}")

        except zipfile.BadZipFile:
            logger.error(f"[ZIP] Повреждённый архив: {doc_name}")
            return ""
        except Exception as e:
            logger.error(f"[ZIP] Ошибка распаковки {doc_name}: {e}")
            return ""
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        result = "\n\n".join(texts)
        logger.info(f"[ZIP] Итого извлечено: {len(result)} символов")
        return result

    def _decode_filename(self, fname: str) -> str:
        """Декодирует имя файла из ZIP."""
        for encoding in ["utf-8", "cp866", "cp1251"]:
            try:
                return fname.encode("cp437").decode(encoding)
            except (UnicodeDecodeError, UnicodeEncodeError):
                continue
        return fname

    def _extract_by_extension(self, file_path: Path, doc_name: str, ext: str) -> str:
        """Извлекает текст по расширению файла."""
        if ext in ["docx", "doc"]:
            return self.docx_extractor.extract(file_path, doc_name)
        elif ext == "pdf":
            return self.pdf_extractor.extract(file_path)
        elif ext in ["xlsx", "xls"]:
            return self.excel_extractor.extract(file_path, doc_name)
        elif ext in ["txt", "rtf"]:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Ошибка текстового файла: {e}")
                return ""
        elif ext == "zip":
            return self.extract(file_path, doc_name, lambda x: 0, lambda x: False)
        else:
            return self._extract_by_content(file_path, doc_name)

    def _extract_by_content(self, file_path: Path, doc_name: str) -> str:
        """Определяет тип по содержимому и извлекает."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)
            if header.startswith(b"%PDF-"):
                return self.pdf_extractor.extract(file_path)
            elif header.startswith(b"\x50\x4b\x03\x04"):
                text = self.docx_extractor.extract(file_path, doc_name)
                if not text:
                    return self.excel_extractor.extract(file_path, doc_name)
                return text
            elif header.startswith(b"\xd0\xcf\x11\xe0"):
                return self.docx_extractor.extract(file_path, doc_name)
            else:
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        return f.read()
                except:
                    return ""
        except Exception as e:
            logger.error(f"[ZIP] Ошибка определения типа {doc_name}: {e}")
            return ""
