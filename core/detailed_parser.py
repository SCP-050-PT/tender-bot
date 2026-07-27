"""
core/detailed_parser.py
Детальный парсинг карточки тендера (common-info + documents).
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - Рефакторинг: использует tender_type, param_extractor, url_builder, price_parser, http_session
  - Убраны: EXTRACTION_PATTERNS, TYPE_PATTERNS, inline промпт, дубли сессии
  - cities_count через единый _count_addresses()
  - URL через url_builder
  - Цена через price_parser
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin
from datetime import datetime

from bs4 import BeautifulSoup
from loguru import logger

from core.http_session import get_session_manager
from core.document_processor import DocumentProcessor
from core.tender_cache import TenderCache
from utils.url_builder import get_url_builder
from utils.price_parser import get_price_parser
from core.tender_type import get_type_detector


@dataclass
class TenderDocument:
    """Документ тендера (ТЗ, извещение, КД)."""

    name: str
    url: str
    file_type: str = ""
    size: str = ""
    date: str = ""
    is_active: bool = True
    file_url: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "url": self.url,
            "file_type": self.file_type,
            "size": self.size,
            "date": self.date,
            "is_active": self.is_active,
            "file_url": self.file_url,
        }


@dataclass
class TenderDetail:
    """Детальная информация о тендере."""

    reg_number: str = ""
    law_type: str = ""
    purchase_name: str = ""
    purchase_method: str = ""
    nmck: float = 0.0
    customer_name: str = ""
    customer_inn: str = ""
    customer_region: str = ""
    customer_address: str = ""
    publish_date: str = ""
    deadline_date: str = ""
    current_revision_date: str = ""
    platform_name: str = ""
    platform_url: str = ""
    requirements: str = ""
    documents: List[TenderDocument] = field(default_factory=list)
    documents_text: str = ""
    common_info_url: str = ""
    documents_url: str = ""
    application_guarantee: str = ""
    contract_guarantee: str = ""
    guarantee_method: str = ""
    contact_person: str = ""
    contact_email: str = ""
    contact_phone: str = ""

    # Извлечённые параметры
    rm_total: int = 0
    rm_category_1: int = 0
    rm_category_2: int = 0
    rm_with_iii: int = 0
    points_count: int = 0
    students_count: int = 0
    factors_count: int = 0
    addresses_count: int = 0
    cities_count: int = 0
    deadline_days: int = 0
    has_full_time: bool = False
    has_polygon: bool = False
    is_urgent: bool = False
    needs_siz_norms: bool = False
    needs_dsiz_norms: bool = False
    needs_iot_norms: bool = False
    needs_subcontractor: bool = False
    tender_type: str = ""

    # Очные параметры обучения
    teacher_days: int = 0
    accommodation_nights: int = 0
    transport_km: int = 0
    venue_rent_days: int = 0
    manikin_days: int = 0

    # Параметры командировок СОУТ
    trip_days: int = 0

    # ОПР и сезонность
    opr_positions: int = 0
    opr_persons: int = 0
    is_seasonal: bool = False

    def to_dict(self) -> Dict:
        return {
            "reg_number": self.reg_number,
            "law_type": self.law_type,
            "purchase_name": self.purchase_name,
            "purchase_method": self.purchase_method,
            "nmck": self.nmck,
            "customer_name": self.customer_name,
            "customer_inn": self.customer_inn,
            "customer_region": self.customer_region,
            "customer_address": self.customer_address,
            "publish_date": self.publish_date,
            "deadline_date": self.deadline_date,
            "current_revision_date": self.current_revision_date,
            "platform_name": self.platform_name,
            "platform_url": self.platform_url,
            "requirements": self.requirements,
            "documents": [d.to_dict() for d in self.documents],
            "documents_text": (
                self.documents_text[:500] + "..."
                if len(self.documents_text) > 500
                else self.documents_text
            ),
            "common_info_url": self.common_info_url,
            "documents_url": self.documents_url,
            "application_guarantee": self.application_guarantee,
            "contract_guarantee": self.contract_guarantee,
            "guarantee_method": self.guarantee_method,
            "contact_person": self.contact_person,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "rm_total": self.rm_total,
            "rm_category_1": self.rm_category_1,
            "rm_category_2": self.rm_category_2,
            "rm_with_iii": self.rm_with_iii,
            "points_count": self.points_count,
            "students_count": self.students_count,
            "factors_count": self.factors_count,
            "addresses_count": self.addresses_count,
            "cities_count": self.cities_count,
            "deadline_days": self.deadline_days,
            "has_full_time": self.has_full_time,
            "has_polygon": self.has_polygon,
            "is_urgent": self.is_urgent,
            "needs_siz_norms": self.needs_siz_norms,
            "needs_dsiz_norms": self.needs_dsiz_norms,
            "needs_iot_norms": self.needs_iot_norms,
            "needs_subcontractor": self.needs_subcontractor,
            "tender_type": self.tender_type,
            "teacher_days": self.teacher_days,
            "accommodation_nights": self.accommodation_nights,
            "transport_km": self.transport_km,
            "venue_rent_days": self.venue_rent_days,
            "manikin_days": self.manikin_days,
            "trip_days": self.trip_days,
            "opr_positions": self.opr_positions,
            "opr_persons": self.opr_persons,
            "is_seasonal": self.is_seasonal,
        }


class DetailedParser:
    """
    Детальный парсер карточки тендера.
    Поддерживает 44-ФЗ, 223-ФЗ.
    """

    BASE_URL = "https://zakupki.gov.ru"
    REQUEST_DELAY = (1, 3)

    # Паттерны сезонности (остались — специфичны для HTML-парсинга)
    SEASONAL_PATTERNS = [
        r"отопительный[\s]+сезон",
        r"сезонных[\s]+рабочих[\s]+мест",
        r"период[\s]+их[\s]+фактического[\s]+функционирования",
    ]

    def __init__(self, cache: Optional[TenderCache] = None):
        self.cache = cache
        self.url_builder = get_url_builder()
        self.price_parser = get_price_parser()
        self.type_detector = get_type_detector()

        # Единая сессия из http_session
        self.session_manager = get_session_manager(pool_size=1)
        self.session = self.session_manager.get_primary_session()

        # DocumentProcessor с переданной сессией
        self.doc_processor = DocumentProcessor(session=self.session)

        logger.info("DetailedParser инициализирован (v6.3)")

    def parse(
        self,
        reg_number: str,
        law_type: str = "223",
        notice_guid: str = "",
        nmck: float = None,
    ) -> Optional[TenderDetail]:
        """Парсит детальную информацию о тендере."""
        logger.info(f"🔍 Детальный парсинг: {reg_number} ({law_type}-ФЗ)")

        detail = TenderDetail(
            reg_number=reg_number,
            law_type=law_type,
            nmck=nmck or 0.0,
        )

        # === Шаг 1: Common Info ===
        common_info = self._fetch_common_info(reg_number, law_type, notice_guid)
        if not common_info:
            logger.warning(f"⚠️ Не удалось загрузить common-info для {reg_number}")
            return None

        detail.common_info_url = common_info["url"]
        soup = BeautifulSoup(common_info["html"], "html.parser")

        if law_type == "44":
            parsed_info = self._parse_common_info_44(soup)
        else:
            parsed_info = self._parse_common_info_223(soup)

        for key, value in parsed_info.items():
            if hasattr(detail, key) and value:
                setattr(detail, key, value)

        if not detail.nmck and parsed_info.get("nmck"):
            detail.nmck = parsed_info["nmck"]

        logger.info(
            f"   ✅ Common-info: {detail.purchase_name[:60] if detail.purchase_name else 'N/A'}..."
        )
        logger.info(f"   📍 Регион: {detail.customer_region or 'не определён'}")
        logger.info(f"   🏢 ЭТП: {detail.platform_name or 'не определена'}")
        logger.info(f"   📋 Требования: {'есть' if detail.requirements else 'нет'}")
        logger.info(
            f"   🔒 Обеспечение заявки: {detail.application_guarantee or 'не указано'}"
        )
        logger.info(
            f"   🔒 Обеспечение контракта: {detail.contract_guarantee or 'не указано'}"
        )
        logger.info(f"   📍 Городов поставки: {detail.cities_count or 0}")
        logger.info(f"   📍 Адресов поставки: {detail.addresses_count or 0}")

        # === Шаг 2: Документы ===
        docs_info = self._fetch_documents(reg_number, law_type, notice_guid)
        if docs_info:
            detail.documents_url = docs_info["url"]
            docs_soup = BeautifulSoup(docs_info["html"], "html.parser")
            if law_type == "44":
                detail.documents = self._parse_documents_44(docs_soup)
            else:
                detail.documents = self._parse_documents_223(docs_soup)
            logger.info(f"   📄 Документов: {len(detail.documents)}")

            active_docs = [d for d in detail.documents if d.is_active]
            logger.info(f"   📄 Активных документов: {len(active_docs)}")

            if detail.documents:
                detail.documents_text = self.doc_processor.process_documents(
                    detail.documents, max_docs=3
                )
                logger.info(
                    f"   📝 Текст документов: {len(detail.documents_text)} символов"
                )

        # === Шаг 3: Извлечение параметров из текста ===
        full_text = (
            f"{detail.purchase_name} {detail.requirements} {detail.documents_text}"
        )
        self._extract_params_from_text(detail, full_text)

        # После извлечения определяем tender_type окончательно
        # и пересчитываем cities_count если тип изменился
        if (
            detail.tender_type in ("sout", "combined", "соут", "комбинированный")
            and detail.cities_count == 0
        ):
            if detail.customer_address:
                detail.cities_count = self._count_addresses(
                    detail.customer_address, detail.tender_type
                )
                detail.addresses_count = detail.cities_count
                logger.info(
                    f"   [v6.3] Пересчёт cities_count после определения типа: {detail.cities_count}"
                )

        # === Шаг 4: Кэширование ===
        if self.cache:
            self._save_to_cache(detail)

        return detail

    def _extract_params_from_text(self, detail: TenderDetail, text: str):
        """Извлекает параметры из текста без LLM."""
        if not text or len(text) < 50:
            return

        text_lower = text.lower()

        # Используем tender_type detector для определения типа
        type_result = self.type_detector.detect(text)
        detail.tender_type = type_result.tender_type

        # Извлекаем числовые параметры через простые regex (специфичные для HTML)
        # RM
        rm_match = re.search(r"(\d+)\s*рабочих\s*мест", text_lower)
        if rm_match:
            detail.rm_total = int(rm_match.group(1))

        # Students
        students_match = re.search(r"(\d+)\s*слушател", text_lower)
        if students_match:
            detail.students_count = int(students_match.group(1))

        # Points
        points_match = re.search(r"(\d+)\s*точек", text_lower)
        if points_match:
            detail.points_count = int(points_match.group(1))

        # Addresses — через _count_addresses (единый метод)
        if detail.customer_address:
            detail.cities_count = self._count_addresses(
                detail.customer_address, detail.tender_type
            )
            detail.addresses_count = detail.cities_count

        # Trip days
        trip_match = re.search(
            r"(?:срок|длительность)\s*(?:выезда|командировки)\s*[\-—]?\s*(\d+)\s*дн",
            text_lower,
        )
        if trip_match:
            detail.trip_days = int(trip_match.group(1))

        # Очные параметры
        teacher_match = re.search(
            r"преподавател[ья]\s*(?:работ[аеы]\s*)?(\d+)\s*дн", text_lower
        )
        if teacher_match:
            detail.teacher_days = int(teacher_match.group(1))

        acc_match = re.search(
            r"проживани[ея]\s*(?:в\s+гостинице)?\s*(\d+)\s*ноч", text_lower
        )
        if acc_match:
            detail.accommodation_nights = int(acc_match.group(1))

        km_match = re.search(r"расстояни[ея]\s*[\-—]?\s*(\d+)\s*км", text_lower)
        if km_match:
            detail.transport_km = int(km_match.group(1))

        venue_match = re.search(r"аренд[аы]\s*помещени[ея]\s*(\d+)\s*дн", text_lower)
        if venue_match:
            detail.venue_rent_days = int(venue_match.group(1))

        manikin_match = re.search(r"манекен[аовы]\s*(?:на\s+)?(\d+)\s*дн", text_lower)
        if manikin_match:
            detail.manikin_days = int(manikin_match.group(1))

        # ОПР-параметры
        opr_pos_match = re.search(r"(\d+)\s*должност[ейь]", text_lower)
        if opr_pos_match:
            detail.opr_positions = int(opr_pos_match.group(1))

        opr_per_match = re.search(
            r"(?:численность|человек|работников)\s*[\-—]?\s*(\d+)", text_lower
        )
        if opr_per_match:
            detail.opr_persons = int(opr_per_match.group(1))

        # Проверяем очную часть / полигон
        detail.has_full_time = bool(
            re.search(
                r"очная[\s]+форма|очно|полигон|практическ[аяоеуюй][\s]+часть|манекен",
                text_lower,
                re.IGNORECASE,
            )
        )
        detail.has_polygon = "полигон" in text_lower

        # Проверяем срочность
        urgent_match = re.search(
            r"срок[\s]*(?:исполнения|поставки)[\s]*[\-—]?[\s]*(\d+)[\s]*дней",
            text_lower,
            re.IGNORECASE,
        )
        if urgent_match:
            days = int(urgent_match.group(1))
            detail.is_urgent = days <= 14

        # Проверяем нормы
        detail.needs_siz_norms = bool(
            re.search(
                r"норм[ыы][\s]+СИЗ|средств[аы][\s]+индивидуальной[\s]*защиты",
                text_lower,
                re.IGNORECASE,
            )
        )
        detail.needs_dsiz_norms = "дсиз" in text_lower
        detail.needs_iot_norms = bool(
            re.search(
                r"ИОТ|инструкции[\s]+по[\s]+охране[\s]+труда", text_lower, re.IGNORECASE
            )
        )

        # Проверяем субподряд
        detail.needs_subcontractor = bool(
            re.search(
                r"радиация|ионизирующ|рентген|асбест|кадмий|хром[\s]*\(?vi?\)?|никель",
                text_lower,
                re.IGNORECASE,
            )
        )

        # Сезонность
        detail.is_seasonal = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.SEASONAL_PATTERNS
        )
        if detail.is_seasonal:
            logger.info("   ❄️ Обнаружена сезонность")

        logger.info(
            f"   📊 Извлечено: РМ={detail.rm_total}, кат.1={detail.rm_category_1}, "
            f"кат.2={detail.rm_category_2}, ИИИ={detail.rm_with_iii}, "
            f"точек={detail.points_count}, слушателей={detail.students_count}, "
            f"тип={detail.tender_type}, городов={detail.cities_count}, "
            f"адресов={detail.addresses_count}, дней_выезда={detail.trip_days}, "
            f"сезон={detail.is_seasonal}, opr_pos={detail.opr_positions}, opr_per={detail.opr_persons}"
        )

    # ============ FETCH METHODS (через url_builder + http_session) ============

    def _fetch_common_info(
        self, reg_number: str, law_type: str, notice_guid: str = ""
    ) -> Optional[Dict[str, str]]:
        url = self.url_builder.build_common_info_url(reg_number, law_type, notice_guid)
        return self._fetch_page(url)

    def _fetch_documents(
        self, reg_number: str, law_type: str, notice_guid: str = ""
    ) -> Optional[Dict[str, str]]:
        url = self.url_builder.build_documents_url(reg_number, law_type, notice_guid)
        return self._fetch_page(url)

    def _fetch_page(self, url: str, retries: int = 3) -> Optional[Dict[str, str]]:
        """Использует единую сессию из http_session."""
        import random

        time.sleep(random.uniform(*self.REQUEST_DELAY))

        for attempt in range(retries):
            try:
                response = self.session.get(url, timeout=30)
                if response.status_code == 200:
                    return {"url": url, "html": response.text}
                elif response.status_code == 429:
                    logger.warning(f"   ⏳ 429, ждём...")
                    time.sleep(5 * (attempt + 1))
                else:
                    logger.warning(f"   ⚠️ Статус {response.status_code}")
            except Exception as e:
                logger.error(f"   ❌ Ошибка загрузки: {e}")
                time.sleep(2)

        return None

    # ============ 223-FZ PARSERS ============

    def _parse_common_info_223(self, soup: BeautifulSoup) -> Dict[str, Any]:
        result = {}

        result["purchase_name"] = self._extract_text_by_title(
            soup, "Наименование закупки"
        ) or self._extract_text_by_title(soup, "Объект закупки")
        result["purchase_method"] = self._extract_text_by_title(
            soup, "Способ осуществления закупки"
        )
        result["customer_name"] = self._extract_text_by_title(
            soup, "Наименование организации"
        )
        result["customer_inn"] = self._extract_inn_from_soup(soup)
        result["customer_address"] = self._extract_text_by_title(
            soup, "Место нахождения"
        ) or self._extract_text_by_title(soup, "Почтовый адрес")

        if result.get("customer_address"):
            cities = self._count_addresses(result["customer_address"], "")
            result["cities_count"] = cities
            result["addresses_count"] = cities

        result["customer_region"] = self._extract_region_from_address(
            result.get("customer_address", "")
        )

        price_text = self._extract_text_by_title(soup, "Начальная цена")
        if price_text:
            result["nmck"] = self.price_parser.parse(price_text) or 0.0

        result["publish_date"] = self._extract_text_by_title(
            soup, "Дата размещения извещения"
        ) or self._extract_text_by_title(soup, "Дата размещения")
        result["deadline_date"] = self._extract_text_by_title(
            soup, "Дата и время окончания срока подачи заявок"
        ) or self._extract_text_by_title(soup, "Окончание подачи заявок")
        result["current_revision_date"] = self._extract_text_by_title(
            soup, "Дата размещения текущей редакции извещения"
        )

        result["platform_name"] = self._extract_text_by_title(
            soup, "Наименование электронной площадки"
        )
        result["platform_url"] = self._extract_url_by_title(
            soup, "Адрес электронной площадки"
        )

        result["requirements"] = self._extract_text_by_title(
            soup, "Требования к участникам закупки"
        )

        result["contact_person"] = self._extract_text_by_title(soup, "Контактное лицо")
        result["contact_email"] = self._extract_email_from_soup(soup)
        result["contact_phone"] = self._extract_text_by_title(
            soup, "Контактный телефон"
        )

        return result

    def _parse_documents_223(self, soup: BeautifulSoup) -> List[TenderDocument]:
        """Парсит документы для 223-ФЗ."""
        documents = []

        for block in soup.find_all("div", class_="card-attachments-container"):
            for attachment in block.find_all("div", class_="attachment"):
                doc = self._parse_attachment_223(attachment)
                if doc:
                    documents.append(doc)

        return documents

    def _parse_attachment_223(self, attachment) -> Optional[TenderDocument]:
        """Парсит одно вложение 223-ФЗ."""
        try:
            name_elem = attachment.find("div", class_="attachment__value")
            if not name_elem:
                return None
            name = name_elem.get_text(strip=True)

            is_active = True
            status_elem = attachment.find(
                "div",
                class_="attachment__value",
                string=re.compile(r"Действующая|Недействующая"),
            )
            if status_elem:
                is_active = "Действующая" in status_elem.get_text(strip=True)

            date = ""
            date_text = attachment.find(
                "div", class_="attachment__text", string="Размещено"
            )
            if date_text:
                date_val = date_text.find_next("div", class_="attachment__value")
                if date_val:
                    date = date_val.get_text(strip=True)

            file_docs = []
            for file_link in attachment.find_all(
                "a", href=re.compile(r"filestore|download")
            ):
                href = file_link.get("href", "")
                if not href:
                    continue

                file_url = urljoin(self.BASE_URL, href)
                file_type = ""
                img = file_link.find_previous("img", src=re.compile(r"/type/"))
                if not img:
                    img = file_link.find_parent().find("img", src=re.compile(r"/type/"))

                if img:
                    src = img.get("src", "")
                    file_type = self._detect_file_type(src)

                file_name = file_link.get("title", "") or file_link.get_text(strip=True)
                if not file_name:
                    file_name = name

                doc = TenderDocument(
                    name=file_name or name,
                    url=file_url,
                    file_type=file_type,
                    date=date,
                    is_active=is_active,
                    file_url=file_url,
                )
                file_docs.append(doc)

            if not file_docs:
                return TenderDocument(
                    name=name,
                    url="",
                    file_type="",
                    date=date,
                    is_active=is_active,
                    file_url="",
                )

            return file_docs[0] if file_docs else None

        except Exception as e:
            logger.debug(f"Ошибка парсинга документа 223-ФЗ: {e}")
            return None

    # ============ 44-FZ PARSERS ============

    def _parse_common_info_44(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Парсит common-info для 44-ФЗ."""
        result = {}

        obj_section = soup.find(
            "span", class_="cardMainInfo__title", string="Объект закупки"
        )
        if obj_section:
            content = obj_section.find_next("span", class_="cardMainInfo__content")
            if content:
                result["purchase_name"] = content.get_text(strip=True)

        title_div = soup.find("div", class_="cardMainInfo__title")
        if title_div:
            full_title = title_div.get_text(strip=True)
            result["purchase_method"] = full_title.replace("44-ФЗ", "").strip()

        org_section = soup.find(
            "span",
            class_="cardMainInfo__title",
            string=re.compile("Организация|Заказчик"),
        )
        if org_section:
            org_link = org_section.find_next("a")
            if org_link:
                result["customer_name"] = org_link.get_text(strip=True)

        price_elem = soup.find("span", class_="cardMainInfo__content cost")
        if price_elem:
            result["nmck"] = (
                self.price_parser.parse(price_elem.get_text(strip=True)) or 0.0
            )

        for section in soup.find_all("div", class_="cardMainInfo__section"):
            title = section.find("span", class_="cardMainInfo__title")
            value = section.find("span", class_="cardMainInfo__content")
            if not title or not value:
                continue

            title_text = title.get_text(strip=True)
            value_text = value.get_text(strip=True)

            if "Размещено" in title_text:
                result["publish_date"] = value_text
            elif "Окончание подачи заявок" in title_text:
                result["deadline_date"] = value_text
            elif "Обновлено" in title_text:
                result["current_revision_date"] = value_text

        region = self._extract_section_value_v5(soup, "Регион")
        if region:
            result["customer_region"] = region
        else:
            tz_elem = soup.find("div", class_="time-zone__value")
            if tz_elem:
                tz_text = tz_elem.get_text(strip=True)
                result["customer_region"] = tz_text.split()[0] if tz_text else ""

        result["platform_name"] = self._extract_section_value_v5(
            soup, "Наименование электронной площадки"
        )
        result["platform_url"] = self._extract_section_url_v5(
            soup, "Адрес электронной площадки"
        )

        result["requirements"] = self._extract_section_value_v5(
            soup, "Требования к участникам"
        )
        if not result.get("requirements"):
            result["requirements"] = self._extract_section_value_v5(
                soup, "Требования к участникам закупки"
            )

        result["application_guarantee"] = self._extract_section_value_v5(
            soup, "Обеспечение заявок"
        )
        result["contract_guarantee"] = self._extract_section_value_v5(
            soup, "Обеспечение исполнения контракта"
        )
        result["guarantee_method"] = self._extract_section_value_v5(
            soup, "Способ обеспечения"
        )

        # Место поставки → cities_count
        addresses_text = self._extract_section_value_v5(soup, "Место поставки")
        if addresses_text:
            cities = self._count_addresses(addresses_text, "")
            result["cities_count"] = cities
            result["addresses_count"] = cities
            result["customer_address"] = addresses_text

        if not result.get("contact_person"):
            result["contact_person"] = self._extract_section_value_v5(
                soup, "Контактное лицо"
            )
        if not result.get("contact_email"):
            result["contact_email"] = self._extract_section_value_v5(
                soup, "Адрес электронной почты"
            )
        if not result.get("contact_phone"):
            result["contact_phone"] = self._extract_section_value_v5(
                soup, "Контактный телефон"
            )

        if not result.get("customer_inn"):
            result["customer_inn"] = self._extract_section_value_v5(soup, "ИНН")

        return result

    def _parse_documents_44(self, soup: BeautifulSoup) -> List[TenderDocument]:
        """Парсит документы для 44-ФЗ."""
        documents = []

        for section in soup.find_all(
            "div", class_=re.compile(r"notice-documents|protocols|changes")
        ):
            is_active = True
            status_elem = section.find(
                "div",
                class_="section__value",
                string=re.compile(r"Действующая|Недействующая"),
            )
            if status_elem:
                is_active = "Действующая" in status_elem.get_text(strip=True)

            date = ""
            date_elem = section.find(
                "div", class_="section__attrib", string="Размещено"
            )
            if date_elem:
                date_val = date_elem.find_next("div", class_="section__value")
                if date_val:
                    date = date_val.get_text(strip=True)

            for attachment in section.find_all("div", class_="attachment"):
                doc = self._parse_attachment_44(attachment, is_active, date)
                if doc:
                    documents.append(doc)

        return documents

    def _parse_attachment_44(
        self, attachment, is_active: bool = True, doc_date: str = ""
    ) -> Optional[TenderDocument]:
        """Парсит одно вложение 44-ФЗ."""
        try:
            name_span = attachment.find("span", class_="section__value")
            if not name_span:
                return None

            a_tag = name_span.find("a")
            if a_tag:
                name = a_tag.get("title", "") or a_tag.get_text(strip=True)
                file_url = a_tag.get("href", "")
            else:
                name = name_span.get_text(strip=True)
                file_url = ""

            if file_url:
                file_url = urljoin(self.BASE_URL, file_url)

            file_type = ""
            img = attachment.find("img", src=re.compile(r"/type/"))
            if img:
                src = img.get("src", "")
                file_type = self._detect_file_type(src)

            return TenderDocument(
                name=name,
                url=file_url,
                file_type=file_type,
                date=doc_date,
                is_active=is_active,
                file_url=file_url,
            )
        except Exception as e:
            logger.debug(f"Ошибка парсинга документа 44-ФЗ: {e}")
            return None

    # ============ HELPER METHODS ============

    def _extract_section_value_v5(self, soup: BeautifulSoup, title_text: str) -> str:
        for section in soup.find_all("section", class_="blockInfo__section"):
            title_elem = section.find("span", class_="section__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                value_elem = section.find("span", class_="section__info")
                if value_elem:
                    return value_elem.get_text(strip=True)

        for section in soup.find_all("div", class_="blockInfo__section"):
            title_elem = section.find("span", class_="section__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                value_elem = section.find("span", class_="section__info")
                if value_elem:
                    return value_elem.get_text(strip=True)

        for row in soup.find_all("div", class_="row"):
            title_elem = row.find("div", class_="section__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                value_elem = row.find("div", class_="section__value")
                if value_elem:
                    return value_elem.get_text(strip=True)

        return ""

    def _extract_section_url_v5(self, soup: BeautifulSoup, title_text: str) -> str:
        for section in soup.find_all("section", class_="blockInfo__section"):
            title_elem = section.find("span", class_="section__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                link = section.find("a", href=True)
                if link:
                    href = link.get("href", "")
                    return urljoin(self.BASE_URL, href) if href else ""

        for section in soup.find_all("div", class_="blockInfo__section"):
            title_elem = section.find("span", class_="section__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                link = section.find("a", href=True)
                if link:
                    href = link.get("href", "")
                    return urljoin(self.BASE_URL, href) if href else ""

        for row in soup.find_all("div", class_="row"):
            title_elem = row.find("div", class_="section__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                link = row.find("a", href=True)
                if link:
                    href = link.get("href", "")
                    return urljoin(self.BASE_URL, href) if href else ""
        return ""

    def _detect_file_type(self, src: str) -> str:
        src_lower = src.lower()
        if "docx" in src_lower:
            return "docx"
        elif "doc" in src_lower:
            return "doc"
        elif "xlsx" in src_lower:
            return "xlsx"
        elif "xls" in src_lower:
            return "xls"
        elif "pdf" in src_lower:
            return "pdf"
        elif "zip" in src_lower:
            return "zip"
        elif "rar" in src_lower:
            return "rar"
        return ""

    def _count_addresses(self, text: str, tender_type: str = "") -> int:
        """Единый метод подсчёта городов/адресов."""
        if not text:
            return 1

        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        raw_parts = re.split(r"[\n,;]", text)
        addresses = [a.strip() for a in raw_parts if a.strip() and len(a.strip()) > 5]

        cities = set()
        city_patterns = [
            r"г\.?\s*([А-Яа-я\-]+)",
            r"город\s+([А-Яа-я\-]+)",
            r"([А-Яа-я\-]+)\s+обл",
            r"([А-Яа-я\-]+)\s+край",
            r"респ\.?\s*([А-Яа-я\-]+)",
            r"п(?:г)?т\.?\s*([А-Яа-я\-]+)",
            r"пос(?:ёлок)?\.?\s*([А-Яа-я\-]+)",
            r"с\.?\s*([А-Яа-я\-]+)",
            r"^([А-Я][а-я\-]{2,})\s*,",
            r"^([А-Я][а-я\-]{2,})\s+ул\.",
            r"^([А-Я][а-я\-]{2,})\s+пр\.",
            r"^([А-Я][а-я\-]{2,})\s+пер\.",
            r"^([А-Я][а-я\-]{2,})\s+просп\.",
            r"^([А-Я][а-я\-]{2,})\s+б\-р",
            r"^([А-Я][а-я\-]{2,})\s+пл\.",
            r"^([А-Я][а-я\-]{2,})\s+ш\.",
            r"^([А-Я][а-я\-]{2,})\s+туп\.",
            r"^([А-Я][а-я\-]{2,})\s+наб\.",
        ]

        for addr in addresses:
            for pattern in city_patterns:
                match = re.search(pattern, addr, re.IGNORECASE)
                if match:
                    cities.add(match.group(1).lower())
                    break

        # Для обучения: 1 площадка = 1 адрес
        if tender_type == "education":
            if len(cities) > 0:
                logger.info(f"[v6.3] Обучение: {len(cities)} город(ов) → 1 площадка")
                return 1
            return 1

        # Для СОУТ и остальных: уникальные города
        if len(cities) > 0:
            logger.info(f"[v6.3] СОУТ/др.: {len(cities)} уникальных город(ов)")
            return len(cities)

        logger.info(f"[v6.3] Города не найдены → fallback 1")
        return 1

    def _extract_text_by_title(self, soup: BeautifulSoup, title_text: str) -> str:
        for block in soup.find_all("div", class_="col-9 mr-auto"):
            title_elem = block.find("div", class_="common-text__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                value_elem = block.find("div", class_="common-text__value")
                if value_elem:
                    return value_elem.get_text(strip=True)
        return ""

    def _extract_url_by_title(self, soup: BeautifulSoup, title_text: str) -> str:
        for block in soup.find_all("div", class_="col-9 mr-auto"):
            title_elem = block.find("div", class_="common-text__title")
            if (
                title_elem
                and title_text.lower() in title_elem.get_text(strip=True).lower()
            ):
                link = block.find("a", href=True)
                if link:
                    href = link.get("href", "")
                    return urljoin(self.BASE_URL, href) if href else ""
        return ""

    def _extract_inn_from_soup(self, soup: BeautifulSoup) -> str:
        inn_elem = soup.find("div", class_="common-text__value--gray", string="ИНН")
        if inn_elem:
            inn_val = inn_elem.find_next("div", class_="common-text__value")
            if inn_val:
                return inn_val.get_text(strip=True)
        return ""

    def _extract_email_from_soup(self, soup: BeautifulSoup) -> str:
        mailto = soup.find("a", href=re.compile(r"mailto:"))
        if mailto:
            href = mailto.get("href", "")
            return href.replace("mailto:", "")
        return ""

    def _extract_region_from_address(self, address: str) -> str:
        if not address:
            return ""
        address = re.sub(r"^\d{6},?\s*", "", address)
        regions = [
            "Москва",
            "Санкт-Петербург",
            "Севастополь",
            "Московская",
            "Ленинградская",
            "Нижегородская",
            "Свердловская",
            "Ростовская",
            "Челябинская",
            "Самарская",
            "Башкортостан",
            "Татарстан",
            "Краснодарский",
            "Красноярский",
            "Пермский",
            "Алтайский",
            "Ставропольский",
            "Хабаровский",
            "Приморский",
            "Кемеровская",
            "Новосибирская",
            "Омская",
            "Томская",
            "Иркутская",
            "Амурская",
            "Сахалинская",
            "Камчатский",
            "Магаданская",
            "Чукотский",
            "Ямало-Ненецкий",
            "Ханты-Мансийский",
            "Тюменская",
            "Курганская",
            "Оренбургская",
            "Саратовская",
            "Волгоградская",
            "Астраханская",
            "Калмыкия",
            "Дагестан",
            "Ингушетия",
            "Кабардино-Балкария",
            "Карачаево-Черкесия",
            "Северная Осетия",
            "Чечня",
            "Адыгея",
            "Крым",
            "Удмуртия",
            "Мордовия",
            "Чувашия",
            "Марий Эл",
            "Тыва",
            "Бурятия",
            "Саха",
            "Якутия",
            "Забайкальский",
            "Еврейская",
        ]
        address_lower = address.lower()
        for region in regions:
            if region.lower() in address_lower:
                return region
        parts = address.split(",")
        if parts:
            first = parts[0].strip()
            if first:
                return first
        return ""

    def _save_to_cache(self, detail: TenderDetail):
        if not self.cache:
            return
        try:
            from core.tender_cache import PurchaseState

            state = PurchaseState(
                reg_number=detail.reg_number,
                last_update_date=detail.current_revision_date
                or detail.publish_date
                or "",
                status="parsed",
                checked_at=datetime.now().isoformat(),
            )
            self.cache.set_purchase_state(state)
        except Exception as e:
            logger.debug(f"Ошибка сохранения в кэш: {e}")

    def analyze_with_llm(
        self, detail: TenderDetail, llm_client=None
    ) -> Optional[Dict[str, Any]]:
        """LLM-анализ через analyzer (не через inline промпт)."""
        if not llm_client:
            logger.warning("LLM-клиент не передан")
            return None

        if not detail.documents_text or len(detail.documents_text) < 100:
            logger.warning("Недостаточно текста документов для LLM-анализа")
            return None

        # Делегируем analyzer — здесь только подготовка данных
        # Реальный LLM-вызов делается в analyzer.analyze()
        extracted_info = {
            "purchase_name": detail.purchase_name,
            "customer_name": detail.customer_name,
            "customer_region": detail.customer_region,
            "nmck": detail.nmck,
            "purchase_method": detail.purchase_method,
            "platform_name": detail.platform_name,
            "requirements": detail.requirements,
            "documents_text": detail.documents_text,
            "rm_total": detail.rm_total,
            "rm_category_1": detail.rm_category_1,
            "rm_category_2": detail.rm_category_2,
            "rm_with_iii": detail.rm_with_iii,
            "points_count": detail.points_count,
            "students_count": detail.students_count,
            "cities_count": detail.cities_count,
            "addresses_count": detail.addresses_count,
            "trip_days": detail.trip_days,
            "teacher_days": detail.teacher_days,
            "accommodation_nights": detail.accommodation_nights,
            "transport_km": detail.transport_km,
            "venue_rent_days": detail.venue_rent_days,
            "manikin_days": detail.manikin_days,
            "opr_positions": detail.opr_positions,
            "opr_persons": detail.opr_persons,
            "is_seasonal": detail.is_seasonal,
            "application_guarantee": detail.application_guarantee,
            "contract_guarantee": detail.contract_guarantee,
        }

        return extracted_info
