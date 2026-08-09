"""
core/parsers/detailed_parser.py
Детальный парсинг карточки тендера (оркестрация).
РЕФАКТОРИНГ (v6.6-r1):
  - HTML-парсеры вынесены в html_parsers.py
  - Парсинг адресов вынесен в address_parser.py
  - Исправлен баг: КТРУ теперь проверяет РМ и слушателей независимо
  - Параметры применяются через маппинг (не 40 строк ручного кода)
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime

from bs4 import BeautifulSoup
from loguru import logger

from core.http_session import get_session_manager
from core.document_processor import DocumentProcessor
from core.tender_cache import TenderCache
from core.tender_type import get_type_detector
from core.parsers.html_parsers import Html44Parser, Html223Parser
from core.parsers.address_parser import AddressParser
from utils.url_builder import get_url_builder


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
    delivery_address: str = ""
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
    regions_count: int = 1
    trips: int = 1
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

    # v6.6-r1: Метаданные источников данных
    _param_sources: Dict[str, str] = field(default_factory=dict, repr=False)

    def set_param_source(self, param: str, source: str):
        """Запоминает источник параметра (ktru, xls, docx, regex, llm)."""
        self._param_sources[param] = source

    def get_param_source(self, param: str) -> str:
        return self._param_sources.get(param, "unknown")

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
            "delivery_address": self.delivery_address,
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
            "regions_count": self.regions_count,
            "trips": self.trips,
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
    """Детальный парсер карточки тендера. Поддерживает 44-ФЗ, 223-ФЗ."""

    BASE_URL = "https://zakupki.gov.ru"
    REQUEST_DELAY = (1, 3)

    # v6.6-r1: Маппинг полей TenderDetail -> полей извлеченных параметров
    PARAM_FIELDS = [
        "rm_total", "rm_category_1", "rm_category_2", "rm_with_iii",
        "points_count", "students_count", "factors_count", "addresses_count",
        "trip_days", "teacher_days", "accommodation_nights", "transport_km",
        "venue_rent_days", "manikin_days", "opr_positions", "opr_persons",
    ]

    BOOL_FIELDS = [
        "has_full_time", "has_polygon", "is_urgent", "is_seasonal",
        "needs_siz_norms", "needs_dsiz_norms", "needs_iot_norms",
    ]

    def __init__(self, cache: Optional[TenderCache] = None):
        self.cache = cache
        self.url_builder = get_url_builder()
        self.type_detector = get_type_detector()
        self.session_manager = get_session_manager(pool_size=1)
        self.session = self.session_manager.get_primary_session()
        self.doc_processor = DocumentProcessor(session=self.session)

        # v6.6-r1: Специализированные парсеры
        self.html_44 = Html44Parser()
        self.html_223 = Html223Parser()
        self.address_parser = AddressParser()

        logger.info("DetailedParser инициализирован (v6.6-r1)")

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

        # Fallback noticeGuid для 223-ФЗ
        if law_type == "223" and not notice_guid:
            notice_guid = self._extract_notice_guid_from_html(common_info["html"])

        # Парсим common-info через специализированный парсер
        if law_type == "44":
            parsed_info = self.html_44.parse_common_info(soup)
        else:
            parsed_info = self.html_223.parse_common_info(soup)

        for key, value in parsed_info.items():
            if hasattr(detail, key) and value is not None:
                setattr(detail, key, value)

        if not detail.nmck and parsed_info.get("nmck"):
            detail.nmck = parsed_info["nmck"]

        # v6.6-r1: Парсим КТРУ из HTML 44-ФЗ (НЕЗАВИСИМО для РМ и слушателей)
        if law_type == "44":
            ktru_data = self.html_44.parse_ktru_positions(soup)

            # РМ и слушатели — независимые проверки (был баг с elif)
            if ktru_data.get("rm_total"):
                detail.rm_total = ktru_data["rm_total"]
                detail.set_param_source("rm_total", "ktru")
                if not detail.tender_type:
                    detail.tender_type = "sout"
                logger.info(f"   [KTRU] rm_total={detail.rm_total} (confidence=1.0)")

            if ktru_data.get("students_count"):
                detail.students_count = ktru_data["students_count"]
                detail.set_param_source("students_count", "ktru")
                if not detail.tender_type:
                    detail.tender_type = "education"
                logger.info(f"   [KTRU] students_count={detail.students_count} (confidence=1.0)")

        self._log_common_info(detail)

        # === Шаг 2: Документы ===
        docs_info = self._fetch_documents(reg_number, law_type, notice_guid)
        if docs_info:
            detail.documents_url = docs_info["url"]
            docs_soup = BeautifulSoup(docs_info["html"], "html.parser")
            if law_type == "44":
                detail.documents = self.html_44.parse_documents(docs_soup)
            else:
                detail.documents = self.html_223.parse_documents(docs_soup)

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

        # === Шаг 4: Пересчет адресов ===
        delivery_for_count = detail.delivery_address or detail.customer_address
        if delivery_for_count:
            address_result = self.address_parser.count_addresses(
                delivery_for_count, detail.tender_type
            )
            detail.cities_count = address_result.get("cities_count", 0)
            detail.regions_count = max(1, address_result.get("regions_count", 1))
            detail.trips = max(1, address_result.get("trips", 1))

            # Считаем адреса
            delivery_clean = (
                delivery_for_count.replace("<br>", "\n")
                .replace("<br/>", "\n")
                .replace("<br />", "\n")
            )
            if "\n" in delivery_clean:
                lines = [
                    l.strip()
                    for l in delivery_clean.split("\n")
                    if l.strip() and len(l.strip()) > 5
                ]
                detail.addresses_count = len(lines)
            else:
                city_markers = re.findall(
                    r"г\.\s*\w+|город\s+\w+", delivery_clean, re.IGNORECASE
                )
                detail.addresses_count = max(1, len(city_markers))

            if address_result.get("needs_manual_check"):
                logger.warning(
                    f"   [AddressParser] ⚠️ Много адресов/регионов — требуется ручная проверка"
                )

        # === Шаг 5: Кэширование ===
        if self.cache:
            self._save_to_cache(detail)

        return detail

    def _extract_params_from_text(self, detail: TenderDetail, text: str):
        """v6.6-r1: Использует TenderParamExtractor + маппинг полей."""
        if not text or len(text) < 50:
            return

        from core.param_extractor import TenderParamExtractor

        extractor = TenderParamExtractor()
        params = extractor.extract(text, nmck=detail.nmck, tender_type_hint=detail.tender_type)

        # Сохраняем КТРУ-данные перед применением regex/LLM
        ktru_values = {
            "rm_total": detail.rm_total,
            "students_count": detail.students_count,
        }

        # v6.6-r1: Применяем числовые параметры через маппинг
        for field in self.PARAM_FIELDS:
            value = getattr(params, field, None)
            if value is not None:
                current = getattr(detail, field, 0)
                # Не перезаписываем КТРУ-данные (confidence=1.0)
                if field in ktru_values and ktru_values[field] > 0:
                    if current != ktru_values[field]:
                        logger.info(
                            f"   [Priority] КТРУ {field}={ktru_values[field]} "
                            f"(вместо {value} из текста)"
                        )
                    continue
                setattr(detail, field, value)
                detail.set_param_source(field, "regex")

        # v6.6-r1: Применяем булевы флаги
        for field in self.BOOL_FIELDS:
            value = getattr(params, field, None)
            if value is not None:
                setattr(detail, field, value)

        # Тип тендера (если не определен через КТРУ)
        if not detail.tender_type and params.region_hint:
            type_result = self.type_detector.detect(text)
            detail.tender_type = type_result.tender_type

        logger.info(
            f"   📊 Извлечено: РМ={detail.rm_total}, кат.1={detail.rm_category_1}, "
            f"кат.2={detail.rm_category_2}, ИИИ={detail.rm_with_iii}, "
            f"точек={detail.points_count}, слушателей={detail.students_count}, "
            f"тип={detail.tender_type}, городов={detail.cities_count}, "
            f"адресов={detail.addresses_count}, дней_выезда={detail.trip_days}, "
            f"сезон={detail.is_seasonal}, opr_pos={detail.opr_positions}, opr_per={detail.opr_persons}"
        )

    def _log_common_info(self, detail: TenderDetail):
        """Логирует результаты парсинга common-info."""
        logger.info(
            f"   ✅ Common-info: {detail.purchase_name[:60] if detail.purchase_name else 'N/A'}..."
        )
        logger.info(f"   📍 Регион: {detail.customer_region or 'не определен'}")
        logger.info(f"   🏢 ЭТП: {detail.platform_name or 'не определена'}")
        logger.info(f"   📋 Требования: {'есть' if detail.requirements else 'нет'}")
        logger.info(f"   🔒 Обеспечение заявки (raw): '{detail.application_guarantee or 'пусто'}'")
        logger.info(f"   🔒 Обеспечение контракта (raw): '{detail.contract_guarantee or 'пусто'}'")
        logger.info(f"   🔒 Способ обеспечения (raw): '{detail.guarantee_method or 'пусто'}'")

    # ============ FETCH METHODS ============

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
                    logger.warning(f"   ⏳ 429, ждем...")
                    time.sleep(5 * (attempt + 1))
                else:
                    logger.warning(f"   ⚠️ Статус {response.status_code}")
            except Exception as e:
                logger.error(f"   ❌ Ошибка загрузки: {e}")
                time.sleep(2)

        return None

    def _extract_notice_guid_from_html(self, html: str) -> str:
        """Извлекает noticeGuid из HTML страницы 223-ФЗ."""
        if not html:
            return ""
        patterns = [
            r"noticeGuid=([a-f0-9\-]+)",
            r'"noticeGuid"\s*:\s*"([a-f0-9\-]+)"',
            r"noticeGuid\s*=\s*'([a-f0-9\-]+)'",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                guid = match.group(1)
                logger.info(f"   [noticeGuid] Извлечен из HTML: {guid}")
                return guid
        return ""

    def _save_to_cache(self, detail: TenderDetail):
        if not self.cache:
            return
        try:
            from core.tender_cache import PurchaseState

            state = PurchaseState(
                reg_number=detail.reg_number,
                last_update_date=detail.current_revision_date or detail.publish_date or "",
                status="parsed",
                checked_at=datetime.now().isoformat(),
            )
            self.cache.set_purchase_state(state)
        except Exception as e:
            logger.debug(f"Ошибка сохранения в кэш: {e}")
