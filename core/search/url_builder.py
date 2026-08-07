"""
Построение URL для поиска тендеров на zakupki.gov.ru.
Вынесено из searcher.py (v6.6-r2).
"""

from datetime import datetime, timedelta
from urllib.parse import urlencode
from typing import Optional, Dict, Any
from loguru import logger


class SearchUrlBuilder:
    """Строит URL для поиска тендеров на ЕИС."""

    BASE_SEARCH_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/results.html"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def build_search_url(
        self,
        page: int = 1,
        date_from: Optional[datetime.date] = None,
        date_to: Optional[datetime.date] = None,
    ) -> str:
        """Строит URL поиска с заданными параметрами."""
        today = datetime.now().date()

        if date_from is None:
            date_from = today - timedelta(days=self.config.get("publish_date_days", 3))
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
            "priceFromGeneral": str(self.config.get("min_nmck", 100000)),
        }

        if self.config.get("okpd2_ids"):
            params["okpd2Ids"] = ",".join(self.config["okpd2_ids"])
            params["okpd2IdsWithNested"] = "on"
            if self.config.get("okpd2_codes"):
                params["okpd2IdsCodes"] = ",".join(self.config["okpd2_codes"])

        url = f"{self.BASE_SEARCH_URL}?{urlencode(params, safe='{}')}"
        logger.info(f"[SearchUrlBuilder] URL (стр. {page}, {date_from}–{date_to}): {url}")
        return url

    def build_common_info_url(self, reg_number: str, law_type: str = "44") -> str:
        """Строит URL карточки тендера."""
        base = "https://zakupki.gov.ru"
        if law_type == "44":
            return f"{base}/epz/order/notice/ea44/view/common-info.html?regNumber={reg_number}"
        elif law_type == "223":
            return f"{base}/epz/order/notice/ezk/view/common-info.html?regNumber={reg_number}"
        else:
            return f"{base}/epz/order/notice/common-info.html?regNumber={reg_number}"

    def build_documents_url(self, reg_number: str, notice_guid: Optional[str] = None, law_type: str = "44") -> str:
        """Строит URL документов тендера."""
        base = "https://zakupki.gov.ru"
        if law_type == "44":
            return f"{base}/epz/order/notice/ea44/view/documents.html?regNumber={reg_number}"
        elif law_type == "223" and notice_guid:
            return f"{base}/epz/order/notice/ezk/view/documents.html?regNumber={reg_number}&noticeGuid={notice_guid}"
        elif law_type == "223":
            return f"{base}/epz/order/notice/ezk/view/documents.html?regNumber={reg_number}"
        else:
            return f"{base}/epz/order/notice/documents.html?regNumber={reg_number}"
