"""
core/searcher.py
Поиск тендеров на zakupki.gov.ru (ЕИС).
ИСПРАВЛЕНО (21.07.2026 v2):
  - Добавлен 615-ФЗ (совместные закупки)
  - Улучшена защита от ложных срабатываний "информационная безопасность"
  - Добавлена проверка контекста для составных фраз
  - Синхронизированы exclude_keywords с risk_rules.yaml
"""

import sys
import random
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any, Generator
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from pathlib import Path
import re
import time
import json

from loguru import logger

try:
    from curl_cffi import requests as curl_requests

    HAS_CURL_CFFI = True
except ImportError:
    import requests as curl_requests

    HAS_CURL_CFFI = False
    logger.warning("⚠️ curl_cffi не найдена, используем обычный requests")

from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def get_platform_from_ua(user_agent: str) -> str:
    if "Windows" in user_agent:
        return "Windows"
    elif "Macintosh" in user_agent:
        return "macOS"
    elif "Linux" in user_agent:
        return "Linux"
    return "Windows"


@dataclass
class TenderSearchResult:
    tender_id: str
    title: str
    url: str
    nmck: Optional[float] = None
    region: Optional[str] = None
    publish_date: Optional[str] = None
    deadline_date: Optional[str] = None
    etp: str = "zakupki.gov.ru"
    law: str = "44-FZ"
    okpd2: List[str] = field(default_factory=list)
    customer: Optional[str] = None
    status: Optional[str] = None
    notice_guid: Optional[str] = None
    raw_html: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "tender_id": self.tender_id,
            "title": self.title,
            "url": self.url,
            "nmck": self.nmck,
            "region": self.region,
            "publish_date": self.publish_date,
            "deadline_date": self.deadline_date,
            "etp": self.etp,
            "law": self.law,
            "okpd2": self.okpd2,
            "customer": self.customer,
            "status": self.status,
            "notice_guid": self.notice_guid,
        }


SEARCH_CONFIG = {
    "okpd2_ids": [
        "8874806",
        "8879198",
        "8879202",
    ],
    "okpd2_codes": [
        "85.42",
        "71.20.11",
        "71.20.19",
    ],
    "relevance_keywords": [
        "охрана труда",
        "охране труда",
        "охраны труда",
        "СОУТ",
        "специальная оценка условий труда",
        "специальной оценки условий труда",
        "специальной оценке условий труда",
        "оценка профессиональных рисков",
        "оценке профессиональных рисков",
        "оценки профессиональных рисков",
        "производственный лабораторный контроль",
        "производственного лабораторного контроля",
        "замеры вредных факторов",
        "замеров вредных факторов",
        "вредные производственные факторы",
        "вредных производственных факторов",
        "обучение охране труда",
        "обучению охране труда",
        "обучения охране труда",
        "оценка проф. рисков",
        "пожарная безопасность",
        "пожарной безопасности",
        "промышленная безопасность",
        "промышленной безопасности",
        "обучение рабочих профессий",
        "технологические карты",
        "санитарно-защитная зона",
        "тренинги",
        "инструктажи",
        "аттестация рабочих мест",
        "аттестации рабочих мест",
    ],
    # === ИСПРАВЛЕНО: Запрещённые слова (синхронизировано с risk_rules.yaml) ===
    "exclude_keywords": [
        # Точные фразы
        "лицензия МЧС",
        "медицинские работники",
        "водительские права",
        "гражданская оборона",
        "категорированные организации",
        "охранники с оружием",
        "лицензия ФСБ",
        "гостайна",
        "исследования воды",
        "смывы",
        "гельминты",
        "биология",
        "микробиолог",
        # Экспертиза промбезопасности — ВСЕ склонения
        "экспертиза промышленной безопасности",
        "экспертизы промышленной безопасности",
        "экспертизе промышленной безопасности",
        "экспертизу промышленной безопасности",
        "экспертизой промышленной безопасности",
        "экспертиз промышленной безопасности",
        # Дополнительные варианты
        "экспертиза безопасности",
        "экспертизы безопасности",
        "техническая диагностика",
        "промбезопасность",
        "промбезопасности",
    ],
    # === ИСПРАВЛЕНО: Контекстные исключения ===
    # Если найдено одно из этих слов — НЕ отклонять (ложное срабатывание)
    "exclude_context_exceptions": [
        "СОУТ",
        "сои",
        "специальная оценка",
        "оценка профессиональных рисков",
        "ОПР",
        "охрана труда",
        "обучение охране труда",
    ],
    # === НОВОЕ: Составные запрещённые фразы с контекстной проверкой ===
    "exclude_composite": [
        {
            "words": ["экспертиз", "безопасност"],
            "max_distance": 5,
            "check_context": True,
        },
        {
            "words": ["диагностик", "оборудован"],
            "max_distance": 5,
            "check_context": True,
        },
        {
            "words": ["техническ", "диагностик"],
            "max_distance": 5,
            "check_context": True,
        },
    ],
    "min_nmck": 100000,
    "max_nmck_siz": 300000,
    "laws": ["44-FZ", "223-FZ", "615-FZ"],
    "publish_date_days": 14,
}


class TenderSearcher:
    BASE_SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html"
    MAX_SEARCH_WORKERS = 5
    BATCH_SIZE = 50
    REQUEST_DELAY = (0.5, 1.5)

    def __init__(
        self,
        config: Optional[Dict] = None,
        proxy: Optional[str] = None,
        parsing_only: bool = True,
    ):
        self.config = config or SEARCH_CONFIG
        self.proxy = proxy
        self.parsing_only = parsing_only

        self._sessions: List[Any] = []
        self._lock = Lock()
        self._consecutive_429 = 0
        self._base_delay = 5

        self._init_session_pool()
        self.session = self._sessions[0] if self._sessions else self._create_session()

        logger.info(
            f"🔍 TenderSearcher initialized "
            f"(parsing_only={parsing_only}, "
            f"{len(self._sessions)} sessions)"
        )

    def _init_session_pool(self, pool_size: int = 3):
        for i in range(pool_size):
            session = self._create_session()
            self._sessions.append(session)
        logger.info(f"🔌 Пул сессий: {pool_size} шт.")

    def _get_session(self, index: int = 0) -> Any:
        if not self._sessions:
            return self._create_session()
        return self._sessions[index % len(self._sessions)]

    def _create_session(self) -> Any:
        user_agent = get_random_user_agent()
        platform = get_platform_from_ua(user_agent)

        try:
            if HAS_CURL_CFFI:
                session = curl_requests.Session(impersonate="chrome124")
            else:
                session = curl_requests.Session()

            if self.proxy:
                session.proxies = {"http": self.proxy, "https": self.proxy}

        except Exception as e:
            logger.error(f"Ошибка создания сессии: {e}")
            session = curl_requests.Session()

        self._update_session_headers(session, user_agent, platform)
        session.verify = False
        return session

    def _update_session_headers(self, session, user_agent: str, platform: str):
        sec_ch_ua = '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'

        session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Referer": "https://zakupki.gov.ru/",
                "sec-ch-ua": sec_ch_ua,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": f'"{platform}"',
            }
        )

    def _rotate_user_agent(self):
        user_agent = get_random_user_agent()
        platform = get_platform_from_ua(user_agent)
        self._update_session_headers(self.session, user_agent, platform)
        logger.debug(f"🎭 User-Agent изменён")

    def _calculate_delay(self) -> float:
        if self._consecutive_429 > 0:
            delay = self._base_delay * (2 ** (self._consecutive_429 - 1))
            delay = min(delay, 300)
            logger.warning(f"⏳ Backoff: {delay}с (429 x{self._consecutive_429})")
            return delay
        return random.uniform(*self.REQUEST_DELAY)

    def _handle_429(self):
        self._consecutive_429 += 1
        self._rotate_user_agent()
        delay = self._calculate_delay()
        logger.warning(f"🚫 429! Ждём {delay}с...")
        time.sleep(delay)

    def _reset_429_counter(self):
        if self._consecutive_429 > 0:
            logger.info(f"✅ Сброс 429 (было: {self._consecutive_429})")
            self._consecutive_429 = 0

    def _build_search_url(
        self,
        page: int = 1,
        date_from: Optional[datetime.date] = None,
        date_to: Optional[datetime.date] = None,
    ) -> str:
        today = datetime.now().date()

        if date_from is None:
            date_from = today - timedelta(days=self.config["publish_date_days"])
        if date_to is None:
            date_to = today

        params = {
            "morphology": "on",
            "search-filter": "Дате+размещения",
            "sortBy": "UPDATE_DATE",
            "sortDirection": "false",
            "publishDateFrom": date_from.strftime("%d.%m.%Y"),
            "publishDateTo": date_to.strftime("%d.%m.%Y"),
            "currencyIdGeneral": "-1",
            "showLotsInfoHidden": "false",
            "pageNumber": str(page),
            "recordsPerPage": "_50",
            "fz44": "on",
            "fz223": "on",
            # === НОВОЕ: 615-ФЗ ===
            "fz615": "on",
            "priceFromGeneral": str(self.config.get("min_nmck", 100000)),
        }

        if self.config.get("okpd2_ids"):
            params["okpd2Ids"] = ",".join(self.config["okpd2_ids"])
            params["okpd2IdsWithNested"] = "on"
            if self.config.get("okpd2_codes"):
                params["okpd2IdsCodes"] = ",".join(self.config["okpd2_codes"])

        url = f"{self.BASE_SEARCH_URL}?{urlencode(params, safe='{}')}"
        return url

    def _make_request(
        self,
        url: str,
        session: Optional[Any] = None,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> Optional[Any]:
        if session is None:
            session = self.session

        for attempt in range(max_retries):
            delay = self._calculate_delay()
            if attempt > 0:
                logger.info(f"  ⏳ Попытка {attempt + 1}, задержка {delay:.1f}с...")
            time.sleep(delay)

            try:
                response = session.get(url, timeout=timeout)

                if response.status_code == 429:
                    self._handle_429()
                    continue

                if response.status_code == 200:
                    self._reset_429_counter()
                    return response

                logger.warning(f"  ⚠️ Статус {response.status_code}")

            except Exception as e:
                logger.error(f"  ❌ Ошибка запроса: {e}")
                time.sleep(5)

        return None

    def search(
        self,
        max_pages: Optional[int] = None,
        max_results: Optional[int] = None,
    ) -> Generator[TenderSearchResult, None, None]:
        total_found = 0
        seen_ids = set()

        logger.info(f"\n{'='*60}")
        logger.info(f"📋 Поиск тендеров")
        logger.info(
            f"📋 Фильтр НМЦК: ≥{self.config.get('min_nmck', 100000):,}₽ (на стороне ЕИС)"
        )
        logger.info(f"📋 Период: {self.config.get('publish_date_days', 14)} дней")
        logger.info(f"📋 Законы: {self.config.get('laws', [])}")
        logger.info(f"📋 ОКПД2: {self.config.get('okpd2_codes', [])}")
        logger.info(f"{'='*60}")

        for result in self._search_single(max_pages):
            if result.tender_id in seen_ids:
                continue
            seen_ids.add(result.tender_id)

            if not self._is_relevant(result.title):
                logger.debug(
                    f"  ⏭ {result.tender_id}: не релевантно — '{result.title[:60]}...'"
                )
                continue

            if self._has_excluded_keywords(result.title):
                logger.info(f"  🚫 {result.tender_id}: запрещённые слова в названии")
                continue

            total_found += 1
            yield result

            if max_results and total_found >= max_results:
                logger.info(f"✅ Достигнут лимит: {max_results} тендеров")
                return

        logger.info(f"\n✅ ПОИСК ЗАВЕРШЁН: {total_found} тендеров")

    def _search_single(
        self,
        max_pages: Optional[int] = None,
    ) -> Generator[TenderSearchResult, None, None]:
        url = self._build_search_url(page=1)
        response = self._make_request(url)

        if not response:
            logger.error("❌ Не удалось загрузить первую страницу")
            return

        if "captcha" in response.text.lower():
            logger.error("🤖 CAPTCHA!")
            return

        total_count = self._extract_total_count(response.text)
        if total_count == 0:
            logger.info("  ⏭ Нет результатов")
            return

        total_pages = min((total_count // 50) + 1, max_pages or 100)
        logger.info(f"📊 ~{total_count} записей, страниц: {total_pages}")

        for result in self._parse_search_results(response.text):
            yield result

        if total_pages <= 1:
            return

        remaining_pages = list(range(2, total_pages + 1))

        with ThreadPoolExecutor(max_workers=self.MAX_SEARCH_WORKERS) as executor:
            future_to_page = {
                executor.submit(self._fetch_page, page): page
                for page in remaining_pages
            }

            completed = 0
            for future in as_completed(future_to_page):
                page = future_to_page[future]
                try:
                    results = future.result(timeout=30)
                    completed += 1

                    if results:
                        for result in results:
                            yield result

                    if completed % 5 == 0 or completed == len(remaining_pages):
                        logger.info(
                            f"  📄 Прогресс: {completed}/{len(remaining_pages)}"
                        )

                except concurrent.futures.TimeoutError:
                    logger.warning(f"  ⏰ Таймаут страницы {page}")
                except Exception as e:
                    logger.error(f"  ❌ Ошибка страницы {page}: {e}")

    def _fetch_page(self, page: int) -> Optional[List[TenderSearchResult]]:
        session = self._get_session(page)
        url = self._build_search_url(page=page)

        time.sleep(random.uniform(0.1, 0.3))

        try:
            response = session.get(url, timeout=30)

            if response.status_code == 429:
                time.sleep(2)
                response = session.get(url, timeout=30)

            if response.status_code == 200:
                if "captcha" in response.text.lower():
                    return None
                return list(self._parse_search_results(response.text))

            logger.warning(f"  ⚠️ Страница {page}: статус {response.status_code}")
            return None

        except Exception as e:
            logger.error(f"  ❌ Страница {page}: {e}")
            return None

    def _extract_total_count(self, html: str) -> int:
        soup = BeautifulSoup(html, "html.parser")
        patterns = [
            r"более\s*([\d\s]+)\s*записей",
            r"([\d\s]+)\s*записей",
            r"найдено\s*[:—]?\s*([\d\s]+)",
            r"найдено\s*([\d\s]+)\s*запис",
            r"всего\s*([\d\s]+)",
            r"результатов:\s*([\d\s]+)",
        ]
        text = soup.get_text()
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                count_str = (
                    match.group(1)
                    .replace(" ", "")
                    .replace("\xa0", "")
                    .replace("\u202f", "")
                )
                try:
                    return int(count_str)
                except ValueError:
                    continue
        return 0

    def _parse_search_results(
        self,
        html: str,
    ) -> Generator[TenderSearchResult, None, None]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="registry-entry__form")

        logger.info(f"   📄 Найдено карточек: {len(cards)}")

        for card in cards:
            try:
                result = self._parse_card(card)
                if result:
                    yield result
            except Exception as e:
                logger.debug(f"Ошибка парсинга карточки: {e}")
                continue

    def _parse_card(self, card: Any) -> Optional[TenderSearchResult]:
        law_elem = card.find("div", class_="registry-entry__header-top__title")
        law_text = law_elem.get_text(strip=True) if law_elem else ""

        if "223" in law_text:
            law = "223-FZ"
        elif "615" in law_text:
            law = "615-FZ"
        elif "44" in law_text:
            law = "44-FZ"
        else:
            law = "44/223/615"

        number_elem = card.find("div", class_="registry-entry__header-mid__number")
        if not number_elem:
            return None

        link = number_elem.find("a", href=True)
        if not link:
            return None

        href = link.get("href", "")
        tender_id = link.get_text(strip=True).replace("№", "").strip()

        if not tender_id:
            return None

        purchase_url = self._normalize_purchase_url(href, law, tender_id)

        status_elem = card.find("div", class_="registry-entry__header-mid__title")
        status = status_elem.get_text(strip=True) if status_elem else None

        title = ""
        obj_block = card.find("div", class_="registry-entry__body-block")
        if obj_block:
            title_elem = obj_block.find("div", class_="registry-entry__body-value")
            if title_elem:
                title = title_elem.get_text(strip=True)

        customer = None
        customer_elem = card.find("div", class_="registry-entry__body-href")
        if customer_elem:
            customer_link = customer_elem.find("a")
            if customer_link:
                customer = customer_link.get_text(strip=True)

        nmck = None
        price_elem = card.find("div", class_="price-block__value")
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            nmck = self._parse_price(price_text)

        publish_date = None
        deadline_date = None

        data_blocks = card.find_all("div", class_="data-block__value")
        data_titles = card.find_all("div", class_="data-block__title")

        for title_elem, value_elem in zip(data_titles, data_blocks):
            title_text = title_elem.get_text(strip=True)
            value_text = value_elem.get_text(strip=True)

            if "Размещено" in title_text:
                publish_date = value_text
            elif "Окончание подачи заявок" in title_text:
                deadline_date = value_text

        notice_guid = None
        if law in ["223-FZ", "615-FZ"]:
            docs_href = card.find("a", href=re.compile(r"noticeGuid="))
            if docs_href:
                href_docs = docs_href.get("href", "")
                match = re.search(r"noticeGuid=([^&]+)", href_docs)
                if match:
                    notice_guid = match.group(1)

        return TenderSearchResult(
            tender_id=tender_id,
            title=title,
            url=purchase_url,
            nmck=nmck,
            region=None,
            publish_date=publish_date,
            deadline_date=deadline_date,
            etp="zakupki.gov.ru",
            law=law,
            okpd2=[],
            customer=customer,
            status=status,
            notice_guid=notice_guid,
        )

    def _normalize_purchase_url(self, href: str, law: str, tender_id: str) -> str:
        if href.startswith("http"):
            return href

        if law in ["223-FZ", "615-FZ"]:
            return f"https://zakupki.gov.ru/223/purchase/public/purchase/info/common-info.html?regNumber={tender_id}"

        return f"https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber={tender_id}"

    def _parse_price(self, price_text: str) -> Optional[float]:
        cleaned = price_text.replace("\xa0", "").replace(" ", "").replace("\u202f", "")
        cleaned = cleaned.replace("₽", "").replace("руб.", "").replace("руб", "")

        match = re.search(r"([\d\s]+(?:[,.]\d{2})?)", cleaned)
        if match:
            price_str = match.group(1).replace(" ", "").replace(",", ".")
            try:
                return float(price_str)
            except ValueError:
                pass
        return None

    # =================================================================
    # === ИСПРАВЛЕННЫЕ ФИЛЬТРЫ ===
    # =================================================================

    def _is_relevant(self, text: str) -> bool:
        """Проверяет релевантность по ключевым словам."""
        if not text:
            return False

        text_lower = text.lower()
        for keyword in self.config.get("relevance_keywords", []):
            if keyword.lower() in text_lower:
                return True
        return False

    def _has_excluded_keywords(self, text: str) -> bool:
        """
        Проверяет наличие запрещённых слов.
        ИСПРАВЛЕНО: контекстная проверка + исключения для СОУТ/ОПР.
        """
        if not text:
            return False

        text_lower = text.lower()

        # Проверка 1: Контекстные исключения
        # Если в тексте есть слова СОУТ/ОПР/охрана труда — проверяем "информационная безопасность" отдельно
        has_sout_context = any(
            exc.lower() in text_lower
            for exc in self.config.get("exclude_context_exceptions", [])
        )

        # Проверка 2: Точные подстроки
        for keyword in self.config.get("exclude_keywords", []):
            keyword_lower = keyword.lower()
            if keyword_lower in text_lower:
                # ИСПРАВЛЕНО: если контекст СОУТ/ОПР и найдено "информационная безопасность" — пропускаем
                if has_sout_context and "информационная безопасность" in keyword_lower:
                    logger.info(f"  ✅ Пропущено '{keyword}' (контекст СОУТ/ОПР)")
                    continue
                logger.debug(f"  🚫 Найдено запрещённое: '{keyword}'")
                return True

        # Проверка 3: Составные фразы (слова рядом)
        for composite in self.config.get("exclude_composite", []):
            if self._check_composite(text_lower, composite):
                # ИСПРАВЛЕНО: если контекст СОУТ/ОПР — не отклонять
                if has_sout_context and composite.get("check_context", False):
                    logger.info(f"  ✅ Пропущена составная фраза (контекст СОУТ/ОПР)")
                    continue
                logger.debug(f"  🚫 Составная фраза: {composite['words']}")
                return True

        return False

    def _check_composite(self, text: str, composite: dict) -> bool:
        """Проверяет, находятся ли слова из composite рядом в тексте."""
        words = composite.get("words", [])
        max_distance = composite.get("max_distance", 5)

        if len(words) < 2:
            return False

        text_words = text.split()

        for i, w1 in enumerate(text_words):
            for word in words:
                if word in w1:
                    for other_word in words:
                        if other_word == word:
                            continue
                        for j in range(
                            max(0, i - max_distance),
                            min(len(text_words), i + max_distance + 1),
                        ):
                            if other_word in text_words[j]:
                                return True

        return False

    def search_and_save(
        self,
        output_file: Optional[str] = None,
        max_pages: Optional[int] = None,
        max_results: Optional[int] = None,
    ) -> List[Dict]:
        if not self.parsing_only:
            logger.warning("parsing_only=False, но вызван search_and_save()")

        logger.info(f"\n{'='*60}")
        logger.info(f"🔍 РЕЖИМ ТОЛЬКО ПАРСИНГА")
        logger.info(
            f"📋 Фильтр НМЦК: ≥{self.config.get('min_nmck', 100000):,}₽ (на стороне ЕИС)"
        )
        logger.info(f"📋 Период: {self.config.get('publish_date_days', 14)} дней")
        logger.info(f"📋 Законы: {self.config.get('laws', [])}")
        logger.info(f"📋 ОКПД2: {self.config.get('okpd2_codes', [])}")
        logger.info(f"{'='*60}")

        results = []

        for tender in self.search(max_pages=max_pages, max_results=max_results):
            data = tender.to_dict()
            results.append(data)

            print(f"\n{'─'*60}")
            print(f"🆔 {tender.tender_id}")
            print(f"📌 {tender.title[:80]}...")
            print(
                f"💰 НМЦК: {tender.nmck:,.0f} ₽"
                if tender.nmck
                else "💰 НМЦК: не указана"
            )
            print(f"🏢 Заказчик: {tender.customer or 'не указан'}")
            print(f"📅 Размещено: {tender.publish_date or 'не указана'}")
            print(f"⏰ Срок: {tender.deadline_date or 'не указан'}")
            print(f"⚖️ Закон: {tender.law}")
            print(f"📋 Статус: {tender.status or 'не указан'}")
            print(f"🔗 {tender.url}")
            print(f"{'─'*60}")

        if output_file and results:
            self._save_results(results, output_file)

        logger.info(f"\n✅ Найдено тендеров: {len(results)}")
        return results

    def _save_results(self, results: List[Dict], output_file: str):
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "search_date": datetime.now().isoformat(),
            "config": {
                "okpd2_ids": self.config.get("okpd2_ids", []),
                "okpd2_codes": self.config.get("okpd2_codes", []),
                "min_nmck": self.config.get("min_nmck"),
                "laws": self.config.get("laws", []),
                "relevance_keywords": self.config.get("relevance_keywords", []),
            },
            "total": len(results),
            "tenders": results,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"💾 Результаты сохранены: {output_path}")
        print(f"\n💾 Сохранено в: {output_path}")


def create_searcher(
    parsing_only: bool = True,
    proxy: Optional[str] = None,
    config: Optional[Dict] = None,
) -> TenderSearcher:
    return TenderSearcher(
        config=config,
        proxy=proxy,
        parsing_only=parsing_only,
    )
