"""
Фасад для поиска тендеров на zakupki.gov.ru (ЕИС).
Делегирует работу специализированным модулям.

Багфиксы v6.6-r2:
  - Разделён на: url_builder, filters, parser
  - Улучшенная обработка 429
  - Фильтр по сроку + сортировка по дедлайну
"""

import sys
import random
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import List, Dict, Optional, Any, Generator
from datetime import datetime
from pathlib import Path
import re
import time
import json

from loguru import logger

from core.http_session import (
    get_session_manager,
    get_random_user_agent,
    get_platform_from_ua,
)
from utils.price_parser import get_price_parser
from core.risk_rules import RiskAnalyzer

from core.search.url_builder import SearchUrlBuilder
from core.search.filters import TenderFilters
from core.search.parser import SearchResultParser, TenderSearchResult

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as curl_requests
    HAS_CURL_CFFI = False
    logger.warning("⚠️ curl_cffi не найдена, используем обычный requests")

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ← v6.3.1: Загрузка exclude_keywords из risk_rules.yaml
def _load_exclude_keywords() -> List[str]:
    try:
        analyzer = RiskAnalyzer()
        forbidden = analyzer.rules.get("forbidden_directions", [])
        keywords = []
        for item in forbidden:
            pattern = item.get("pattern", "")
            text = (
                pattern.replace("\\b", "").replace("\\s+", " ").replace("[\\s]+", " ")
            )
            text = re.sub(r"[?.*+^$()\[\]{}|]", "", text)
            if text and len(text) > 3:
                keywords.append(text)
        if keywords:
            logger.info(
                f"[v6.3.1] exclude_keywords загружены из risk_rules.yaml: {len(keywords)} правил"
            )
            return keywords
    except Exception as e:
        logger.warning(f"[v6.3.1] Не удалось загрузить exclude_keywords из YAML: {e}")

    logger.info("[v6.3.1] Используем inline exclude_keywords (fallback)")
    return [
        "лицензия МЧС", "медицинские работники", "водительские права",
        "гражданская оборона", "категорированные организации",
        "охранники с оружием", "лицензия ФСБ", "гостайна",
        "исследования воды", "смывы", "гельминты", "биология",
        "микробиолог", "экспертиза промышленной безопасности",
        "экспертизы промышленной безопасности",
        "экспертизе промышленной безопасности",
        "экспертизу промышленной безопасности",
        "экспертизой промышленной безопасности",
        "экспертиз промышленной безопасности",
        "экспертиза безопасности", "экспертизы безопасности",
        "техническая диагностика", "промбезопасность", "промбезопасности",
    ]


def _load_context_exceptions() -> List[str]:
    """Загружает allow_if_types из risk_rules.yaml для контекстной проверки."""
    try:
        analyzer = RiskAnalyzer()
        forbidden = analyzer.rules.get("forbidden_directions", [])
        exceptions = set()
        for item in forbidden:
            allowed = item.get("allow_if_types", [])
            exceptions.update(allowed)
        return list(exceptions)
    except Exception:
        return ["sout", "opr", "соут", "опр"]


SEARCH_CONFIG = {
    "okpd2_ids": ["8874806", "8879198", "8879202"],
    "okpd2_codes": ["85.42", "71.20.11", "71.20.19"],
    "relevance_keywords": [
        "охрана труда", "охране труда", "охраны труда",
        "СОУТ", "специальная оценка условий труда",
        "специальной оценки условий труда", "специальной оценке условий труда",
        "оценка профессиональных рисков", "оценке профессиональных рисков",
        "оценки профессиональных рисков", "производственный лабораторный контроль",
        "производственного лабораторного контроля", "замеры вредных факторов",
        "замеров вредных факторов", "вредные производственные факторы",
        "вредных производственных факторов", "обучение охране труда",
        "обучению охране труда", "обучения охране труда",
        "оценка проф. рисков", "пожарная безопасность", "пожарной безопасности",
        "промышленная безопасность", "промышленной безопасности",
        "обучение рабочих профессий", "технологические карты",
        "санитарно-защитная зона", "тренинги", "инструктажи",
        "аттестация рабочих мест", "аттестации рабочих мест",
    ],
    "exclude_keywords": _load_exclude_keywords(),
    "exclude_context_exceptions": _load_context_exceptions(),
    "exclude_composite": [
        {"words": ["экспертиз", "безопасност"], "max_distance": 5, "check_context": True},
        {"words": ["диагностик", "оборудован"], "max_distance": 5, "check_context": True},
        {"words": ["техническ", "диагностик"], "max_distance": 5, "check_context": True},
    ],
    "min_nmck": 100000,
    "max_nmck_siz": 300000,
    "laws": ["44-FZ", "223-FZ"],
    "publish_date_days": 3,
    "min_days_to_deadline": 3,
}


class TenderSearcher:
    """Фасад для поиска тендеров."""

    MAX_SEARCH_WORKERS = 5
    BATCH_SIZE = 50
    REQUEST_DELAY = (0.5, 1.5)

    def __init__(
        self,
        config: Optional[Dict] = None,
        proxy: Optional[str] = None,
    ):
        self.config = config or SEARCH_CONFIG
        self.proxy = proxy

        # Подсистемы
        self.url_builder = SearchUrlBuilder(config=self.config)
        self.filters = TenderFilters(config=self.config)
        self.parser = SearchResultParser(
            url_builder=self.url_builder,
            price_parser=get_price_parser(),
        )

        # HTTP-сессии
        self.session_manager = get_session_manager(pool_size=3)
        self.session = self.session_manager.get_primary_session()
        self._sessions = [self.session_manager.get_session(i) for i in range(3)]
        self._lock = Lock()
        self._consecutive_429 = 0
        self._base_delay = 5

        logger.info(
            f"🔍 TenderSearcher initialized ({len(self._sessions)} sessions)"
        )

    def _get_session(self, index: int = 0) -> Any:
        if not self._sessions:
            return self.session
        return self._sessions[index % len(self._sessions)]

    def _rotate_user_agent(self):
        user_agent = get_random_user_agent()
        platform = get_platform_from_ua(user_agent)
        for sess in self._sessions:
            self.session_manager._update_session_headers(sess, user_agent, platform)
        logger.debug("🎭 User-Agent изменён")

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
        """Основной метод поиска тендеров."""
        total_found = 0
        seen_ids = set()
        all_results = []

        logger.info(f"\n{'='*60}")
        logger.info(f"📋 Поиск тендеров")
        logger.info(f"📋 Фильтр НМЦК: ≥{self.config.get('min_nmck', 100000):,}₽")
        logger.info(f"📋 Период: {self.config.get('publish_date_days', 14)} дней")
        logger.info(f"📋 Законы: {self.config.get('laws', [])}")
        logger.info(f"📋 ОКПД2: {self.config.get('okpd2_codes', [])}")
        logger.info(f"📋 Мин. дней до дедлайна: {self.config.get('min_days_to_deadline', 3)}")
        logger.info(f"📋 Сортировка: по дедлайну (ближайшие первые)")
        logger.info(f"{'='*60}")

        for result in self._search_single(max_pages):
            if result.tender_id in seen_ids:
                continue
            seen_ids.add(result.tender_id)

            # Фильтр релевантности
            if not self.filters.is_relevant(result.title):
                logger.debug(f"  ⏭ {result.tender_id}: не релевантно")
                continue

            # Фильтр запрещённых слов
            if self.filters.has_excluded_keywords(result.title):
                logger.info(f"  🚫 {result.tender_id}: запрещённые слова")
                continue

            # Фильтр срока
            passed, days_left = self.filters.check_deadline(result.deadline_date)
            if not passed:
                logger.info(f"  ⏭ {result.tender_id}: до дедлайна {days_left} дней — пропущен")
                continue

            all_results.append(result)

        # Сортировка по дедлайну
        all_results = self.filters.sort_by_deadline(all_results)

        for result in all_results:
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
        """Выполняет поиск по страницам."""
        url = self.url_builder.build_search_url(page=1)
        response = self._make_request(url)

        if not response:
            logger.error("❌ Не удалось загрузить первую страницу")
            return

        if "captcha" in response.text.lower():
            logger.error("🤖 CAPTCHA!")
            return

        total_count = self.parser.extract_total_count(response.text)
        if total_count == 0:
            logger.info("  ⏭ Нет результатов")
            return

        total_pages = min((total_count // 50) + 1, max_pages or 100)
        logger.info(f"📊 ~{total_count} записей, страниц: {total_pages}")

        for result in self.parser.parse_search_page(response.text):
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
                        logger.info(f"  📄 Прогресс: {completed}/{len(remaining_pages)}")

                except concurrent.futures.TimeoutError:
                    logger.warning(f"  ⏰ Таймаут страницы {page}")
                except Exception as e:
                    logger.error(f"  ❌ Ошибка страницы {page}: {e}")

    def _fetch_page(self, page: int) -> Optional[List[TenderSearchResult]]:
        """Загружает и парсит одну страницу."""
        session = self._get_session(page)
        url = self.url_builder.build_search_url(page=page)

        time.sleep(random.uniform(0.1, 0.3))

        try:
            response = session.get(url, timeout=30)

            if response.status_code == 429:
                time.sleep(2)
                response = session.get(url, timeout=30)

            if response.status_code == 200:
                if "captcha" in response.text.lower():
                    return None
                return list(self.parser.parse_search_page(response.text))

            logger.warning(f"  ⚠️ Страница {page}: статус {response.status_code}")
            return None

        except Exception as e:
            logger.error(f"  ❌ Страница {page}: {e}")
            return None

    def search_and_save(
        self,
        output_file: Optional[str] = None,
        max_pages: Optional[int] = None,
        max_results: Optional[int] = None,
    ) -> List[Dict]:
        """Поиск с сохранением результатов."""
        logger.info(f"\n{'='*60}")
        logger.info(f"🔍 РЕЖИМ ТОЛЬКО ПАРСИНГА")
        logger.info(f"📋 Фильтр НМЦК: ≥{self.config.get('min_nmck', 100000):,}₽")
        logger.info(f"📋 Период: {self.config.get('publish_date_days', 14)} дней")
        logger.info(f"📋 Законы: {self.config.get('laws', [])}")
        logger.info(f"📋 ОКПД2: {self.config.get('okpd2_codes', [])}")
        logger.info(f"📋 Мин. дней до дедлайна: {self.config.get('min_days_to_deadline', 3)}")
        logger.info(f"📋 Сортировка: по дедлайну (ближайшие первые)")
        logger.info(f"{'='*60}")

        results = []

        for tender in self.search(max_pages=max_pages, max_results=max_results):
            data = tender.to_dict()
            results.append(data)

            print(f"\n{'-'*60}")
            print(f"🆔 {tender.tender_id}")
            print(f"📌 {tender.title[:80]}...")
            print(f"💰 НМЦК: {tender.nmck:,.0f} ₽" if tender.nmck else "💰 НМЦК: не указана")
            print(f"🏢 Заказчик: {tender.customer or 'не указан'}")
            print(f"📅 Размещено: {tender.publish_date or 'не указана'}")
            print(f"⏰ Срок: {tender.deadline_date or 'не указан'}")
            print(f"⚖️ Закон: {tender.law}")
            print(f"📋 Статус: {tender.status or 'не указан'}")
            print(f"🔗 {tender.url}")
            print(f"{'-'*60}")

        if output_file and results:
            self._save_results(results, output_file)

        logger.info(f"\n✅ Найдено тендеров: {len(results)}")
        return results

    def _save_results(self, results: List[Dict], output_file: str):
        """Сохраняет результаты в JSON."""
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
                "min_days_to_deadline": self.config.get("min_days_to_deadline", 3),
            },
            "total": len(results),
            "tenders": results,
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"💾 Результаты сохранены: {output_path}")
        print(f"\n💾 Сохранено в: {output_path}")


def create_searcher(
    proxy: Optional[str] = None,
    config: Optional[Dict] = None,
) -> TenderSearcher:
    return TenderSearcher(
        config=config,
        proxy=proxy,
    )
