"""
core/parsers/detailed_parser.py
Детальный парсинг карточки тендера (common-info + документы).

ИСПРАВЛЕНО (v6.8.6):
- Исправлен NameError: 'soup' is not defined в fetch_and_parse()
- Улучшена проверка блокировки (не ловит "Личный кабинет" в навигации)
- Добавлен парсинг РМ из КТРУ-таблицы
- Улучшены селекторы для 44-ФЗ (регион, заказчик, ЭТП, НМЦК)
"""

import re
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup
from loguru import logger

# v6.8.5: Словарь город -> регион для fallback
CITY_TO_REGION = {
    "москва": "Москва",
    "санкт-петербург": "Санкт-Петербург",
    "севастополь": "Севастополь",
    "новосибирск": "Новосибирская обл",
    "екатеринбург": "Свердловская обл",
    "нижний новгород": "Нижегородская обл",
    "казань": "Республика Татарстан",
    "самара": "Самарская обл",
    "омск": "Омская обл",
    "челябинск": "Челябинская обл",
    "ростов-на-дону": "Ростовская обл",
    "уфа": "Республика Башкортостан",
    "красноярск": "Красноярский край",
    "пермь": "Пермский край",
    "воронеж": "Воронежская обл",
    "волгоград": "Волгоградская обл",
    "краснодар": "Краснодарский край",
    "саратов": "Саратовская обл",
    "тюмень": "Тюменская обл",
    "тольятти": "Самарская обл",
    "ижевск": "Удмуртская Республика",
    "барнаул": "Алтайский край",
    "иркутск": "Иркутская обл",
    "хабаровск": "Хабаровский край",
    "ярославль": "Ярославская обл",
    "владивосток": "Приморский край",
    "томск": "Томская обл",
    "оренбург": "Оренбургская обл",
    "кемерово": "Кемеровская обл",
    "новокузнецк": "Кемеровская обл",
    "рязань": "Рязанская обл",
    "набережные челны": "Республика Татарстан",
    "астрахань": "Астраханская обл",
    "пенза": "Пензенская обл",
    "липецк": "Липецкая обл",
    "тула": "Тульская обл",
    "киров": "Кировская обл",
    "чебоксары": "Чувашская Республика",
    "калининград": "Калининградская обл",
    "брянск": "Брянская обл",
    "курск": "Курская обл",
    "иваново": "Ивановская обл",
    "магнитогорск": "Челябинская обл",
    "тверь": "Тверская обл",
    "ставрополь": "Ставропольский край",
    "симферополь": "Республика Крым",
    "белгород": "Белгородская обл",
    "архангельск": "Архангельская обл",
    "курган": "Курганская обл",
    "сургут": "Ханты-Мансийский АО",
    "орёл": "Орловская обл",
    "чита": "Забайкальский край",
    "мурманск": "Мурманская обл",
    "смоленск": "Смоленская обл",
    "тамбов": "Тамбовская обл",
    "владимир": "Владимирская обл",
    "петрозаводск": "Республика Карелия",
    "нижневартовск": "Ханты-Мансийский АО",
    "йошкар-ола": "Республика Марий Эл",
    "саранск": "Республика Мордовия",
    "новороссийск": "Краснодарский край",
    "якутск": "Республика Саха (Якутия)",
    "надым": "Ямало-Ненецкий АО",
    "салехард": "Ямало-Ненецкий АО",
    "новый уренгой": "Ямало-Ненецкий АО",
    "калуга": "Калужская обл",
    "сочи": "Краснодарский край",
    "петропавловск-камчатский": "Камчатский край",
    "сыктывкар": "Республика Коми",
    "ухта": "Республика Коми",
    "вологда": "Вологодская обл",
    "северодвинск": "Архангельская обл",
    "череповец": "Вологодская обл",
    "орск": "Оренбургская обл",
    "бузулук": "Оренбургская обл",
    "абакан": "Республика Хакасия",
    "майкоп": "Республика Адыгея",
    "нальчик": "Кабардино-Балкарская Республика",
    "владикавказ": "Республика Северная Осетия",
    "грозный": "Чеченская Республика",
    "махачкала": "Республика Дагестан",
    "черкесск": "Карачаево-Черкесская Республика",
    "элиста": "Республика Калмыкия",
    "альметьевск": "Республика Татарстан",
    "нижнекамск": "Республика Татарстан",
    "зеленодольск": "Республика Татарстан",
    "стерлитамак": "Республика Башкортостан",
    "салават": "Республика Башкортостан",
    "нефтеюганск": "Ханты-Мансийский АО",
    "ноябрьск": "Ямало-Ненецкий АО",
    "губкинский": "Ямало-Ненецкий АО",
    "мирный": "Республика Саха (Якутия)",
    "ленск": "Республика Саха (Якутия)",
    "алдан": "Республика Саха (Якутия)",
    "нижний бестях": "Республика Саха (Якутия)",
    "покачи": "Ханты-Мансийский АО",
    "лангепас": "Ханты-Мансийский АО",
    "радужный": "Ханты-Мансийский АО",
    "урай": "Ханты-Мансийский АО",
    "когалым": "Ханты-Мансийский АО",
    "тобольск": "Тюменская обл",
    "ишим": "Тюменская обл",
    "ялуторовск": "Тюменская обл",
    "заводоуковск": "Тюменская обл",
    "шадринск": "Курганская обл",
    "катайск": "Курганская обл",
    "далматово": "Курганская обл",
    "куртамыш": "Курганская обл",
    "петухово": "Курганская обл",
    "щучье": "Курганская обл",
    "макушино": "Курганская обл",
    "варгаши": "Курганская обл",
    "каргаполье": "Курганская обл",
    "юргамыш": "Курганская обл",
    "альменево": "Курганская обл",
    "целинное": "Курганская обл",
    "частоозерье": "Курганская обл",
    "шумиха": "Курганская обл",
    "шатрово": "Курганская обл",
    "мишкино": "Курганская обл",
    "глядянское": "Курганская обл",
    "мокроусово": "Курганская обл",
    "притобольный": "Курганская обл",
    "сафакулево": "Курганская обл",
}

# v6.8: Ключевые слова для каскадного определения типа
TYPE_KEYWORDS = {
    "sout": [
        "специальная оценка условий труда",
        "специальной оценки условий труда",
        "специальной оценке условий труда",
        "специальную оценку условий труда",
        "соут",
        "оценка условий труда",
        "оценки условий труда",
        "спецоценка",
        "вредные производственные факторы",
        "идентификация потенциально вредных",
        "класс условий труда",
        "классы условий труда",
        "декларация соответствия условий труда",
        "карта соут",
        "карты соут",
        "протоколы измерений",
        "исследования факторов",
        "измерение вредных факторов",
        "замеры вредных факторов",
    ],
    "opr": [
        "оценка профессиональных рисков",
        "опр",
        "профессиональный риск",
        "проф. риск",
        "профриск",
        "проф.риск",
        "декларация о соответствии условий труда",
        "мероприятия по снижению рисков",
        "карта оценки профессиональных рисков",
        "методика оценки профессиональных рисков",
        "идентификация опасностей",
        "анализ рисков",
    ],
    "education": [
        "обучение охране труда",
        "обучение по охране труда",
        "программа обучения",
        "программа повышения квалификации",
        "переподготовка",
        "повышение квалификации",
        "профессиональное обучение",
        "дополнительное образование",
        "слушатели",
        "учебные часы",
        "учебный план",
        "протоколы обучения",
        "удостоверение",
        "инструктаж",
        "стажировка",
        "обучение рабочих",
        "обучение по промышленной безопасности",
        "обучение по пожарной безопасности",
        "обучение по электробезопасности",
        "обучение по газовой безопасности",
        "обучение по высотным работам",
    ],
    "plk": [
        "производственный контроль",
        "плк",
        "лабораторные исследования",
        "лабораторный контроль",
        "замеры шума",
        "замеры вибрации",
        "замеры микроклимата",
        "замеры освещенности",
        "замеры электромагнитных полей",
        "анализ воздуха рабочей зоны",
        "санитарно-гигиенические исследования",
        "гигиеническая оценка",
        "санитарно-эпидемиологическая",
        "испытания факторов производственной среды",
    ],
}

# v6.8: ОКПД2 -> тип
OKPD2_TO_TYPE = {
    "85.42": "education",
    "71.20.11": "plk",
    "71.20.19": "plk",
    "71.20.11.190": "plk",
}

RUSSIAN_REGIONS = [
    "Москва",
    "Санкт-Петербург",
    "Севастополь",
    "Московская обл",
    "Ленинградская обл",
    "Новосибирская обл",
    "Свердловская обл",
    "Нижегородская обл",
    "Самарская обл",
    "Омская обл",
    "Челябинская обл",
    "Ростовская обл",
    "Красноярский край",
    "Пермский край",
    "Воронежская обл",
    "Волгоградская обл",
    "Краснодарский край",
    "Саратовская обл",
    "Тюменская обл",
    "Алтайский край",
    "Иркутская обл",
    "Хабаровский край",
    "Приморский край",
    "Астраханская обл",
    "Белгородская обл",
    "Брянская обл",
    "Владимирская обл",
    "Вологодская обл",
    "Ивановская обл",
    "Калининградская обл",
    "Калужская обл",
    "Кемеровская обл",
    "Кировская обл",
    "Костромская обл",
    "Курганская обл",
    "Курская обл",
    "Липецкая обл",
    "Магаданская обл",
    "Мурманская обл",
    "Новгородская обл",
    "Оренбургская обл",
    "Орловская обл",
    "Пензенская обл",
    "Псковская обл",
    "Рязанская обл",
    "Сахалинская обл",
    "Смоленская обл",
    "Тамбовская обл",
    "Тверская обл",
    "Томская обл",
    "Тульская обл",
    "Ульяновская обл",
    "Ярославская обл",
    "Архангельская обл",
    "Республика Адыгея",
    "Республика Алтай",
    "Республика Башкортостан",
    "Республика Бурятия",
    "Республика Дагестан",
    "Республика Ингушетия",
    "Кабардино-Балкарская Республика",
    "Республика Калмыкия",
    "Карачаево-Черкесская Республика",
    "Республика Карелия",
    "Республика Коми",
    "Республика Крым",
    "Республика Марий Эл",
    "Республика Мордовия",
    "Республика Саха (Якутия)",
    "Республика Северная Осетия",
    "Республика Татарстан",
    "Республика Тыва",
    "Удмуртская Республика",
    "Республика Хакасия",
    "Чеченская Республика",
    "Чувашская Республика",
    "Еврейская АО",
    "Ненецкий АО",
    "Ханты-Мансийский АО",
    "Чукотский АО",
    "Ямало-Ненецкий АО",
    "Забайкальский край",
    "Камчатский край",
    "Ставропольский край",
]


@dataclass
class TenderDocument:
    """Документ тендера."""

    name: str
    link: str
    file_type: Optional[str] = None
    size: Optional[int] = None


@dataclass
class TenderDetail:
    """Детальная информация о тендере."""

    tender_id: str
    law: str
    title: str
    customer: str
    region: str
    etp: str
    nmck: float
    publish_date: str
    deadline_date: str
    requirements: str
    warranty_required: bool
    warranty_percent: float
    documents: List[Dict[str, Any]]
    raw_html: str = ""
    notice_guid: Optional[str] = None
    tender_type_hint: Optional[str] = None
    lot_info: Optional[Dict] = None
    customer_address: Optional[str] = None
    type_detection_source: Optional[str] = None
    # v6.8.5: Новые поля для данных из поисковой выдачи
    purchase_name: Optional[str] = None
    customer_name: Optional[str] = None
    customer_region: Optional[str] = None
    delivery_address: Optional[str] = None
    purchase_method: Optional[str] = None
    platform_name: Optional[str] = None
    application_guarantee: Optional[str] = None
    contract_guarantee: Optional[str] = None
    guarantee_method: Optional[str] = None
    documents_text: Optional[str] = None
    addresses_count: int = 1
    cities_count: int = 1
    regions_count: int = 1
    rm_total: int = 0
    students_count: int = 0
    points_count: int = 0
    has_full_time: bool = False
    teacher_days: int = 0
    accommodation_nights: int = 0
    transport_km: int = 0
    venue_rent_days: int = 0
    manikin_days: int = 0
    trip_days: int = 0
    is_seasonal: bool = False
    opr_positions: int = 0
    opr_persons: int = 0
    needs_subcontractor: bool = False


class DetailedParser:
    """Парсер детальной информации о тендере."""

    def __init__(self, session_manager=None):
        self.session_manager = session_manager
        logger.info("DetailedParser инициализирован (v6.8.6)")

    # ==================== v6.8.6: НОВЫЙ МЕТОД ====================

    def fetch_and_parse(
        self,
        reg_number: str,
        law_type: str,
        notice_guid: str = "",
        nmck: float = 0,
        fallback_title: str = "",
        fallback_region: str = "",
        fallback_customer: str = "",
    ) -> Optional[TenderDetail]:
        """
        Загружает common-info и парсит его.
        При блокировке zakupki — возвращает fallback с данными из поисковой выдачи.
        """
        law = law_type if "FZ" in law_type else f"{law_type}-FZ"
        law_clean = law.replace("-FZ", "")

        # Формируем URL
        if law_clean == "223":
            url = f"https://zakupki.gov.ru/epz/order/notice/ea223/view/common-info.html?regNumber={reg_number}"
        elif law_clean == "615":
            url = f"https://zakupki.gov.ru/epz/order/notice/ea615/view/common-info.html?regNumber={reg_number}"
        else:
            url = f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={reg_number}"

        logger.info(f"   [v6.8.6] Загрузка common-info: {url}")

        html = ""
        status_code = None

        # Загружаем HTML через session_manager
        if self.session_manager:
            try:
                # v6.8.6: Добавляем Referer из поисковой выдачи
                session = self.session_manager.get_primary_session()
                headers = {
                    "Referer": f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={reg_number}",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "navigate",
                }
                response = session.get(url, timeout=30, headers=headers)
                status_code = response.status_code
                html = response.text
                logger.info(
                    f"   [v6.8.6] Статус: {status_code}, длина HTML: {len(html)}"
                )
            except Exception as e:
                logger.warning(f"   [v6.8.6] Ошибка загрузки common-info: {e}")

        # v6.8.6: Дебаг-логи — проверяем блокировку
        is_blocked = False
        soup_check = None
        if html:
            # Проверяем НАСТОЯЩУЮ блокировку (редирект на авторизацию)
            # Ложные срабатывания: "Личный кабинет" в навигации, "Вход" в футере
            # Истинная блокировка: title="Вход в личный кабинет" ИЛИ форма авторизации
            soup_check = BeautifulSoup(html, "html.parser")

            # Признаки истинной блокировки:
            # 1. Форма входа (input type="password")
            has_login_form = bool(soup_check.find("input", {"type": "password"}))
            # 2. Title = "Авторизация" или "Вход"
            title_tag = soup_check.find("title")
            is_auth_title = (
                title_tag
                and any(
                    word in title_tag.get_text()
                    for word in ["Авторизация", "Вход", "Личный кабинет"]
                )
                if title_tag
                else False
            )
            # 3. Отсутствие карточки тендера (cardMainInfo)
            has_card = bool(soup_check.find("div", class_="cardMainInfo"))
            # 4. Маленький HTML (< 5000 символов) без реальных данных
            is_too_small = len(html) < 5000

            # Блокировка = (форма входа ИЛИ auth title) И отсутствие карточки
            # ИЛИ слишком маленький HTML
            is_blocked = (
                has_login_form or (is_auth_title and not has_card)
            ) or is_too_small

            if is_blocked:
                logger.warning(
                    f"   [v6.8.6] ⚠️ БЛОКИРОВКА: "
                    f"login_form={has_login_form}, auth_title={is_auth_title}, "
                    f"has_card={has_card}, len={len(html)}"
                )
            else:
                logger.info(
                    f"   [v6.8.6] ✅ common-info ОК: "
                    f"has_card={has_card}, len={len(html)}"
                )
                # Сохраняем HTML для анализа
                try:
                    from pathlib import Path

                    debug_path = Path(f"data/debug_{reg_number}_ok.html")
                    debug_path.parent.mkdir(parents=True, exist_ok=True)
                    debug_path.write_text(html[:2000], encoding="utf-8")
                    logger.info(f"   [v6.8.6] HTML сохранён: {debug_path}")
                except Exception:
                    pass

            # Дополнительная проверка: есть ли реальные данные в карточке
            if not is_blocked and soup_check:
                has_real_data = bool(
                    soup_check.find("span", class_="cardMainInfo__purchaseLink")
                    or soup_check.find("div", class_="cardMainInfo__section")
                )
                if not has_real_data and len(html) < 1000:
                    is_blocked = True
                    logger.warning(
                        f"   [v6.8.6] ⚠️ Пустой HTML (len={len(html)}), возможна блокировка"
                    )

        # Если заблокировано — возвращаем fallback
        if is_blocked or not html:
            logger.info(f"   [v6.8.6] Используем fallback на поисковые данные")
            return self._create_fallback_detail(
                reg_number=reg_number,
                law=law,
                title=fallback_title,
                region=fallback_region,
                customer=fallback_customer,
                nmck=nmck,
            )

        # Парсим HTML
        return self.parse(html=html, tender_id=reg_number, law=law)

    def _create_fallback_detail(
        self,
        reg_number: str,
        law: str,
        title: str,
        region: str,
        customer: str,
        nmck: float,
    ) -> TenderDetail:
        """Создаёт fallback TenderDetail из поисковых данных."""
        # v6.8.6: Определяем тип по названию
        tender_type_hint, type_source = self._detect_type_from_title(title)

        logger.info(
            f"   [v6.8.6] Fallback: type={tender_type_hint}, source={type_source}"
        )

        return TenderDetail(
            tender_id=reg_number,
            law=law,
            title=title,
            customer=customer or "",
            region=region or "",
            etp="",
            nmck=nmck or 0.0,
            publish_date="",
            deadline_date="",
            requirements="",
            platform_name=etp,
            warranty_required=False,
            warranty_percent=0.0,
            documents=[],
            raw_html="",
            notice_guid=None,
            tender_type_hint=tender_type_hint,
            type_detection_source=type_source,
            purchase_name=title,
            customer_name=customer or "",
            customer_region=region or "",
        )

    def _detect_type_from_title(self, title: str) -> tuple:
        if not title:
            return None, "empty_title"

        title_lower = title.lower()

        for ttype, keywords in TYPE_KEYWORDS.items():
            for keyword in keywords:
                # Проверяем точное вхождение
                if keyword.lower() in title_lower:
                    return ttype, f"title_keyword:{keyword}"

                # Проверяем базовую форму (без окончаний)
                base = keyword.lower().replace("ая ", "а ").replace("ой ", "о ")
                if base in title_lower:
                    return ttype, f"title_keyword_stem:{keyword}"

        return None, "undetermined"

    # ==================== ОСНОВНОЙ МЕТОД ПАРСИНГА ====================

    def parse(self, html: str, tender_id: str, law: str) -> Optional[TenderDetail]:
        """Парсит детальную информацию из HTML."""
        soup = BeautifulSoup(html, "html.parser")

        notice_guid = self._extract_notice_guid(soup, law)
        lot_info = self._parse_lot_list_223(soup, law) if law == "223-FZ" else None
        tender_type_hint, type_source = self._cascade_type_detection(
            soup, law, lot_info
        )

        title = self._extract_title(soup)
        customer = self._extract_customer(soup)
        region = self._extract_region(soup, law, lot_info)
        etp = self._extract_etp(soup)
        nmck = self._extract_nmck(soup)
        publish_date = self._extract_publish_date(soup)
        deadline_date = self._extract_deadline(soup)
        requirements = self._extract_requirements(soup)
        warranty_info = self._extract_warranty(soup)
        documents = self._extract_documents(soup)
        customer_address = self._extract_customer_address(soup, law)

        # v6.8.6: Извлекаем РМ из КТРУ
        rm_from_ktru = self._extract_rm_from_ktru(soup)

        # v6.8.6: Заполняем новые поля
        purchase_name = title
        customer_name = customer
        customer_region = region

        return TenderDetail(
            tender_id=tender_id,
            law=law,
            title=title,
            customer=customer,
            region=region,
            etp=etp,
            nmck=nmck,
            publish_date=publish_date,
            deadline_date=deadline_date,
            requirements=requirements,
            warranty_required=warranty_info["required"],
            warranty_percent=warranty_info["percent"],
            documents=documents,
            raw_html=html,
            notice_guid=notice_guid,
            tender_type_hint=tender_type_hint,
            lot_info=lot_info,
            customer_address=customer_address,
            type_detection_source=type_source,
            purchase_name=purchase_name,
            customer_name=customer_name,
            customer_region=customer_region,
            rm_total=rm_from_ktru,  # v6.8.6: РМ из КТРУ
        )

    # ==================== v6.8: НОВЫЕ МЕТОДЫ ====================

    def _extract_notice_guid(self, soup: BeautifulSoup, law: str) -> Optional[str]:
        """Извлекает noticeGuid из HTML для 223-ФЗ."""
        if law != "223-FZ":
            return None

        patterns = [
            r'noticeGuid[=:]\s*["\']' + r'([0-9a-fA-F\-]{36})' + r'["\']',
            r'purchaseNoticeGuid[=:]\s*["\']' + r'([0-9a-fA-F\-]{36})' + r'["\']',
            r'guid[=:]\s*["\']' + r'([0-9a-fA-F\-]{36})' + r'["\']',
        ]

        html_text = str(soup)
        for pattern in patterns:
            match = re.search(pattern, html_text)
            if match:
                guid = match.group(1)
                logger.info(f"    [noticeGuid] Извлечен из HTML: {guid}")
                return guid

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            match = re.search(r"noticeGuid=([0-9a-fA-F\-]{36})", href)
            if match:
                guid = match.group(1)
                logger.info(f"    [noticeGuid] Извлечен из ссылки: {guid}")
                return match.group(1)

        logger.warning("    [noticeGuid] Не найден в HTML 223-ФЗ")
        return None

    def _parse_lot_list_223(self, soup: BeautifulSoup, law: str) -> Optional[Dict]:
        """Парсит lot-list для 223-ФЗ: объект закупки, ОКПД2, ОКВЭД2."""
        if law != "223-FZ":
            return None

        lot_info = {
            "object_name": "",
            "okpd2": [],
            "okved2": [],
            "nmck": 0.0,
        }

        tables = soup.find_all("table", class_="table")
        for table in tables:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if any(
                "окпд2" in h or "оквэд2" in h or "наименование лота" in h
                for h in headers
            ):
                rows = table.find_all("tr")
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) >= 3:
                        obj_text = cells[0].get_text(separator=" ", strip=True)
                        if not obj_text:
                            obj_text = cells[1].get_text(separator=" ", strip=True)
                        lot_info["object_name"] = obj_text

                        for cell in cells:
                            text = cell.get_text(strip=True)
                            okpd_match = re.search(
                                r"(\d{2}\.\d{2}\.\d{2}(?:\.\d+)?)", text
                            )
                            if okpd_match:
                                lot_info["okpd2"].append(okpd_match.group(1))
                            okved_match = re.search(r"(\d{2}\.\d{2}(?:\.\d+)?)", text)
                            if (
                                okved_match
                                and okved_match.group(1) not in lot_info["okpd2"]
                            ):
                                lot_info["okved2"].append(okved_match.group(1))

                logger.info(
                    f"    [LotList223] Объект: {lot_info['object_name'][:80]}..."
                )
                logger.info(f"    [LotList223] ОКПД2: {lot_info['okpd2']}")
                return lot_info

        return None

    def _cascade_type_detection(
        self, soup: BeautifulSoup, law: str, lot_info: Optional[Dict]
    ) -> tuple:
        """Каскадное определение типа тендера. Возвращает (type_hint, source)."""
        text_sources = []

        if lot_info and lot_info.get("object_name"):
            text_sources.append((lot_info["object_name"], "lot_object"))

        if lot_info and lot_info.get("okpd2"):
            for okpd in lot_info["okpd2"]:
                for pattern, ttype in OKPD2_TO_TYPE.items():
                    if okpd.startswith(pattern):
                        logger.info(f"    [TypeDetect] ОКПД2 {okpd} -> {ttype}")
                        return ttype, "okpd2"

        title = self._extract_title(soup)
        if title:
            text_sources.append((title, "title"))

        obj_block = soup.find("div", class_="registry-entry__body-block")
        if obj_block:
            obj_title = obj_block.find("div", class_="registry-entry__body-title")
            if obj_title and "объект" in obj_title.get_text().lower():
                obj_value = obj_block.find("div", class_="registry-entry__body-value")
                if obj_value:
                    text_sources.append(
                        (obj_value.get_text(strip=True), "common_info_object")
                    )

        for text, source in text_sources:
            text_lower = text.lower()
            for ttype, keywords in TYPE_KEYWORDS.items():
                for keyword in keywords:
                    if keyword.lower() in text_lower:
                        logger.info(
                            f"    [TypeDetect] Ключевое слово '{keyword}' в {source} -> {ttype}"
                        )
                        return ttype, f"keyword:{source}"

        return None, "undetermined"

    def _extract_customer_address(self, soup: BeautifulSoup, law: str) -> Optional[str]:
        """Извлекает адрес заказчика из common-info (для 223-ФЗ)."""
        if law != "223-FZ":
            return None

        sections = soup.find_all("section", class_="common-text")
        for section in sections:
            caption = section.find("div", class_="common-text__caption")
            if caption and "заказчик" in caption.get_text().lower():
                rows = section.find_all("div", class_="row")
                for row in rows:
                    text = row.get_text(separator=" ", strip=True)
                    if "местонахождение" in text.lower() or "адрес" in text.lower():
                        address_match = re.search(
                            r"(?:местонахождение|адрес)[\s:]*(.+?)(?:|$)",
                            text,
                            re.IGNORECASE,
                        )
                        if address_match:
                            address = address_match.group(1).strip()
                            logger.info(f"    [CustomerAddress] {address[:80]}...")
                            return address

        return None

    def _extract_region_from_address(self, address: str) -> str:
        """Извлекает регион из адресной строки."""
        if not address:
            return ""

        address_lower = address.lower()

        for region in RUSSIAN_REGIONS:
            if region.lower() in address_lower:
                return region

        city_match = re.search(r"г\.?\s*([А-Яа-я\-]+)", address, re.IGNORECASE)
        if city_match:
            city = city_match.group(1).lower()
            if city in CITY_TO_REGION:
                return CITY_TO_REGION[city]

        parts = [p.strip() for p in address.split(",")]
        for part in parts:
            part_lower = part.lower()
            for region in RUSSIAN_REGIONS:
                if region.lower() in part_lower:
                    return region
            if "янао" in part_lower or "ямало-ненец" in part_lower:
                return "Ямало-Ненецкий АО"
            if "хмао" in part_lower or "ханты-манс" in part_lower:
                return "Ханты-Мансийский АО"
            if "чукот" in part_lower:
                return "Чукотский АО"
            if "ненец" in part_lower and "ямал" not in part_lower:
                return "Ненецкий АО"

        return ""

    # ==================== СУЩЕСТВУЮЩИЕ МЕТОДЫ ====================

    def _extract_region(
        self, soup: BeautifulSoup, law: str, lot_info: Optional[Dict]
    ) -> str:
        """Извлекает регион с учётом типа закона."""
        # v6.8.6: Сначала ищем явный блок "Регион" в контактной информации
        for section in soup.find_all("section", class_="blockInfo__section"):
            title_span = section.find("span", class_="section__title")
            if title_span and title_span.get_text(strip=True) == "Регион":
                content = section.find("span", class_="section__info")
                if content:
                    region = content.get_text(strip=True)
                    logger.info(f"    [Region] Извлечен из блока 'Регион': {region}")
                    return region

        # Для 223-ФЗ: из адреса заказчика
        if law == "223-FZ":
            customer_address = self._extract_customer_address(soup, law)
            if customer_address:
                region = self._extract_region_from_address(customer_address)
                if region:
                    logger.info(
                        f"    [Region223] Извлечен из адреса заказчика: {region}"
                    )
                    return region

            if lot_info and lot_info.get("object_name"):
                region = self._extract_region_from_address(lot_info["object_name"])
                if region:
                    logger.info(
                        f"    [Region223] Извлечен из объекта закупки: {region}"
                    )
                    return region

        # Fallback: старые селекторы
        region_elem = soup.find("span", string=re.compile("Регион", re.I))
        if region_elem:
            parent = region_elem.find_parent("div", class_="col-6")
            if parent:
                value = parent.find("span", class_="section__info")
                if value:
                    return value.get_text(strip=True)

        text = soup.get_text()
        region_patterns = [
            r"Регион\s*[:\-]\s*([^]+)",
            r"Место нахождения[^]*([^]+)",
        ]
        for pattern in region_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return ""

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Извлекает название тендера."""
        # v6.8.6: Пробуем несколько селекторов
        # 1. Ссылка с номером закупки
        title_elem = soup.find("span", class_="cardMainInfo__purchaseLink")
        if title_elem:
            return title_elem.get_text(strip=True)

        # 2. Объект закупки (для 44-ФЗ)
        obj_section = soup.find("div", class_="cardMainInfo__section")
        if obj_section:
            title_elem = obj_section.find("span", class_="cardMainInfo__content")
            if title_elem:
                return title_elem.get_text(strip=True)

        # 3. Из section "Наименование объекта закупки"
        for section in soup.find_all("section", class_="blockInfo__section"):
            title_span = section.find("span", class_="section__title")
            if title_span and "объект закупки" in title_span.get_text().lower():
                content = section.find("span", class_="section__info")
                if content:
                    return content.get_text(strip=True)

        # 4. Из таблицы КТРУ
        ktru_table = soup.find("div", id="purchaseObjectTruTable1")
        if ktru_table:
            name_cell = ktru_table.find("td", string=re.compile("Наименование товара"))
            if name_cell:
                next_cell = name_cell.find_next("td")
                if next_cell:
                    return next_cell.get_text(strip=True)

        return ""

    def _extract_customer(self, soup: BeautifulSoup) -> str:
        """Извлекает заказчика."""
        # v6.8.6: Ищем в контактной информации
        for section in soup.find_all("section", class_="blockInfo__section"):
            title_span = section.find("span", class_="section__title")
            if title_span and "организация, осуществляющая размещение" in title_span.get_text().lower():
                content = section.find("span", class_="section__info")
                if content:
                    # Убираем ссылки, оставляем только текст
                    return content.get_text(strip=True)

        # Fallback: старые селекторы
        customer_elem = soup.find("span", string=re.compile("Заказчик", re.I))
        if customer_elem:
            parent = customer_elem.find_parent("div", class_="col-6")
            if parent:
                value = parent.find("span", class_="section__info")
                if value:
                    return value.get_text(strip=True)

        customer_block = soup.find("div", class_="registry-entry__body-block")
        if customer_block:
            title = customer_block.find("div", class_="registry-entry__body-title")
            if title and "заказчик" in title.get_text().lower():
                value = customer_block.find("div", class_="registry-entry__body-value")
                if value:
                    return value.get_text(strip=True)

        return ""

    def _extract_etp(self, soup: BeautifulSoup) -> str:
        """Извлекает ЭТП."""
        # v6.8.6: Ищем в секции с "площадк" в заголовке
        for section in soup.find_all("section", class_="blockInfo__section"):
            title_span = section.find("span", class_="section__title")
            if title_span:
                title_text = title_span.get_text(strip=True).lower()
                # Проверяем часть слова "площадк" (площадка, площадки)
                if "площадк" in title_text and "электронн" in title_text:
                    content = section.find("span", class_="section__info")
                    if content:
                        etp = content.get_text(strip=True)
                        logger.info(f"    [ETP] Извлечен: {etp}")
                        return etp

        # Fallback: старые селекторы
        etp_elem = soup.find("span", string=re.compile("Электронная площадка", re.I))
        if etp_elem:
            parent = etp_elem.find_parent("div", class_="col-6")
            if parent:
                value = parent.find("span", class_="section__info")
                if value:
                    return value.get_text(strip=True)
        return ""
    def _extract_nmck(self, soup: BeautifulSoup) -> float:
        """Извлекает НМЦК."""
        nmck_elem = soup.find("span", class_="cardMainInfo__content_cost")
        if nmck_elem:
            text = nmck_elem.get_text(strip=True)
            cleaned = re.sub(r"[^\d.,]", "", text)
            cleaned = cleaned.replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return 0.0

    def _extract_publish_date(self, soup: BeautifulSoup) -> str:
        """Извлекает дату публикации."""
        date_elem = soup.find("span", string=re.compile("Размещено", re.I))
        if date_elem:
            parent = date_elem.find_parent("div", class_="col-6")
            if parent:
                value = parent.find("span", class_="section__info")
                if value:
                    return value.get_text(strip=True)
        return ""

    def _extract_deadline(self, soup: BeautifulSoup) -> str:
        """Извлекает дедлайн."""
        deadline_elem = soup.find(
            "span", string=re.compile("Окончание подачи заявок", re.I)
        )
        if deadline_elem:
            parent = deadline_elem.find_parent("div", class_="col-6")
            if parent:
                value = parent.find("span", class_="section__info")
                if value:
                    return value.get_text(strip=True)
        return ""

    def _extract_requirements(self, soup: BeautifulSoup) -> str:
        """Извлекает требования."""
        req_elem = soup.find("span", string=re.compile("Требования", re.I))
        if req_elem:
            parent = req_elem.find_parent("div", class_="col-12")
            if parent:
                value = parent.find("div", class_="")
                if value:
                    return value.get_text(strip=True)
        return ""

    def _extract_warranty(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Извлекает информацию об обеспечении."""
        warranty = {"required": False, "percent": 0.0}

        warranty_elem = soup.find("span", string=re.compile("Обеспечение заявки", re.I))
        if warranty_elem:
            parent = warranty_elem.find_parent("div", class_="col-6")
            if parent:
                value = parent.find("span", class_="section__info")
                if value:
                    text = value.get_text(strip=True).lower()
                    if "требуется" in text or "да" in text:
                        warranty["required"] = True
                        percent_match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
                        if percent_match:
                            warranty["percent"] = float(percent_match.group(1))

        return warranty

    def _extract_documents(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Извлекает список документов."""
        documents = []
        doc_tables = soup.find_all("table", class_="table")
        for table in doc_tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    name = cells[0].get_text(strip=True)
                    link_elem = cells[0].find("a", href=True)
                    link = link_elem.get("href") if link_elem else ""
                    if name and link:
                        documents.append({"name": name, "link": link})
        return documents

    def _extract_rm_from_ktru(self, soup: BeautifulSoup) -> int:
        """Извлекает количество РМ из таблицы КТРУ."""
        ktru_table = soup.find("div", id="purchaseObjectTruTable1")
        if not ktru_table:
            return 0

        # Ищем строку с единицей измерения "Рабочее место"
        for row in ktru_table.find_all("tr", class_="tableBlock__row"):
            cells = row.find_all("td", class_="tableBlock__col")
            for i, cell in enumerate(cells):
                if "рабочее место" in cell.get_text().lower():
                    # Количество обычно в соседней ячейке
                    if i + 1 < len(cells):
                        qty_text = cells[i + 1].get_text(strip=True)
                        qty_match = re.search(r"[\d\s]+", qty_text)
                        if qty_match:
                            try:
                                qty = int(qty_match.group().replace(" ", "").replace("&nbsp;", ""))
                                logger.info(f"    [KTRU] РМ из таблицы: {qty}")
                                return qty
                            except ValueError:
                                pass
        return 0
