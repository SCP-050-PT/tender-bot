"""
core/document_processor.py
Скачивание и извлечение текста из файлов тендеров.
Поддерживает: docx, doc, pdf, xml, xlsx, html, rtf

Принцип: скачиваем все активные документы, извлекаем текст,
склеиваем в один блок и отдаём ИИ (YandexGPT) для анализа.
Никаких регулярных выражений — только ИИ.
"""

import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from loguru import logger


class DocumentProcessor:
    """
    Универсальный процессор документов тендеров.
    Скачивает файлы и извлекает plain text для передачи в LLM.
    """

    SUPPORTED_EXTENSIONS = {
        ".docx",
        ".doc",
        ".pdf",
        ".xml",
        ".xlsx",
        ".xls",
        ".html",
        ".htm",
        ".rtf",
    }

    def __init__(self, download_dir: Optional[Path] = None):
        self.download_dir = (
            Path(download_dir)
            if download_dir
            else Path(tempfile.gettempdir()) / "tender_docs"
        )
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
        )

    # ------------------------------------------------------------------------
    # DOWNLOAD
    # ------------------------------------------------------------------------

    def download(self, url: str, filename: Optional[str] = None) -> Optional[Path]:
        """
        Скачивает файл по URL. Определяет тип по Content-Type и Content-Disposition.
        Возвращает путь к сохранённому файлу.
        """
        try:
            response = self.session.get(
                url, timeout=60, stream=True, verify=False, allow_redirects=True
            )
            response.raise_for_status()

            # Определяем имя файла
            if not filename:
                # Пробуем Content-Disposition
                cd = response.headers.get("Content-Disposition") or ""
                fname_match = re.search(r'filename[^;=\n]*=[\'"]?([^\'"\n]*)[\'"]?', cd)
                if fname_match:
                    filename = fname_match.group(1)
                else:
                    # Из URL
                    parsed = urlparse(url)
                    filename = Path(parsed.path).name or f"doc_{int(time.time())}"

            # Определяем расширение по Content-Type если нет в имени
            file_type = self._detect_type_from_headers(response.headers)
            if file_type and not Path(filename).suffix:
                filename = f"{filename}.{file_type}"

            # Очищаем имя файла
            filename = re.sub(r'[<>":/\|?*]', "_", filename)
            if len(filename) > 100:
                filename = filename[:100]

            local_path = self.download_dir / filename

            # Сохраняем
            with open(local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(
                f"  ✅ Скачан: {filename} ({local_path.stat().st_size / 1024:.1f} KB)"
            )
            return local_path

        except Exception as e:
            logger.error(f"  ❌ Ошибка скачивания {url[:80]}: {e}")
            return None

    def _detect_type_from_headers(self, headers: Dict[str, str]) -> Optional[str]:
        """Определяет тип файла по HTTP-заголовкам."""
        content_type = headers.get("Content-Type", "").lower()

        if "pdf" in content_type:
            return "pdf"
        elif "word" in content_type or "officedocument" in content_type:
            return "docx"
        elif "excel" in content_type or "spreadsheet" in content_type:
            return "xlsx"
        elif "xml" in content_type:
            return "xml"
        elif "html" in content_type:
            return "html"
        elif "rtf" in content_type:
            return "rtf"

        # Пробуем Content-Disposition из тех же headers
        cd = headers.get("Content-Disposition") or ""
        fname_match = re.search(r'filename[^;=\n]*=[\'"]?([^\'"\n]*)[\'"]?', cd)
        if fname_match:
            ext = Path(fname_match.group(1)).suffix.lower()
            if ext:
                return ext.replace(".", "")

        return None

    # ------------------------------------------------------------------------
    # TEXT EXTRACTION
    # ------------------------------------------------------------------------

    def extract_text(self, file_path: Path) -> str:
        """
        Извлекает текст из файла любого поддерживаемого формата.
        Возвращает plain text для LLM.
        """
        ext = file_path.suffix.lower()

        if ext == ".docx":
            return self._extract_docx(file_path)
        elif ext == ".doc":
            return self._extract_doc(file_path)
        elif ext == ".pdf":
            return self._extract_pdf(file_path)
        elif ext in (".xml", ".html", ".htm"):
            return self._extract_xml(file_path)
        elif ext in (".xlsx", ".xls"):
            return self._extract_excel(file_path)
        elif ext == ".rtf":
            return self._extract_rtf(file_path)
        else:
            # Пробуем как текст
            try:
                return file_path.read_text(encoding="utf-8", errors="ignore")[:50000]
            except Exception:
                return ""

    def _extract_docx(self, file_path: Path) -> str:
        """Извлекает текст из docx."""
        try:
            from docx import Document

            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            # Также таблицы
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        text = cell.text.strip()
                        if text:
                            paragraphs.append(text)
            return "\n".join(paragraphs)
        except ImportError:
            logger.warning("  ⚠️ python-docx не установлен, пробуем pandoc")
            return self._extract_via_pandoc(file_path)
        except Exception as e:
            logger.error(f"  Ошибка docx: {e}")
            return ""

    def _extract_doc(self, file_path: Path) -> str:
        """Извлекает текст из .doc (старый формат)."""
        for tool in ["antiword", "pandoc"]:
            result = self._extract_via_tool(file_path, tool)
            if result:
                return result
        return ""

    def _extract_pdf(self, file_path: Path) -> str:
        """Извлекает текст из PDF."""
        try:
            import pdfplumber

            texts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        texts.append(text)
            return "\n\n".join(texts)
        except ImportError:
            logger.warning("  ⚠️ pdfplumber не установлен, пробуем pdftotext")
            result = self._extract_via_tool(file_path, "pdftotext")
            if result:
                return result
            # Fallback: пробуем через PyPDF2
            try:
                import PyPDF2

                texts = []
                with open(file_path, "rb") as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text:
                            texts.append(text)
                return "\n\n".join(texts)
            except ImportError:
                return ""
        except Exception as e:
            logger.error(f"  Ошибка PDF: {e}")
            return ""

    def _extract_xml(self, file_path: Path) -> str:
        """Извлекает текст из XML/HTML."""
        try:
            soup = BeautifulSoup(
                file_path.read_text(encoding="utf-8", errors="ignore"), "html.parser"
            )
            return soup.get_text(separator="\n", strip=True)
        except Exception as e:
            logger.error(f"  Ошибка XML: {e}")
            return ""

    def _extract_excel(self, file_path: Path) -> str:
        """Извлекает текст из Excel (для НМЦК-файлов)."""
        try:
            import openpyxl

            wb = openpyxl.load_workbook(file_path, data_only=True)
            texts = []
            for sheet in wb.worksheets:
                texts.append(f"=== Лист: {sheet.title} ===")
                for row in sheet.iter_rows(values_only=True):
                    row_text = " | ".join(str(cell) for cell in row if cell is not None)
                    if row_text.strip():
                        texts.append(row_text)
            return "\n".join(texts)
        except ImportError:
            logger.warning("  ⚠️ openpyxl не установлен")
            return ""
        except Exception as e:
            logger.error(f"  Ошибка Excel: {e}")
            return ""

    def _extract_rtf(self, file_path: Path) -> str:
        """Извлекает текст из RTF через pandoc."""
        return self._extract_via_pandoc(file_path)

    def _extract_via_pandoc(self, file_path: Path) -> str:
        """Конвертирует через pandoc в plain text."""
        return self._extract_via_tool(file_path, "pandoc", extra_args=["-t", "plain"])

    def _extract_via_tool(
        self, file_path: Path, tool: str, extra_args: Optional[List[str]] = None
    ) -> str:
        """Универсальный метод вызова внешних утилит."""
        try:
            cmd = [tool, str(file_path)]
            if extra_args:
                cmd.extend(extra_args)

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, errors="ignore"
            )
            if result.returncode == 0:
                return result.stdout
            else:
                logger.debug(f"  {tool} stderr: {result.stderr[:200]}")
                return ""
        except FileNotFoundError:
            logger.debug(f"  Утилита {tool} не найдена")
            return ""
        except subprocess.TimeoutExpired:
            logger.warning(f"  {tool} таймаут")
            return ""
        except Exception as e:
            logger.debug(f"  Ошибка {tool}: {e}")
            return ""

    # ------------------------------------------------------------------------
    # BATCH PROCESSING (для detailed_parser.py)
    # ------------------------------------------------------------------------

    def process_documents(self, documents: List[Any], max_docs: int = 5) -> str:
        """
        Скачивает и извлекает текст из списка документов.
        Склеивает всё в один текстовый блок для передачи ИИ.

        Args:
            documents: Список TenderDocument (или объектов с .url, .name, .is_active)
            max_docs: Максимум документов для обработки

        Returns:
            Склеенный текст всех документов
        """
        texts = []
        active_docs = [d for d in documents if getattr(d, "is_active", True)]

        logger.info(f"📄 Обработка документов: {len(active_docs)} активных")

        for i, doc in enumerate(active_docs[:max_docs], 1):
            url = getattr(doc, "url", "") or getattr(doc, "file_url", "")
            name = getattr(doc, "name", "") or getattr(doc, "file_name", f"doc_{i}")

            if not url:
                continue

            logger.info(f"  [{i}/{min(len(active_docs), max_docs)}] {name[:60]}")

            local_path = None
            try:
                # Скачиваем
                local_path = self.download(url, name)
                if not local_path:
                    continue

                # Извлекаем текст
                text = self.extract_text(local_path)
                if text and len(text) > 50:  # Минимум 50 символов
                    texts.append(f"\n=== ДОКУМЕНТ: {name} ===\n{text[:10000]}")
                    logger.info(f"    ✅ {len(text)} символов")
                else:
                    logger.warning(f"    ⚠️ Текст не извлечён или слишком короткий")

            except Exception as e:
                logger.error(f"    ❌ Ошибка обработки документа {name}: {e}")

            finally:
                # Удаляем временный файл — ВСЕГДА, даже при ошибке
                if local_path and local_path.exists():
                    try:
                        local_path.unlink()
                    except Exception:
                        pass

        result = "\n".join(texts)
        logger.info(
            f"📊 Итого извлечено: {len(result)} символов из {len(texts)} документов"
        )
        return result

    def cleanup(self):
        """Очищает временные файлы."""
        try:
            for f in self.download_dir.glob("*"):
                f.unlink()
            logger.info(f"🧹 Очищено: {self.download_dir}")
        except Exception as e:
            logger.warning(f"Ошибка очистки: {e}")
