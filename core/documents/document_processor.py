"""
core/documents/document_processor.py
Фасад обработки документов: делегирует извлечение специализированным экстракторам.
РЕФАКТОРИНГ (v6.6-r1):
  - DOCX в docx_extractor.py
  - Excel в excel_extractor.py
  - PDF в pdf_extractor.py
  - ZIP в zip_extractor.py
"""

import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
from loguru import logger

from core.documents.docx_extractor import DocxExtractor
from core.documents.excel_extractor import ExcelExtractor
from core.documents.pdf_extractor import PdfExtractor
from core.documents.zip_extractor import ZipExtractor


# === ЧЁРНЫЙ СПИСОК ФАЙЛОВ ===
CONTRACT_PATTERNS = [
    r"проект\s*контракта",
    r"проект\s*договора",
    r"\bконтракт\b",
    r"\bдоговор\b",
    r"\bконтракт\s*\S*",
    r"\bдоговор\s*\S*",
]

# === ПРИОРИТЕТЫ ФАЙЛОВ ===
FILE_PRIORITY = {
    r"техническое\s*задание|тз|техзадание|техническое\s*задани": 100,
    r"извещение|извещени": 90,
    r"документаци|документы|документ": 80,
    r"пояснительн|пояснительная": 70,
    r"заявка|заявление": 60,
    r"протокол": 50,
    r"решение|решен": 40,
    r"приложение": 30,
}

MAX_TEXT_LENGTH = 80000
MAX_CONTRACT_FILE_SIZE = 200 * 1024


@dataclass
class DocumentInfo:
    name: str
    url: str
    file_type: str = ""
    size: str = ""
    date: str = ""
    is_active: bool = True
    file_url: str = ""
    file_size_bytes: int = 0
    is_contract: bool = False
    priority: int = 0

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "url": self.url,
            "file_type": self.file_type,
            "size": self.size,
            "date": self.date,
            "is_active": self.is_active,
            "file_url": self.file_url,
            "is_contract": self.is_contract,
            "priority": self.priority,
        }


class DocumentProcessor:
    """Фасад обработки документов тендера."""

    def __init__(self, download_dir: Optional[Path] = None, session=None):
        self.download_dir = download_dir or Path(__file__).resolve().parent.parent / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.session = session

        # Экстракторы
        self.docx_extractor = DocxExtractor()
        self.excel_extractor = ExcelExtractor()
        self.pdf_extractor = PdfExtractor()
        self.zip_extractor = ZipExtractor(
            self.docx_extractor, self.pdf_extractor, self.excel_extractor
        )

        logger.info(f"DocumentProcessor инициализирован (v6.6-r1)")

    def process_documents(self, documents: List[DocumentInfo], max_docs: int = 5) -> str:
        """Обрабатывает список документов и возвращает объединённый текст."""
        if not documents:
            logger.warning("Нет документов для обработки")
            return ""

        active_docs = [d for d in documents if d.is_active]
        logger.info(f"Активных документов: {len(active_docs)}")

        # Классификация
        for doc in active_docs:
            doc.is_contract = self._is_contract_file(doc.name)
            doc.priority = self._get_file_priority(doc.name)
            if doc.is_contract:
                logger.info(f"Контракт/договор (пропускаем): {doc.name}")

        # Сортировка и фильтрация
        active_docs.sort(key=lambda d: (-d.priority, d.is_contract))
        docs_to_process = [d for d in active_docs if not d.is_contract][:max_docs]
        contract_docs = [d for d in active_docs if d.is_contract]

        logger.info(
            f"Обработать: {len(docs_to_process)} (контрактов пропущено: {len(contract_docs)})"
        )

        # Извлечение текста
        texts = []
        for doc in docs_to_process:
            try:
                text = self._process_single_document(doc)
                if text:
                    texts.append(f"=== ФАЙЛ: {doc.name} ===\n{text}")
            except Exception as e:
                logger.error(f"Ошибка обработки {doc.name}: {e}")

        # Примечание о контрактах
        if contract_docs:
            names = [d.name for d in contract_docs[:3]]
            texts.append(
                f"\n=== ВНИМАНИЕ: СРЕДИ ДОКУМЕНТОВ ЕСТЬ КОНТРАКТЫ/ДОГОВОРЫ ===\n"
                f"Пропущены: {', '.join(names)}"
            )
            if len(contract_docs) > 3:
                texts.append(f"... и ещё {len(contract_docs) - 3} файлов")

        result = "\n\n".join(texts)
        logger.info(f"Итоговый текст: {len(result)} символов")
        return result

    def _is_contract_file(self, filename: str) -> bool:
        if not filename:
            return False
        name_lower = filename.lower()
        return any(re.search(p, name_lower, re.IGNORECASE) for p in CONTRACT_PATTERNS)

    def _get_file_priority(self, filename: str) -> int:
        if not filename:
            return 0
        name_lower = filename.lower()
        return max(
            (priority for pattern, priority in FILE_PRIORITY.items()
             if re.search(pattern, name_lower, re.IGNORECASE)),
            default=0
        )

    def _process_single_document(self, doc: DocumentInfo) -> str:
        logger.info(f"Обработка: {doc.name} ({doc.file_type})")

        if doc.is_contract and doc.file_size_bytes > MAX_CONTRACT_FILE_SIZE:
            logger.info(f"Пропущен (контракт >200 KB): {doc.name}")
            return ""

        file_path = self._download_file(doc)
        if not file_path:
            return ""

        if not self._validate_file(file_path, doc.file_type):
            return ""

        text = self._extract_text(file_path, doc.file_type, doc.name)
        if text and len(text) > MAX_TEXT_LENGTH:
            logger.info(f"Обрезано с {len(text)} до {MAX_TEXT_LENGTH} символов")
            text = text[:MAX_TEXT_LENGTH] + "\n[... текст обрезан ...]"

        return text

    def _download_file(self, doc: DocumentInfo) -> Optional[Path]:
        """Скачивает файл через сессию."""
        if not doc.file_url:
            return None
        try:
            if self.session:
                response = self.session.get(doc.file_url, timeout=30)
            else:
                import requests
                response = requests.get(doc.file_url, timeout=30, verify=False)

            if response.status_code != 200:
                logger.warning(f"Статус {response.status_code}: {doc.file_url}")
                return None

            doc.file_size_bytes = len(response.content)
            safe_name = re.sub(r"[^\w\-_.]", "_", doc.name)[:100]
            ext = Path(doc.name).suffix
            file_path = self.download_dir / f"{safe_name}_{int(time.time())}{ext}"

            with open(file_path, "wb") as f:
                f.write(response.content)

            logger.info(f"Скачан: {file_path} ({doc.file_size_bytes} байт)")
            return file_path
        except Exception as e:
            logger.error(f"Ошибка скачивания: {e}")
            return None

    def _validate_file(self, file_path: Path, file_type: str) -> bool:
        """Проверяет магические байты файла."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)

            ext = (file_type.lower() if file_type else file_path.suffix.lower()).lstrip(".")

            if ext == "pdf" and not header.startswith(b"%PDF"):
                logger.error(f"Файл {file_path.name} — не PDF")
                return False
            elif ext in ["zip", "docx", "xlsx"] and not header.startswith(b"PK\x03\x04"):
                logger.error(f"Файл {file_path.name} — не ZIP")
                return False

            return True
        except Exception as e:
            logger.error(f"Ошибка проверки файла: {e}")
            return False

    def _extract_text(self, file_path: Path, file_type: str, doc_name: str) -> str:
        """Делегирует извлечение специализированному экстрактору."""
        ext = file_type.lower() if file_type else file_path.suffix.lower()

        if ext in ["docx", "doc"]:
            text = self.docx_extractor.extract(file_path, doc_name)
            if not text and ext == "doc":
                text = self.docx_extractor.extract(file_path, doc_name, force_docx=True)
            return text
        elif ext == "pdf":
            text = self.pdf_extractor.extract(file_path)
            if not text:
                # Проверка: может быть DOCX под видом PDF
                real_type = self._detect_by_magic(file_path)
                if real_type in ["doc", "docx"]:
                    logger.info(f"Файл {doc_name} — на самом деле {real_type}")
                    text = self.docx_extractor.extract(file_path, doc_name)
            return text
        elif ext in ["xlsx", "xls"]:
            return self.excel_extractor.extract(file_path, doc_name)
        elif ext == "zip":
            return self.zip_extractor.extract(
                file_path, doc_name, self._get_file_priority, self._is_contract_file
            )
        elif ext in ["txt", "rtf"]:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Ошибка текстового файла: {e}")
                return ""
        else:
            logger.warning(f"Неизвестный тип: {ext}")
            return ""

    def _detect_by_magic(self, file_path: Path) -> str:
        """Определяет тип файла по магическим байтам."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)
            if header.startswith(b"\x50\x4b\x03\x04"):
                return "zip"
            elif header.startswith(b"\xd0\xcf\x11\xe0"):
                return "doc"
            elif header.startswith(b"%PDF-"):
                return "pdf"
        except Exception as e:
            logger.error(f"Ошибка определения типа: {e}")
        return file_path.suffix.lower().lstrip(".") or "unknown"
