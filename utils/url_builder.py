"""
utils/url_builder.py
Единый генератор URL для zakupki.gov.ru.
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - Консолидирована логика из searcher.py, detailed_parser.py, analyzer.py, main.py
  - Поддержка 4 типов 44-ФЗ: ea20, zk20, ezt20, ok20
  - 223-ФЗ: 1 вид
  - 615/94-ФЗ: исключены (не нужны)
  - Авто-определение типа закупки 44-ФЗ по ID (эвристика)
"""

from typing import Optional, List
from urllib.parse import urljoin
from datetime import datetime, date, timedelta
from loguru import logger

BASE_URL = "https://zakupki.gov.ru"


class TenderURLBuilder:
    """
    Единый билдер URL тендеров zakupki.gov.ru.
    Заменяет: inline URL в searcher.py, detailed_parser.py, analyzer.py, main.py.
    """

    # Типы закупок 44-ФЗ
    FZ44_TYPES = {
        "ea20": "электронный аукцион",
        "zk20": "запрос котировок",
        "ezt20": "электронный запрос котировок",
        "ok20": "открытый конкурс",
    }

    # Эвристика: по ID определяем тип (fallback)
    # Первые 3 цифры региона → предположительный тип
    ID_PREFIX_PATTERNS = {
        # Можно расширить при необходимости
    }

    def __init__(self):
        logger.info("TenderURLBuilder инициализирован (v6.3)")

    def build_common_info_url(
        self,
        reg_number: str,
        law_type: str = "44",
        notice_guid: str = "",
        purchase_type_44: str = "ea20",
    ) -> str:
        """
        Строит URL common-info.

        Args:
            reg_number: Регистрационный номер тендера
            law_type: "44", "223", "615" (615 исключён, но оставлен для совместимости)
            notice_guid: GUID для 223-ФЗ
            purchase_type_44: Тип закупки 44-ФЗ (ea20/zk20/ezt20/ok20)
        """
        law = str(law_type).replace("-FZ", "").replace("-ФЗ", "")

        if law == "223":
            return self._build_223_common_info(reg_number, notice_guid)
        elif law == "44":
            return self._build_44_common_info(reg_number, purchase_type_44)
        elif law == "615":
            # 615-ФЗ не поддерживается, fallback на 223
            logger.warning(
                f"615-ФЗ не поддерживается, используем 223-ФЗ URL для {reg_number}"
            )
            return self._build_223_common_info(reg_number, notice_guid)
        else:
            # Неизвестный закон — fallback на 44
            logger.warning(
                f"Неизвестный закон '{law}', fallback на 44-ФЗ для {reg_number}"
            )
            return self._build_44_common_info(reg_number, purchase_type_44)

    def build_documents_url(
        self,
        reg_number: str,
        law_type: str = "44",
        notice_guid: str = "",
        purchase_type_44: str = "ea20",
    ) -> str:
        """Строит URL страницы документов."""
        law = str(law_type).replace("-FZ", "").replace("-ФЗ", "")

        if law == "223":
            return self._build_223_documents(reg_number, notice_guid)
        elif law == "44":
            return self._build_44_documents(reg_number, purchase_type_44)
        else:
            return self._build_44_documents(reg_number, purchase_type_44)

    def build_search_url(
        self,
        page: int = 1,
        date_from: Optional[datetime.date] = None,
        date_to: Optional[datetime.date] = None,
        okpd2_ids: Optional[List[str]] = None,
        min_nmck: Optional[int] = None,
        publish_date_days: int = 3,
    ) -> str:
        """
        Строит URL поиска тендеров на ЕИС.

        Args:
            page: Номер страницы
            date_from: Дата публикации от
            date_to: Дата публикации до
            okpd2_ids: Список ID ОКПД2
            min_nmck: Минимальная НМЦК
            publish_date_days: Дней назад для публикации
        """
        from datetime import datetime, timedelta
        from urllib.parse import urlencode

        today = datetime.now().date()
        if date_from is None:
            date_from = today - timedelta(days=publish_date_days)
        if date_to is None:
            date_to = today

        params = {
            "morphology": "on",
            "search-filter": "Дате+размещения",
            "sortBy": "DEADLINE",
            "sortDirection": "false",
            "publishDateFrom": date_from.strftime("%d.%m.%Y"),
            "currencyIdGeneral": "-1",
            "showLotsInfoHidden": "false",
            "pageNumber": str(page),
            "recordsPerPage": "_50",
            "fz44": "on",
            "fz223": "on",
            "af": "on",
            "priceFromGeneral": str(min_nmck or 100000),
        }

        if okpd2_ids:
            params["okpd2Ids"] = ",".join(okpd2_ids)
            params["okpd2IdsWithNested"] = "on"

        url = f"{BASE_URL}/epz/order/extendedsearch/results.html?{urlencode(params, safe='{}')}"
        logger.info(
            f"[SearchUrlBuilder] URL (стр. {page}, {date_from}–{date_to}): {url}"
        )
        return url

    def _build_44_common_info(
        self, reg_number: str, purchase_type: str = "ea20"
    ) -> str:
        """URL common-info для 44-ФЗ."""
        if purchase_type not in self.FZ44_TYPES:
            logger.warning(f"Неизвестный тип 44-ФЗ '{purchase_type}', используем ea20")
            purchase_type = "ea20"

        return (
            f"{BASE_URL}/epz/order/notice/{purchase_type}/view/common-info.html"
            f"?regNumber={reg_number}"
        )

    def _build_44_documents(self, reg_number: str, purchase_type: str = "ea20") -> str:
        """URL документов для 44-ФЗ."""
        if purchase_type not in self.FZ44_TYPES:
            purchase_type = "ea20"

        return (
            f"{BASE_URL}/epz/order/notice/{purchase_type}/view/documents.html"
            f"?regNumber={reg_number}"
        )

    def _build_223_common_info(self, reg_number: str, notice_guid: str = "") -> str:
        """URL common-info для 223-ФЗ."""
        # v6.8.6-r3: Исправлен путь 223-ФЗ (notice223 вместо ea223/view)
        if notice_guid:
            return (
                f"{BASE_URL}/epz/order/notice/notice223/common-info.html"
                f"?noticeGuid={notice_guid}&regNumber={reg_number}"
            )
        # Без noticeGuid — zakupki.gov.ru сам редиректит и добавит noticeGuid
        return (
            f"{BASE_URL}/epz/order/notice/notice223/common-info.html"
            f"?regNumber={reg_number}"
        )

    def _build_223_documents(self, reg_number: str, notice_guid: str = "") -> str:
        """URL документов для 223-ФЗ."""
        # v6.8.6-r3: Исправлен путь 223-ФЗ
        if notice_guid:
            return (
                f"{BASE_URL}/epz/order/notice/notice223/documents.html"
                f"?purchaseNoticeNumber={reg_number}&noticeGuid={notice_guid}"
            )
        # Fallback без noticeGuid (редиректит)
        return (
            f"{BASE_URL}/epz/order/notice/notice223/documents.html"
            f"?purchaseNoticeNumber={reg_number}"
        )

    def detect_44_purchase_type(self, title: str = "", method: str = "") -> str:
        """
        Эвристика определения типа закупки 44-ФЗ по названию/способу.
        Возвращает: ea20 | zk20 | ezt20 | ok20
        """
        text = f"{title} {method}".lower()

        if "открытый конкурс" in text or "конкурс" in text:
            return "ok20"
        elif "электронный запрос котировок" in text or "эзк" in text:
            return "ezt20"
        elif "запрос котировок" in text or "зк" in text:
            return "zk20"
        elif "аукцион" in text or "электронный аукцион" in text:
            return "ea20"

        # По умолчанию — самый распространённый
        return "ea20"

    def get_purchase_type_name(self, purchase_type: str) -> str:
        """Возвращает человекочитаемое название типа закупки."""
        return self.FZ44_TYPES.get(purchase_type, "неизвестный тип")


# Глобальный инстанс для удобства
_url_builder = None


def get_url_builder() -> TenderURLBuilder:
    global _url_builder
    if _url_builder is None:
        _url_builder = TenderURLBuilder()
    return _url_builder


def build_common_info_url(
    reg_number: str,
    law_type: str = "44",
    notice_guid: str = "",
    purchase_type_44: str = "ea20",
) -> str:
    """Удобная функция для быстрого построения URL."""
    return get_url_builder().build_common_info_url(
        reg_number, law_type, notice_guid, purchase_type_44
    )


def build_documents_url(
    reg_number: str,
    law_type: str = "44",
    notice_guid: str = "",
    purchase_type_44: str = "ea20",
) -> str:
    """Удобная функция для быстрого построения URL документов."""
    return get_url_builder().build_documents_url(
        reg_number, law_type, notice_guid, purchase_type_44
    )
