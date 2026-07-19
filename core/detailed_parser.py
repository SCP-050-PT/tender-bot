"""
core/detailed_parser.py
Детальный парсинг карточки закупки на zakupki.gov.ru.

Парсит:
- common-info.html — общая информация, заказчик, сроки, контакты
- documents.html — документы, статус, ссылки на файлы

Интеграция:
- Использует requests + BeautifulSoup
- Сохраняет результаты в TenderCache
- Передаёт извлечённый текст в LLM через YandexGPTClient
- Скачивание/извлечение текста делегирует DocumentProcessor
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config.settings import settings
from core.tender_cache import TenderCache, PurchaseState
from core.document_processor import DocumentProcessor
from utils.llm_client import YandexGPTClient
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================================
# DATACLASSES
# ============================================================================


@dataclass
class TenderDocument:
    """Документ из раздела documents.html."""

    name: str
    url: str
    file_type: str
    revision: int
    is_active: bool
    posted_at: str = ""


@dataclass
class TenderDetail:
    """Полная информация о закупке после детального парсинга."""

    reg_number: str
    law_type: str
    notice_guid: str = ""

    purchase_name: str = ""
    purchase_method: str = ""
    revision: int = 1
    revision_reason: str = ""
    publish_date: str = ""
    current_revision_date: str = ""

    customer_name: str = ""
    customer_inn: str = ""
    customer_kpp: str = ""
    customer_ogrn: str = ""
    customer_address: str = ""
    customer_postal_address: str = ""
    customer_region: str = ""

    contact_person: str = ""
    contact_email: str = ""
    contact_phone: str = ""

    deadline_date: str = ""
    deadline_timezone: str = ""
    results_date: str = ""
    submission_start_date: str = ""

    platform_name: str = ""
    platform_url: str = ""

    requirements: str = ""

    documents: List[TenderDocument] = field(default_factory=list)

    is_cancelled: bool = False
    has_winner: bool = False
    has_protocols: bool = False
    has_contract: bool = False
    has_clarifications: bool = False

    # Сырой текст ВСЕХ документов для ИИ
    documents_text: str = ""

    parsed_at: str = ""
    common_info_url: str = ""
    documents_url: str = ""

    # Доп. поля для анализа
    nmck: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "reg_number": self.reg_number,
            "law_type": self.law_type,
            "notice_guid": self.notice_guid,
            "purchase_name": self.purchase_name,
            "purchase_method": self.purchase_method,
            "revision": self.revision,
            "revision_reason": self.revision_reason,
            "publish_date": self.publish_date,
            "current_revision_date": self.current_revision_date,
            "customer_name": self.customer_name,
            "customer_inn": self.customer_inn,
            "customer_kpp": self.customer_kpp,
            "customer_ogrn": self.customer_ogrn,
            "customer_address": self.customer_address,
            "customer_region": self.customer_region,
            "contact_person": self.contact_person,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "deadline_date": self.deadline_date,
            "deadline_timezone": self.deadline_timezone,
            "results_date": self.results_date,
            "platform_name": self.platform_name,
            "platform_url": self.platform_url,
            "requirements": self.requirements,
            "is_cancelled": self.is_cancelled,
            "has_winner": self.has_winner,
            "has_protocols": self.has_protocols,
            "has_contract": self.has_contract,
            "has_clarifications": self.has_clarifications,
            "nmck": self.nmck,
            "documents_text": (
                self.documents_text[:500] + "..."
                if len(self.documents_text) > 500
                else self.documents_text
            ),
            "parsed_at": self.parsed_at,
            "documents_count": len(self.documents),
            "documents": [
                {
                    "name": d.name,
                    "url": d.url,
                    "type": d.file_type,
                    "active": d.is_active,
                }
                for d in self.documents
            ],
        }
        return result


# ============================================================================
# REGIONS
# ============================================================================

RUSSIAN_REGIONS = [
    "Москва",
    "Санкт-Петербург",
    "Севастополь",
    "Амурская область",
    "Архангельская область",
    "Астраханская область",
    "Белгородская область",
    "Брянская область",
    "Владимирская область",
    "Волгоградская область",
    "Вологодская область",
    "Воронежская область",
    "Ивановская область",
    "Иркутская область",
    "Калининградская область",
    "Калужская область",
    "Кемеровская область",
    "Кировская область",
    "Костромская область",
    "Курганская область",
    "Курская область",
    "Ленинградская область",
    "Липецкая область",
    "Магаданская область",
    "Московская область",
    "Мурманская область",
    "Нижегородская область",
    "Новгородская область",
    "Новосибирская область",
    "Омская область",
    "Оренбургская область",
    "Орловская область",
    "Пензенская область",
    "Псковская область",
    "Ростовская область",
    "Рязанская область",
    "Самарская область",
    "Саратовская область",
    "Сахалинская область",
    "Свердловская область",
    "Смоленская область",
    "Тамбовская область",
    "Тверская область",
    "Томская область",
    "Тульская область",
    "Тюменская область",
    "Ульяновская область",
    "Челябинская область",
    "Ярославская область",
    "Республика Адыгея",
    "Республика Алтай",
    "Республика Башкортостан",
    "Республика Бурятия",
    "Республика Дагестан",
    "Республика Ингушетия",
    "Республика Кабардино-Балкария",
    "Республика Калмыкия",
    "Республика Карачаево-Черкесия",
    "Республика Карелия",
    "Республика Коми",
    "Республика Крым",
    "Республика Марий Эл",
    "Республика Мордовия",
    "Республика Саха (Якутия)",
    "Республика Северная Осетия",
    "Республика Татарстан",
    "Республика Тыва",
    "Республика Удмуртия",
    "Республика Хакасия",
    "Республика Чечня",
    "Республика Чувашия",
    "Алтайский край",
    "Забайкальский край",
    "Камчатский край",
    "Краснодарский край",
    "Красноярский край",
    "Пермский край",
    "Приморский край",
    "Ставропольский край",
    "Хабаровский край",
    "Ненецкий автономный округ",
    "Ханты-Мансийский автономный округ",
    "Чукотский автономный округ",
    "Ямало-Ненецкий автономный округ",
    "Еврейская автономная область",
]


def extract_region_from_address(address: str) -> str:
    if not address:
        return ""
    address_lower = address.lower()
    for region in RUSSIAN_REGIONS:
        if region.lower() in address_lower:
            return region
    match = re.search(
        r"([А-Яа-я\s]+(?:область|край|республика|округ|автономная))",
        address,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip().title()
    return ""


# ============================================================================
# DETAILED PARSER
# ============================================================================


class DetailedParser:
    BASE_URL = "https://zakupki.gov.ru"
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 3
    RETRY_DELAY = 2

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    def __init__(self, cache: Optional[TenderCache] = None):
        self.cache = cache
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.llm_client: Optional[YandexGPTClient] = None
        self.doc_processor = DocumentProcessor()

        try:
            if settings.YANDEX_API_KEY and settings.YANDEX_FOLDER_ID:
                self.llm_client = YandexGPTClient()
        except Exception as e:
            logger.warning(f"LLM клиент не инициализирован: {e}")

    # ------------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------------

    def _fetch(self, url: str) -> Optional[BeautifulSoup]:
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                logger.debug(f"Загрузка {url} (попытка {attempt})")
                response = self.session.get(
                    url,
                    timeout=self.REQUEST_TIMEOUT,
                    verify=False,
                    allow_redirects=True,
                )
                response.raise_for_status()

                text_lower = response.text.lower()
                if (
                    "captcha" in text_lower
                    or "доступ ограничен" in text_lower
                    or "проверка безопасности" in text_lower
                ):
                    logger.warning(f"Капча или блокировка на {url}")
                    time.sleep(self.RETRY_DELAY * attempt)
                    continue

                return BeautifulSoup(response.text, "html.parser")

            except requests.exceptions.SSLError as e:
                logger.warning(f"SSL ошибка (попытка {attempt}): {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)
                continue

            except requests.exceptions.RequestException as e:
                logger.warning(f"Ошибка загрузки {url}: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY * attempt)
                else:
                    logger.error(
                        f"Не удалось загрузить {url} после {self.MAX_RETRIES} попыток"
                    )
                    return None
        return None

    def _extract_text(self, element) -> str:
        if element:
            return element.get_text(strip=True)
        return ""

    def _extract_text_by_title(self, soup: BeautifulSoup, title: str) -> str:
        for block in soup.find_all("div", class_="col-9 mr-auto"):
            title_elem = block.find("div", class_="common-text__title")
            if title_elem and title in title_elem.get_text(strip=True):
                value_elem = block.find("div", class_="common-text__value")
                return self._extract_text(value_elem)
        return ""

    # ------------------------------------------------------------------------
    # URL BUILDERS
    # ------------------------------------------------------------------------

    def _build_common_info_url(self, reg_number: str, law_type: str) -> str:
        if law_type == "223":
            return f"{self.BASE_URL}/223/purchase/public/purchase/info/common-info.html?regNumber={reg_number}"
        elif law_type == "44":
            return f"{self.BASE_URL}/epz/order/notice/ea44/view/common-info.html?regNumber={reg_number}"
        else:
            return f"{self.BASE_URL}/epz/order/notice/ea{law_type}/view/common-info.html?regNumber={reg_number}"

    def _build_documents_url(
        self, reg_number: str, law_type: str, notice_guid: str = ""
    ) -> str:
        if law_type == "223":
            if notice_guid:
                return f"{self.BASE_URL}/epz/order/notice/notice223/documents.html?purchaseNoticeNumber={reg_number}&noticeGuid={notice_guid}"
            else:
                return f"{self.BASE_URL}/epz/order/notice/notice223/documents.html?purchaseNoticeNumber={reg_number}"
        elif law_type == "44":
            return f"{self.BASE_URL}/epz/order/notice/ea44/view/documents.html?regNumber={reg_number}"
        else:
            return f"{self.BASE_URL}/epz/order/notice/ea{law_type}/view/documents.html?regNumber={reg_number}"

    # ------------------------------------------------------------------------
    # DOCUMENT PARSERS
    # ------------------------------------------------------------------------

    def _parse_documents(self, soup: BeautifulSoup, law_type: str) -> Dict[str, Any]:
        result = {
            "is_cancelled": False,
            "has_contract": False,
            "has_protocols": False,
            "has_winner": False,  # ИСПРАВЛЕНО: добавлен парсинг победителя
            "has_clarifications": False,
            "documents": [],
        }

        # 1. Парсим файлы
        if law_type == "223":
            result["documents"] = self._parse_223_documents(soup)
        else:
            result["documents"] = self._parse_44_documents(soup)

        # 2. Статусные блоки (универсально)
        status_blocks = []

        for block in soup.find_all("div", class_="card-attachments__block"):
            title = block.find("div", class_="title")
            if title:
                status_blocks.append((title.get_text(strip=True), block))

        for block in soup.find_all("div", class_="blockInfo"):
            title = block.find("h2", class_="blockInfo__title")
            if title:
                status_blocks.append((title.get_text(strip=True), block))

        for title_text, block in status_blocks:
            empty_indicators = block.find_all(
                string=re.compile(r"Сведения отсутствуют|Информация отсутствует")
            )
            has_data = len(empty_indicators) == 0

            if any(
                x in title_text
                for x in [
                    "Отмена закупки",
                    "Отмена определения поставщика",
                    "Отмена лотов",
                ]
            ):
                result["is_cancelled"] = has_data
            elif "Договор" in title_text:
                result["has_contract"] = has_data
            elif "Протокол" in title_text:
                result["has_protocols"] = has_data
                # ИСПРАВЛЕНО: проверяем протокол подведения итогов
                if "итог" in title_text.lower() or "подведения" in title_text.lower():
                    result["has_winner"] = has_data
            elif "Разъяснен" in title_text or "Уточнен" in title_text:
                result["has_clarifications"] = has_data

        return result

    def _parse_223_documents(self, soup: BeautifulSoup) -> List[TenderDocument]:
        documents = []
        doc_section = soup.find("section", class_="card-attachments")
        if not doc_section:
            logger.warning("  ⚠️ Не найден section.card-attachments для 223-ФЗ")
            return documents

        for block in doc_section.find_all("div", class_="card-attachments__block"):
            title_elem = block.find("div", class_="title")
            if not title_elem:
                continue

            title = title_elem.get_text(strip=True)
            if "Документация" not in title and "документ" not in title.lower():
                continue

            for att in block.find_all("div", class_=re.compile(r"\battachment\b")):
                revision = 1
                is_active = True

                for val_div in att.find_all("div", class_="attachment__value"):
                    text = val_div.get_text(strip=True)
                    match = re.search(r"Версия\s*№?\s*(\d+)", text)
                    if match:
                        revision = int(match.group(1))
                    if "Недействующая" in text:
                        is_active = False
                    elif "Действующая" in text:
                        is_active = True

                for link in att.find_all("a", href=True):
                    href = link.get("href", "")
                    if not href or "signview" in href or "listModal" in href:
                        continue

                    if href.startswith("/"):
                        href = f"{self.BASE_URL}{href}"
                    elif not href.startswith("http"):
                        href = f"{self.BASE_URL}/{href}"

                    name = link.get_text(strip=True)
                    if not name:
                        tooltip = link.get("data-tooltip", "")
                        if tooltip:
                            import html

                            name = html.unescape(tooltip).strip()
                    if not name:
                        name = link.get("title", "")

                    # Пропускаем ссылки на просмотр (view.html)
                    if "view.html" in href:
                        continue

                    file_type = self._detect_file_type(href, link)

                    doc = TenderDocument(
                        name=name or Path(href).name,
                        url=href,
                        file_type=file_type or "unknown",
                        revision=revision,
                        is_active=is_active,
                    )
                    documents.append(doc)

        return documents

    def _parse_44_documents(self, soup: BeautifulSoup) -> List[TenderDocument]:
        documents = []

        for doc_section in soup.find_all(
            "div", class_=re.compile(r"notice-documents|first-row-active-documents")
        ):
            revision = 1
            is_active = True

            doc_name = doc_section.find("div", class_="docName")
            if doc_name:
                text = doc_name.get_text()
                match = re.search(r"ред\.\s*(\d+)|версия\s*(\d+)", text, re.I)
                if match:
                    revision = int(match.group(1) or match.group(2))
                if doc_section.find("span", class_="inactiveElement"):
                    is_active = False

            status_elems = doc_section.find_all("div", class_="section__value")
            for elem in status_elems:
                text = elem.get_text(strip=True)
                if "Недействующая" in text:
                    is_active = False
                elif "Действующая" in text:
                    is_active = True

            files_block = doc_section.find("div", class_="blockFilesTabDocs")
            if files_block:
                for link in files_block.find_all("a", href=True):
                    href = link.get("href", "")
                    if not href or "signview" in href or "listModal" in href:
                        continue

                    if href.startswith("/"):
                        href = f"{self.BASE_URL}{href}"
                    elif not href.startswith("http"):
                        href = f"{self.BASE_URL}/{href}"

                    name = link.get_text(strip=True)
                    if not name:
                        name = link.get("title", "")

                    # Пропускаем view.html
                    if "view.html" in href:
                        continue

                    file_type = self._detect_file_type(href, link)

                    doc = TenderDocument(
                        name=name or Path(href).name,
                        url=href,
                        file_type=file_type or "unknown",
                        revision=revision,
                        is_active=is_active,
                    )
                    documents.append(doc)

        return documents

    def _detect_file_type(self, href: str, link) -> Optional[str]:
        ext = Path(href.split("?")[0]).suffix.lower()
        if ext == ".docx":
            return "docx"
        elif ext == ".doc":
            return "doc"
        elif ext == ".pdf":
            return "pdf"
        elif ext in (".xlsx", ".xls"):
            return "xlsx"
        elif ext == ".xml":
            return "xml"
        elif ext == ".zip":
            return "zip"
        elif ext == ".rar":
            return "rar"
        elif ext == ".rtf":
            return "rtf"

        data_ext = link.get("data-file-ext", "")
        if data_ext:
            return data_ext.lower().replace(".", "")

        img = link.find("img")
        if img:
            alt = img.get("alt", "").lower()
            src = img.get("src", "").lower()
            if "word" in alt or "doc" in src:
                return "docx"
            elif "pdf" in alt or "acrobat" in alt or "pdf" in src:
                return "pdf"
            elif "excel" in alt or "xls" in src:
                return "xlsx"

        link_classes = " ".join(link.get("class", [])).lower()
        if "doc" in link_classes:
            return "docx"
        elif "pdf" in link_classes:
            return "pdf"

        return None

    # ------------------------------------------------------------------------
    # COMMON-INFO PARSER
    # ------------------------------------------------------------------------

    def _parse_common_info_from_soup(self, soup: BeautifulSoup) -> Dict[str, Any]:
        result = {}

        result["purchase_name"] = self._extract_text_by_title(
            soup, "Наименование закупки"
        )
        result["purchase_method"] = self._extract_text_by_title(
            soup, "Способ осуществления закупки"
        )
        result["reg_number"] = self._extract_text_by_title(
            soup, "Реестровый номер извещения"
        )
        result["publish_date"] = self._extract_text_by_title(
            soup, "Дата размещения извещения"
        )
        result["current_revision_date"] = self._extract_text_by_title(
            soup, "Дата размещения текущей редакции извещения"
        )
        result["revision_reason"] = self._extract_text_by_title(
            soup, "Причина внесения изменений"
        )

        revision_text = self._extract_text_by_title(soup, "Редакция")
        try:
            result["revision"] = int(revision_text)
        except (ValueError, TypeError):
            result["revision"] = 1

        org_link = soup.find("a", href=re.compile(r"/epz/organization/view"))
        if org_link:
            result["customer_name"] = org_link.get_text(strip=True)

        result["customer_inn"] = self._extract_text_by_title(soup, "ИНН")
        result["customer_kpp"] = self._extract_text_by_title(soup, "КПП")
        result["customer_ogrn"] = self._extract_text_by_title(soup, "ОГРН")
        result["customer_address"] = self._extract_text_by_title(
            soup, "Место нахождения"
        )
        result["customer_postal_address"] = self._extract_text_by_title(
            soup, "Почтовый адрес"
        )
        result["customer_region"] = extract_region_from_address(
            result["customer_address"]
        )

        result["contact_person"] = self._extract_text_by_title(soup, "Контактное лицо")

        email_link = soup.find("a", href=re.compile(r"mailto:"))
        if email_link:
            result["contact_email"] = email_link.get("href", "").replace("mailto:", "")
        else:
            result["contact_email"] = self._extract_text_by_title(
                soup, "Адрес электронной почты"
            )

        result["contact_phone"] = self._extract_text_by_title(
            soup, "Контактный телефон"
        )

        result["submission_start_date"] = self._extract_text_by_title(
            soup, "Дата начала срока подачи заявок"
        )
        result["deadline_date"] = self._extract_text_by_title(
            soup, "Дата и время окончания срока подачи заявок"
        )
        result["results_date"] = self._extract_text_by_title(
            soup, "Дата подведения итогов"
        )

        deadline = result.get("deadline_date", "")
        tz_match = re.search(r"\(МСК([+-]\d+)\)", deadline)
        result["deadline_timezone"] = tz_match.group(1) if tz_match else ""

        result["platform_name"] = self._extract_text_by_title(
            soup, "Наименование электронной площадки"
        )
        platform_link = soup.find("a", href=re.compile(r"www\."))
        if platform_link:
            result["platform_url"] = platform_link.get_text(strip=True)

        # ИСПРАВЛЕНО: парсим НМЦК из common-info
        nmck_text = self._extract_text_by_title(soup, "Начальная максимальная цена")
        if not nmck_text:
            nmck_text = self._extract_text_by_title(soup, "НМЦК")
        if nmck_text:
            # Очищаем от пробелов и валюты
            cleaned = (
                nmck_text.replace(" ", "").replace("\xa0", "").replace("\u202f", "")
            )
            cleaned = cleaned.replace("₽", "").replace("руб.", "").replace("руб", "")
            match = re.search(r"([\d\s]+(?:[,.]\d{2})?)", cleaned)
            if match:
                try:
                    result["nmck"] = float(
                        match.group(1).replace(" ", "").replace(",", ".")
                    )
                except ValueError:
                    result["nmck"] = 0.0
        else:
            result["nmck"] = 0.0

        req_section = soup.find(
            "div", class_="common-text__caption", string=re.compile("Требования")
        )
        if req_section:
            req_value = req_section.find_next("div", class_="common-text__value")
            result["requirements"] = self._extract_text(req_value)

        return result

    # ------------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------------

    def analyze_with_llm(
        self, documents_text: str, tender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Передаёт текст документов в YandexGPT для анализа.
        Возвращает структурированные данные для calculator.py.

        ИСПРАВЛЕНО: nmck теперь передаётся из tender_info.
        """
        if not self.llm_client or not documents_text:
            return {}

        # ИСПРАВЛЕНО: nmck берётся из tender_info (если есть)
        nmck_value = tender_info.get("nmck", "")
        if nmck_value:
            nmck_str = f"{nmck_value:,.0f} ₽"
        else:
            nmck_str = "не указана"

        system_prompt = f"""Ты — аналитик тендеров компании "АС Безопасности".
Проанализируй текст документов закупки и извлеки ключевые параметры для расчёта стоимости.

Информация о закупке:
- Название: {tender_info.get("purchase_name", "")}
- Заказчик: {tender_info.get("customer_name", "")}
- Регион: {tender_info.get("customer_region", "")}
- НМЦК: {nmck_str}
- Срок подачи заявок: {tender_info.get("deadline_date", "")}

Верни СТРОГО JSON без markdown:
{{
  "tender_type": "sout|education|plk|opr|combined|unknown",
  "confidence": 0.0,
  "students_count": 0,
  "certificates": 0,
  "is_distance": true,
  "rm_total": 0,
  "rm_category_1": 0,
  "rm_category_2": 0,
  "iii_count": 0,
  "points_count": 0,
  "factors_count": 0,
  "delivery_count": 1,
  "is_annual": false,
  "needs_subcontractor": false,
  "needs_siz_norms": false,
  "needs_dsiz_norms": false,
  "needs_iot_norms": false,
  "deadline_days": 0,
  "addresses_count": 1,
  "has_venue": false,
  "urgency": "normal|high|critical",
  "special_requirements": [],
  "red_flags": [],
  "notes": ""
}}"""

        try:
            result = self.llm_client.send(
                system_prompt=system_prompt,
                user_message=documents_text[:15000],
                temperature=0.1,
                max_tokens=2000,
            )
            return result or {}
        except Exception as e:
            logger.error(f"Ошибка LLM-анализа: {e}")
            return {}

    # ------------------------------------------------------------------------
    # CORE PARSING LOGIC (вынесена из дублирования)
    # ------------------------------------------------------------------------

    def _extract_notice_guid(
        self, soup: BeautifulSoup, reg_number: str, law_type: str
    ) -> str:
        """Извлекает noticeGuid из common-info для 223-ФЗ."""
        if law_type != "223":
            return ""

        notice_guid = ""
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            guid_match = re.search(r"purchaseNoticeGuid=([a-f0-9-]+)", href)
            if guid_match:
                notice_guid = guid_match.group(1)
                break

        if not notice_guid:
            for script in soup.find_all("script"):
                text = script.string or ""
                guid_match = re.search(r'"noticeGuid"\s*:\s*"([a-f0-9-]+)"', text)
                if guid_match:
                    notice_guid = guid_match.group(1)
                    break

        return notice_guid

    def _build_detail(
        self,
        reg_number: str,
        law_type: str,
        notice_guid: str,
        docs_data: Dict[str, Any],
        common_data: Dict[str, Any],
        documents_text: str,
        common_info_url: str,
        documents_url: str,
    ) -> Optional[TenderDetail]:
        """
        Собирает TenderDetail из распарсенных данных.
        ИСПРАВЛЕНО: вынесена общая логика из parse() и parse_from_url().
        """
        if docs_data.get("is_cancelled"):
            logger.info(f"  ⏭ Закупка отменена: {reg_number}")
            if self.cache:
                self.cache.mark_empty(reg_number)
            return None

        if docs_data.get("has_contract"):
            logger.info(f"  ⏭ Договор уже заключён: {reg_number}")
            if self.cache:
                self.cache.mark_empty(reg_number)
            return None

        detail = TenderDetail(
            reg_number=reg_number,
            law_type=law_type,
            notice_guid=notice_guid,
            purchase_name=common_data.get("purchase_name", ""),
            purchase_method=common_data.get("purchase_method", ""),
            revision=common_data.get("revision", 1),
            revision_reason=common_data.get("revision_reason", ""),
            publish_date=common_data.get("publish_date", ""),
            current_revision_date=common_data.get("current_revision_date", ""),
            customer_name=common_data.get("customer_name", ""),
            customer_inn=common_data.get("customer_inn", ""),
            customer_kpp=common_data.get("customer_kpp", ""),
            customer_ogrn=common_data.get("customer_ogrn", ""),
            customer_address=common_data.get("customer_address", ""),
            customer_postal_address=common_data.get("customer_postal_address", ""),
            customer_region=common_data.get("customer_region", ""),
            contact_person=common_data.get("contact_person", ""),
            contact_email=common_data.get("contact_email", ""),
            contact_phone=common_data.get("contact_phone", ""),
            deadline_date=common_data.get("deadline_date", ""),
            deadline_timezone=common_data.get("deadline_timezone", ""),
            results_date=common_data.get("results_date", ""),
            submission_start_date=common_data.get("submission_start_date", ""),
            platform_name=common_data.get("platform_name", ""),
            platform_url=common_data.get("platform_url", ""),
            requirements=common_data.get("requirements", ""),
            documents=docs_data.get("documents", []),
            is_cancelled=docs_data.get("is_cancelled", False),
            has_winner=docs_data.get(
                "has_winner", False
            ),  # ИСПРАВЛЕНО: парсится из протоколов
            has_protocols=docs_data.get("has_protocols", False),
            has_contract=docs_data.get("has_contract", False),
            has_clarifications=docs_data.get("has_clarifications", False),
            documents_text=documents_text,
            parsed_at=datetime.now().isoformat(),
            common_info_url=common_info_url,
            documents_url=documents_url,
            nmck=common_data.get("nmck", 0.0),  # ИСПРАВЛЕНО: добавлено поле nmck
        )

        if self.cache:
            state = PurchaseState(
                reg_number=reg_number,
                last_update_date=detail.current_revision_date or detail.publish_date,
                protocol_count=len(detail.documents),
                last_protocol_date="",
                status="active" if not detail.is_cancelled else "cancelled",
                protocols_hash=TenderCache.get_protocols_hash(
                    [{"name": d.name} for d in detail.documents]
                ),
                checked_at=datetime.now().isoformat(),
                has_evaders=False,
                is_empty=detail.is_cancelled or detail.has_contract,
            )
            self.cache.set_purchase_state(state)

        logger.info(f"  ✅ Парсинг завершён: {reg_number}")
        logger.info(f"     Заказчик: {detail.customer_name[:50]}...")
        logger.info(f"     Регион: {detail.customer_region}")
        logger.info(f"     Дедлайн: {detail.deadline_date}")
        logger.info(f"     НМЦК: {detail.nmck:,.0f} ₽")
        logger.info(f"     Документов: {len(detail.documents)}")
        logger.info(f"     Текст документов: {len(detail.documents_text)} символов")

        return detail

    # ------------------------------------------------------------------------
    # MAIN ENTRY
    # ------------------------------------------------------------------------

    def parse(
        self, reg_number: str, law_type: str, notice_guid: str = "", nmck: float = None
    ) -> Optional[TenderDetail]:
        logger.info(f"🔍 Детальный парсинг: {reg_number} ({law_type}-ФЗ)")

        # === ШАГ 0: Для 223-ФЗ извлекаем noticeGuid ===
        if law_type == "223" and not notice_guid:
            logger.info("  🔎 Извлечение noticeGuid из common-info...")
            common_info_url = self._build_common_info_url(reg_number, law_type)
            common_soup = self._fetch(common_info_url)
            if common_soup:
                notice_guid = self._extract_notice_guid(
                    common_soup, reg_number, law_type
                )
                logger.info(
                    f"  ✅ noticeGuid: {notice_guid[:8]}..."
                    if notice_guid
                    else "  ⚠️ noticeGuid не найден"
                )

        # === ШАГ 1: documents.html ===
        logger.info("  📄 Парсинг documents.html...")
        documents_url = self._build_documents_url(reg_number, law_type, notice_guid)
        docs_soup = self._fetch(documents_url)

        if not docs_soup:
            logger.error(f"  ❌ Не удалось загрузить documents")
            return None

        docs_data = self._parse_documents(docs_soup, law_type)

        # === ШАГ 2: common-info.html ===
        logger.info("  📄 Парсинг common-info.html...")
        common_info_url = self._build_common_info_url(reg_number, law_type)
        common_soup = self._fetch(common_info_url)

        if not common_soup:
            logger.error(f"  ❌ Не удалось загрузить common-info")
            return None

        common_data = self._parse_common_info_from_soup(common_soup)

        # ИСПРАВЛЕНО: если nmck передан извне — используем его
        if nmck is not None:
            common_data["nmck"] = nmck

        # === ШАГ 3: Скачиваем и извлекаем текст из документов через DocumentProcessor ===
        documents_text = ""
        active_docs = [d for d in docs_data.get("documents", []) if d.is_active]
        if active_docs:
            logger.info(f"  📥 Активных документов: {len(active_docs)}")
            documents_text = self.doc_processor.process_documents(
                active_docs, max_docs=5
            )
        else:
            logger.info("  ℹ️ Активных документов нет")

        # === ШАГ 4: LLM-анализ (опционально) ===
        llm_analysis = {}
        if documents_text and self.llm_client:
            logger.info("  🤖 LLM-анализ документов...")
            llm_analysis = self.analyze_with_llm(
                documents_text,
                {
                    "purchase_name": common_data.get("purchase_name", ""),
                    "customer_name": common_data.get("customer_name", ""),
                    "customer_region": common_data.get("customer_region", ""),
                    "nmck": common_data.get("nmck", 0),
                    "deadline_date": common_data.get("deadline_date", ""),
                },
            )
            if llm_analysis:
                logger.info(
                    f"  ✅ LLM-анализ завершён: {llm_analysis.get('tender_type', 'unknown')}"
                )

        # === Сборка через общий метод ===
        return self._build_detail(
            reg_number=reg_number,
            law_type=law_type,
            notice_guid=notice_guid,
            docs_data=docs_data,
            common_data=common_data,
            documents_text=documents_text,
            common_info_url=common_info_url,
            documents_url=documents_url,
        )

    def parse_from_url(
        self,
        reg_number: str,
        law_type: str,
        common_info_url: str,
        notice_guid: str = "",
        nmck: float = None,
    ) -> Optional[TenderDetail]:
        """Парсит закупку, используя готовый URL из поисковой выдачи."""
        law_clean = re.search(r"(\d+)", law_type)
        law_type = law_clean.group(1) if law_clean else law_type

        logger.info(f"🔍 Детальный парсинг (from URL): {reg_number} ({law_type})")

        # === ШАГ 0: Для 223-ФЗ извлекаем noticeGuid ===
        if law_type == "223" and not notice_guid:
            logger.info("  🔎 Извлечение noticeGuid из common-info...")
            common_soup = self._fetch(common_info_url)
            if common_soup:
                notice_guid = self._extract_notice_guid(
                    common_soup, reg_number, law_type
                )
                logger.info(
                    f"  ✅ noticeGuid: {notice_guid[:8]}..."
                    if notice_guid
                    else "  ⚠️ noticeGuid не найден"
                )

        # === ШАГ 1: documents.html ===
        logger.info("  📄 Парсинг documents.html...")
        documents_url = self._build_documents_url(reg_number, law_type, notice_guid)
        docs_soup = self._fetch(documents_url)

        if not docs_soup:
            logger.error(f"  ❌ Не удалось загрузить documents: {documents_url}")
            return None

        docs_data = self._parse_documents(docs_soup, law_type)

        # === ШАГ 2: common-info.html ===
        logger.info("  📄 Парсинг common-info.html...")
        common_soup = self._fetch(common_info_url)
        if not common_soup:
            logger.error(f"  ❌ Не удалось загрузить common-info")
            return None
        common_data = self._parse_common_info_from_soup(common_soup)

        # ИСПРАВЛЕНО: если nmck передан извне — используем его
        if nmck is not None:
            common_data["nmck"] = nmck

        # === ШАГ 3: Скачиваем документы через DocumentProcessor ===
        documents_text = ""
        active_docs = [d for d in docs_data.get("documents", []) if d.is_active]
        if active_docs:
            logger.info(f"  📥 Активных документов: {len(active_docs)}")
            documents_text = self.doc_processor.process_documents(
                active_docs, max_docs=5
            )
        else:
            logger.info("  ℹ️ Активных документов нет")

        # === Сборка через общий метод ===
        return self._build_detail(
            reg_number=reg_number,
            law_type=law_type,
            notice_guid=notice_guid,
            docs_data=docs_data,
            common_data=common_data,
            documents_text=documents_text,
            common_info_url=common_info_url,
            documents_url=documents_url,
        )

    def parse_multiple(self, tenders: List[Dict[str, str]]) -> List[TenderDetail]:
        results = []
        for i, tender in enumerate(tenders):
            logger.info(f"[{i+1}/{len(tenders)}] Обработка {tender['reg_number']}")
            if self.cache:
                cached = self.cache.get_purchase_state(tender["reg_number"])
                if cached and cached.is_empty:
                    logger.info(f"  ⏭ Пропуск (в кэше как пустая)")
                    continue
            detail = self.parse(
                reg_number=tender["reg_number"],
                law_type=tender["law_type"],
                notice_guid=tender.get("notice_guid", ""),
                nmck=tender.get("nmck"), 
            )
            if detail:
                results.append(detail)
            if i < len(tenders) - 1:
                time.sleep(2)
        return results
