"""
core/param_extractor.py
Единое извлечение параметров из текста тендерных документов.
ИСПРАВЛЕНО (27.07.2026 v6.3):
  - Консолидирована логика из text_extractor.py, analyzer.py, detailed_parser.py
  - Все паттерны с весами в одном месте
  - Валидация диапазонов (1–50000, не ID тендера)
  - Авто-определение teacher_days, manikin_days
  - Единый формат результата
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class ExtractedParams:
    """Результат извлечения параметров из текста."""

    # Основные количества
    rm_total: Optional[int] = None
    rm_category_1: Optional[int] = None
    rm_category_2: Optional[int] = None
    rm_with_iii: Optional[int] = None
    points_count: Optional[int] = None
    students_count: Optional[int] = None
    factors_count: Optional[int] = None

    # Логистика
    addresses_count: Optional[int] = None
    cities_count: Optional[int] = None  # Уникальные города (для СОУТ)
    trip_days: Optional[int] = None

    # Сроки
    deadline_days: Optional[int] = None
    deadline_text: Optional[str] = None

    # Обеспечение
    application_guarantee: Optional[str] = None
    contract_guarantee: Optional[str] = None
    guarantee_method: Optional[str] = None

    # Очная часть обучения
    has_full_time: bool = False
    teacher_days: Optional[int] = None
    accommodation_nights: Optional[int] = None
    transport_km: Optional[int] = None
    venue_rent_days: Optional[int] = None
    manikin_days: Optional[int] = None

    # Полигон / срочность / сезонность
    has_polygon: bool = False
    is_urgent: bool = False
    urgency_days: Optional[int] = None
    is_seasonal: bool = False

    # Нормы
    needs_siz_norms: bool = False
    needs_dsiz_norms: bool = False
    needs_iot_norms: bool = False

    # ОПР
    opr_positions: Optional[int] = None
    opr_persons: Optional[int] = None

    # Confidence и метаданные
    confidence: float = 0.0
    raw_matches: List[Dict] = field(default_factory=list)

    # Источники
    rm_total_source: str = ""
    points_count_source: str = ""
    students_count_source: str = ""

    def to_dict(self) -> dict:
        """Сериализация в dict."""
        return {
            "rm_total": self.rm_total,
            "rm_category_1": self.rm_category_1,
            "rm_category_2": self.rm_category_2,
            "rm_with_iii": self.rm_with_iii,
            "points_count": self.points_count,
            "students_count": self.students_count,
            "factors_count": self.factors_count,
            "addresses_count": self.addresses_count,
            "cities_count": self.cities_count,
            "trip_days": self.trip_days,
            "deadline_days": self.deadline_days,
            "deadline_text": self.deadline_text,
            "has_full_time": self.has_full_time,
            "teacher_days": self.teacher_days,
            "accommodation_nights": self.accommodation_nights,
            "transport_km": self.transport_km,
            "venue_rent_days": self.venue_rent_days,
            "manikin_days": self.manikin_days,
            "is_urgent": self.is_urgent,
            "is_seasonal": self.is_seasonal,
            "opr_positions": self.opr_positions,
            "opr_persons": self.opr_persons,
            "confidence": self.confidence,
        }


class TenderParamExtractor:
    """
    Единый экстрактор параметров.
    Заменяет: text_extractor.TenderTextExtractor, analyzer._extract_params_from_text(),
    detailed_parser.EXTRACTION_PATTERNS.
    """

    # === ПАТТЕРНЫ С ВЕСАМИ ===
    # Формат: (regex, field_name, weight, max_value)

    RM_PATTERNS = [
        (
            r"(?:оценк[аеи]|спец[оа]ценк[аеи]|сout)[\s]+(\d+)[\s]+рабочих[\s]+мест",
            "rm_total",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]+рабочих[\s]+мест(?![\d\w])",
            "rm_total",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]+рабочее[\s]+место(?![\d\w])",
            "rm_total",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]+рабочих[\s]+места(?![\d\w])",
            "rm_total",
            1.0,
            50000,
        ),
        (r"(?<![\d\w])(\d{1,5})[\s]+мест[\s]+оценки(?![\d\w])", "rm_total", 0.9, 50000),
        (
            r"(?:^[\s\(\[])[Рр][Мм][\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])",
            "rm_total",
            0.9,
            50000,
        ),
        (r"(?<![\d\w])(\d{1,5})[\s]*[Рр][Мм](?![\d\w])", "rm_total", 0.8, 50000),
        (r"количество[\s]+[Рр][Мм][\s]*[\-—]?[\s]*(\d{1,5})", "rm_total", 0.9, 50000),
    ]

    RM_CATEGORY_PATTERNS = [
        (
            r"(?<![\d\w])(\d{1,5})[\s]*(?:рабочих[\s]+мест|РМ)[\s]+1[\s]*(?:категори|кat\.?)(?![\d\w])",
            "rm_category_1",
            1.0,
            50000,
        ),
        (
            r"1[\s]*(?:категори|кat\.?)[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])",
            "rm_category_1",
            0.9,
            50000,
        ),
        (
            r"(?:категория|кat\.)[\s]*1[\s]*[\-—]?[\s]*(\d{1,5})[\s]*(?:РМ|рабочих)",
            "rm_category_1",
            0.9,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*(?:рабочих[\s]+мест|РМ)[\s]+2[\s]*(?:категори|кat\.?)(?![\d\w])",
            "rm_category_2",
            1.0,
            50000,
        ),
        (
            r"2[\s]*(?:категори|кat\.?)[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])",
            "rm_category_2",
            0.9,
            50000,
        ),
        (
            r"1[\s]*(?:кат|категория)\.?[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])",
            "rm_category_1",
            0.8,
            50000,
        ),
        (
            r"2[\s]*(?:кат|категория)\.?[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])",
            "rm_category_2",
            0.8,
            50000,
        ),
    ]

    III_PATTERNS = [
        (
            r"(?<![\d\w])(\d{1,5})[\s]*(?:РМ|рабочих[\s]+мест)[\s]+(?:с[\s]+)?ИИИ(?![\d\w])",
            "rm_with_iii",
            1.0,
            50000,
        ),
        (r"ИИИ[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_with_iii", 0.9, 50000),
        (
            r"ионизирующ[ие][\s]+излучен[ия][\s]*[\-—]?[\s]*(\d{1,5})",
            "rm_with_iii",
            0.9,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*(?:рентген|узи|рентгенолог|узист)(?![\d\w])",
            "rm_with_iii",
            0.8,
            50000,
        ),
    ]

    POINTS_PATTERNS = [
        (
            r"(?<![\d\w])(\d{1,5})[\s]*точек[\s]*замеров(?![\d\w])",
            "points_count",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*точек[\s]*контроля(?![\d\w])",
            "points_count",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*замерных[\s]*точек(?![\d\w])",
            "points_count",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*точек[\s]*ПЛК(?![\d\w])",
            "points_count",
            0.9,
            50000,
        ),
        (
            r"точ[еи][\s]*(?:замеров|контроля)[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])",
            "points_count",
            0.8,
            50000,
        ),
    ]

    STUDENTS_PATTERNS = [
        (
            r"(?<![\d\w])(\d{1,5})[\s]*слушател[ейь](?![\d\w])",
            "students_count",
            1.0,
            50000,
        ),
        (r"(?<![\d\w])(\d{1,5})[\s]*человек(?![\d\w])", "students_count", 0.9, 50000),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*участник[ов](?![\d\w])",
            "students_count",
            0.9,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*сотрудник[ов](?![\d\w])",
            "students_count",
            0.8,
            50000,
        ),
        (r"обучени[еяю][\s]+(\d{1,5})[\s]*слушател", "students_count", 0.9, 50000),
        (
            r"групп[аы][\s]*из[\s]*(\d{1,5})[\s]*(?:человек|слушател)",
            "students_count",
            0.8,
            50000,
        ),
    ]

    FACTORS_PATTERNS = [
        (
            r"(?<![\d\w])(\d{1,5})[\s]*вредных[\s]*факторов(?![\d\w])",
            "factors_count",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*факторов[\s]*вредности(?![\d\w])",
            "factors_count",
            0.9,
            50000,
        ),
    ]

    ADDRESSES_PATTERNS = [
        (
            r"(?<![\d\w])(\d{1,5})[\s]*адрес[аов](?![\d\w])",
            "addresses_count",
            1.0,
            50000,
        ),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*объект[аов](?![\d\w])",
            "addresses_count",
            0.9,
            50000,
        ),
        (r"(?<![\d\w])(\d{1,5})[\s]*площадок(?![\d\w])", "addresses_count", 0.9, 50000),
        (
            r"(?<![\d\w])(\d{1,5})[\s]*филиал[аов](?![\d\w])",
            "addresses_count",
            0.8,
            50000,
        ),
        (r"по[\s]+(\d{1,5})[\s]*адресам", "addresses_count", 0.8, 50000),
    ]

    DEADLINE_PATTERNS = [
        (
            r"в[\s]*течение[\s]*(\d{1,5})[\s]*(?:календарных|рабочих)?[\s]*дней",
            "deadline_days",
            1.0,
            1095,
        ),
        (
            r"срок[\s]*(?:исполнения|выполнения)[\s]*[\-—]?[\s]*(\d{1,5})[\s]*(?:календарных|рабочих|банковских)?[\s]*дней",
            "deadline_days",
            1.0,
            1095,
        ),
        (
            r"не[\s]*позднее[\s]*[«]?(\d{1,2})[\s]*([а-я]+)[\s]*(\d{4})?",
            "deadline_date",
            0.9,
            None,
        ),
        (
            r"с[\s]*([а-я]+)[\s]*по[\s]*([а-я]+)[\s]*(\d{4})?",
            "deadline_period",
            0.8,
            None,
        ),
    ]

    GUARANTEE_PATTERNS = [
        (
            r"обеспечени[ея][\s]*заявки[\s]*[\-—]?[\s]*([^\n]{3,100})",
            "application_guarantee",
            1.0,
            None,
        ),
        (
            r"обеспечени[ея][\s]*(?:исполнения[\s]*)?контракта[\s]*[\-—]?[\s]*([^\n]{3,100})",
            "contract_guarantee",
            1.0,
            None,
        ),
        (
            r"гарантийный[\s]*взнос[\s]*[\-—]?[\s]*([^\n]{3,100})",
            "application_guarantee",
            0.8,
            None,
        ),
    ]

    FULL_TIME_PATTERNS = [
        r"очная[\s]+форма|очно[\s]*[-—]?[\s]*заочно|с[\s]+применением[\s]+дистанционных",
        r"полигон|практическ[аяоеуюй][\s]+часть|практик[аи]",
        r"манекен|практическ[ие][\s]+занятия|выезд[\s]+к[\s]+заказчику",
    ]

    URGENCY_PATTERNS = [
        (r"срок[\s]*(?:исполнения|поставки)[\s]*[\-—]?[\s]*(\d{1,5})[\s]*дней", 1.0),
        (r"не[\s]*позднее[\s]*(\d{1,5})[\s]*дней", 0.9),
    ]

    NORMS_PATTERNS = [
        (
            r"норм[ыы][\s]+СИЗ|норматив[ыы][\s]+СИЗ|средств[аы][\s]+индивидуальной[\s]*защиты",
            "needs_siz_norms",
        ),
        (r"норм[ыы][\s]+ДСИЗ|дополнительные[\s]+СИЗ", "needs_dsiz_norms"),
        (r"ИОТ|инструкции[\s]+по[\s]+охране[\s]+труда", "needs_iot_norms"),
    ]

    OPR_POSITIONS_PATTERNS = [
        (r"(\d+)[\s]*должност[ейь]", "opr_positions", 1.0, 50000),
        (r"(\d+)[\s]*штатных[\s]*единиц", "opr_positions", 0.9, 50000),
        (r"штат[\s]*[\-—]?[\s]*(\d+)", "opr_positions", 0.8, 50000),
    ]

    OPR_PERSONS_PATTERNS = [
        (
            r"(?:численность|работников|персонала)[\s]*[\-—]?[\s]*(\d+)",
            "opr_persons",
            1.0,
            50000,
        ),
        (
            r"(\d+)[\s]*работников[\s]*(?:предприятия|организации)",
            "opr_persons",
            0.9,
            50000,
        ),
        (r"(\d+)[\s]*чел\.?[\s]*(?:персонала|сотрудников)", "opr_persons", 0.9, 50000),
    ]

    SEASONAL_PATTERNS = [
        r"отопительный[\s]+сезон",
        r"сезонных[\s]+рабочих[\s]+мест",
        r"период[\s]+их[\s]+фактического[\s]+функционирования",
        r"в[\s]+период[\s]+[\w\s]+[\s]+сезона",
    ]

    # Месяцы для парсинга дат
    MONTHS_RU = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
        "январь": 1,
        "февраль": 2,
        "март": 3,
        "апрель": 4,
        "май": 5,
        "июнь": 6,
        "июль": 7,
        "август": 8,
        "сентябрь": 9,
        "октябрь": 10,
        "ноябрь": 11,
        "декабрь": 12,
    }

    def __init__(self):
        logger.info("TenderParamExtractor инициализирован (v6.3)")

    def extract(
        self, text: str, nmck: float = 0, tender_type_hint: str = None
    ) -> ExtractedParams:
        """Извлекает все параметры из текста."""
        if not text or len(text) < 50:
            logger.warning("Текст слишком короткий для извлечения")
            return ExtractedParams(confidence=0.0)

        params = ExtractedParams()
        text_lower = text.lower()

        # === Количества ===
        params.rm_total = self._extract_number(text, self.RM_PATTERNS)
        params.rm_total_source = "regex" if params.rm_total is not None else ""

        params.rm_category_1 = self._extract_number(
            text, self.RM_CATEGORY_PATTERNS, field="rm_category_1"
        )
        params.rm_category_2 = self._extract_number(
            text, self.RM_CATEGORY_PATTERNS, field="rm_category_2"
        )
        params.rm_with_iii = self._extract_number(text, self.III_PATTERNS)
        params.points_count = self._extract_number(text, self.POINTS_PATTERNS)
        params.points_count_source = "regex" if params.points_count is not None else ""
        params.students_count = self._extract_number(text, self.STUDENTS_PATTERNS)
        params.students_count_source = (
            "regex" if params.students_count is not None else ""
        )
        params.factors_count = self._extract_number(text, self.FACTORS_PATTERNS)
        params.addresses_count = self._extract_number(text, self.ADDRESSES_PATTERNS)

        # === ОПР-параметры ===
        params.opr_positions = self._extract_number(text, self.OPR_POSITIONS_PATTERNS)
        params.opr_persons = self._extract_number(text, self.OPR_PERSONS_PATTERNS)

        # === Сроки ===
        params.deadline_days = self._extract_number(text, self.DEADLINE_PATTERNS)
        deadline_date = self._extract_deadline_date(text)
        if deadline_date:
            params.deadline_text = deadline_date

        # === Trip days ===
        trip_match = re.search(
            r"(?:срок|длительность)[\s]*(?:выезда|командировки)[\s]*[\-—]?[\s]*(\d+)[\s]*дн",
            text_lower,
        )
        if trip_match:
            params.trip_days = int(trip_match.group(1))
            logger.debug(f"Найдено trip_days={params.trip_days} из текста")
        else:
            params.trip_days = 3  # дефолт

        # === Обеспечение ===
        params.application_guarantee = self._extract_guarantee(text, "application")
        params.contract_guarantee = self._extract_guarantee(text, "contract")
        params.guarantee_method = self._extract_guarantee_method(text)

        # === Регион ===
        params.region_hint = self._extract_region(text)

        # === Очная часть / полигон ===
        params.has_full_time = self._detect_full_time(text)
        params.has_polygon = self._detect_polygon(text)

        # === Очные параметры ===
        params.teacher_days = self._extract_teacher_days(text)
        params.accommodation_nights = self._extract_accommodation_nights(text)
        params.transport_km = self._extract_transport_km(text)
        params.venue_rent_days = self._extract_venue_rent_days(text)
        params.manikin_days = self._extract_manikin_days(text, tender_type_hint)

        # === Срочность ===
        params.is_urgent, params.urgency_days = self._detect_urgency(text)

        # === Нормы ===
        params.needs_siz_norms = self._detect_norms(text, "needs_siz_norms")
        params.needs_dsiz_norms = self._detect_norms(text, "needs_dsiz_norms")
        params.needs_iot_norms = self._detect_norms(text, "needs_iot_norms")

        # === Сезонность ===
        params.is_seasonal = self._detect_seasonal(text)

        # === Confidence ===
        params.confidence = self._calculate_confidence(params)

        logger.info(
            f"Извлечено: РМ={params.rm_total}(src={params.rm_total_source}), "
            f"кат.1={params.rm_category_1}, кат.2={params.rm_category_2}, "
            f"ИИИ={params.rm_with_iii}, точек={params.points_count}, слушателей={params.students_count}, "
            f"срок={params.deadline_days}д, trip_days={params.trip_days}, "
            f"teacher_days={params.teacher_days}, manikin_days={params.manikin_days}, "
            f"сезон={params.is_seasonal}, opr_pos={params.opr_positions}, opr_per={params.opr_persons}, "
            f"confidence={params.confidence:.2f}"
        )

        return params

    def _extract_number(
        self, text: str, patterns: List[Tuple], field: str = None
    ) -> Optional[int]:
        """Извлекает число по списку regex-паттернов."""
        for pattern, pat_field, weight, max_val in patterns:
            if field and pat_field != field:
                continue
            try:
                matches = re.finditer(pattern, text, re.IGNORECASE)
                for match in matches:
                    try:
                        value = int(match.group(1))
                        # Валидация диапазона
                        if 1 <= value <= (max_val or 50000):
                            # Проверка: не похоже ли на ID тендера?
                            if len(match.group(1)) >= 14:
                                logger.debug(
                                    f"Пропущено {pat_field}={value} (похоже на ID)"
                                )
                                continue
                            logger.debug(
                                f"Найдено {pat_field}={value} (weight={weight})"
                            )
                            return value
                    except (ValueError, IndexError):
                        continue
            except re.error as e:
                logger.error(f'Ошибка regex "{pattern}": {e}')
                continue
        return None

    def _extract_deadline_date(self, text: str) -> Optional[str]:
        """Извлекает дату deadline из текста."""
        text_lower = text.lower()

        # "не позднее 15 октября 2026"
        match = re.search(
            r"не[\s]*позднее[\s]*[«]?(\d{1,2})[\s]*([а-я]+)[\s]*(\d{4})?", text_lower
        )
        if match:
            day, month_str, year = match.groups()
            month = self.MONTHS_RU.get(month_str.lower(), 0)
            if month > 0:
                year_str = year if year else "2026"
                return f"{day}.{month:02d}.{year_str}"

        # "срок оказания услуг: 15 октября 2026"
        match = re.search(
            r"срок[\s]*(?:оказания|выполнения)[\s]*(?:услуг|работ)[\s]*[\-—]?[\s]*[«]?(\d{1,2})[\s]*([а-я]+)[\s]*(\d{4})?",
            text_lower,
        )
        if match:
            day, month_str, year = match.groups()
            month = self.MONTHS_RU.get(month_str.lower(), 0)
            if month > 0:
                year_str = year if year else "2026"
                return f"{day}.{month:02d}.{year_str}"

        return None

    def _extract_teacher_days(self, text: str) -> Optional[int]:
        """Извлекает дни преподавателя."""
        text_lower = text.lower()
        match = re.search(
            r"преподавател[ья]\s*(?:работ[аеы]\s*)?(\d+)\s*дн", text_lower
        )
        if match:
            return int(match.group(1))
        match = re.search(
            r"учебн[ыо][\s]*дн[ея][\s]*(?:для[\s]+преподавателя)?\s*[\-—]?\s*(\d+)",
            text_lower,
        )
        if match:
            return int(match.group(1))
        return None

    def _extract_accommodation_nights(self, text: str) -> Optional[int]:
        """Извлекает ночи проживания."""
        text_lower = text.lower()
        match = re.search(
            r"проживани[ея]\s*(?:в[\s]+гостинице)?\s*(\d+)\s*ноч", text_lower
        )
        if match:
            return int(match.group(1))
        match = re.search(r"ночей[\s]*проживани[ея]\s*[\-—]?\s*(\d+)", text_lower)
        if match:
            return int(match.group(1))
        return None

    def _extract_transport_km(self, text: str) -> Optional[int]:
        """Извлекает расстояние в км."""
        text_lower = text.lower()
        match = re.search(r"расстояни[ея]\s*[\-—]?\s*(\d+)\s*км", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d+)\s*км[\s]*(?:от[\s]+|до[\s]+)", text_lower)
        if match:
            return int(match.group(1))
        return None

    def _extract_venue_rent_days(self, text: str) -> Optional[int]:
        """Извлекает дни аренды помещения."""
        text_lower = text.lower()
        match = re.search(r"аренд[аы][\s]*помещени[ея]\s*(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(
            r"учебн[ыо][\s]*помещени[ея]\s*[\-—]?\s*(\d+)\s*дн", text_lower
        )
        if match:
            return int(match.group(1))
        return None

    def _extract_manikin_days(
        self, text: str, tender_type_hint: str = None
    ) -> Optional[int]:
        """Извлекает дни манекена. Авто-определение по тексту."""
        text_lower = text.lower()
        match = re.search(r"манекен[аовы]\s*(?:на[\s]+)?(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        match = re.search(r"тренаж[её]р[аовы]\s*(?:на[\s]+)?(\d+)\s*дн", text_lower)
        if match:
            return int(match.group(1))
        # Авто-определение
        if "первая помощь" in text_lower or "манекен" in text_lower:
            return 1
        return None

    def _extract_guarantee(self, text: str, guarantee_type: str) -> Optional[str]:
        """Извлекает текст обеспечения."""
        patterns = [p for p in self.GUARANTEE_PATTERNS if p[1] == guarantee_type]
        for pattern, field, weight, _ in patterns:
            try:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    value = match.group(1).strip()
                    value = re.sub(r"\s+", " ", value)
                    value = value[:200]
                    if len(value) > 5:
                        return value
            except re.error as e:
                logger.error(f'Ошибка regex "{pattern}": {e}')
                continue
        return None

    def _extract_guarantee_method(self, text: str) -> Optional[str]:
        """Определяет способ обеспечения."""
        text_lower = text.lower()
        if "банковская гарантия" in text_lower or "банковск" in text_lower:
            return "БГ"
        elif "депозит" in text_lower or "зачислен" in text_lower:
            return "депозит"
        elif "тариф" in text_lower:
            return "тариф"
        elif "не требуется" in text_lower or "обеспечение не" in text_lower:
            return "не требуется"
        return None

    def _detect_full_time(self, text: str) -> bool:
        """Определяет, есть ли очная часть."""
        text_lower = text.lower()
        for pattern in self.FULL_TIME_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    def _detect_polygon(self, text: str) -> bool:
        """Определяет, требуется ли полигон."""
        text_lower = text.lower()
        return "полигон" in text_lower or "практическая часть" in text_lower

    def _detect_urgency(self, text: str) -> Tuple[bool, Optional[int]]:
        """Определяет срочность."""
        text_lower = text.lower()
        for pattern, weight in self.URGENCY_PATTERNS:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                try:
                    days = int(match.group(1))
                    if days <= 5:
                        return True, days
                    elif days <= 14:
                        return True, days
                except (ValueError, IndexError):
                    continue
        urgent_keywords = [
            "срочно",
            "сжатые сроки",
            "в кратчайшие сроки",
            "не позднее 5 дней",
            "в течение недели",
        ]
        for kw in urgent_keywords:
            if kw in text_lower:
                return True, None
        return False, None

    def _detect_norms(self, text: str, norm_type: str) -> bool:
        """Определяет, требуются ли нормы."""
        text_lower = text.lower()
        for pattern, nt in self.NORMS_PATTERNS:
            if nt == norm_type and re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    def _detect_seasonal(self, text: str) -> bool:
        """Определяет сезонность."""
        text_lower = text.lower()
        for pattern in self.SEASONAL_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    def _extract_region(self, text: str) -> Optional[str]:
        """Извлекает регион из текста."""
        regions = [
            "Москва",
            "Санкт-Петербург",
            "Севастополь",
            "Московская",
            "Ленинградская",
            "Свердловская",
            "Нижегородская",
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
            "Новокузнецк",
        ]
        text_lower = text.lower()
        for region in regions:
            if region.lower() in text_lower:
                return region
        return None

    def _calculate_confidence(self, params: ExtractedParams) -> float:
        """Рассчитывает общую уверенность."""
        score = 0.0
        max_score = 0.0

        if params.rm_total is not None:
            score += 0.3
        max_score += 0.3

        if params.rm_category_1 is not None or params.rm_category_2 is not None:
            score += 0.2
        max_score += 0.2

        if params.points_count is not None:
            score += 0.3
        max_score += 0.3

        if params.students_count is not None:
            score += 0.3
        max_score += 0.3

        if params.deadline_days is not None:
            score += 0.1
        max_score += 0.1

        if params.application_guarantee is not None:
            score += 0.05
        max_score += 0.05

        if params.region_hint is not None:
            score += 0.05
        max_score += 0.05

        if max_score == 0:
            return 0.0

        return min(1.0, score / max_score)

    def build_enriched_prompt(
        self,
        params: ExtractedParams,
        original_text: str,
        nmck: float = 0,
        tender_type_hint: str = None,
    ) -> str:
        """Строит обогащённый промпт для LLM."""
        lines = [
            "Проанализируй текст закупки и подтверди или скорректируй параметры.",
            "",
            "=== НАЙДЕНО В ТЕКСТЕ (проверь и подтверди) ===",
        ]

        fields = [
            ("Рабочих мест (РМ)", params.rm_total),
            ("РМ 1 категории", params.rm_category_1),
            ("РМ 2 категории", params.rm_category_2),
            ("РМ с ИИИ", params.rm_with_iii),
            ("Точек замеров (ПЛК)", params.points_count),
            ("Слушателей", params.students_count),
            ("Вредных факторов", params.factors_count),
            ("Адресов/объектов", params.addresses_count),
            ("Дней выезда", params.trip_days),
            ("Должностей ОПР", params.opr_positions),
            ("Человек ОПР", params.opr_persons),
            ("Срок исполнения", params.deadline_days, "дней"),
            ("Дата окончания", params.deadline_text),
            ("Дней преподавателя", params.teacher_days),
            ("Ночей проживания", params.accommodation_nights),
            ("Расстояние", params.transport_km, "км"),
            ("Дней аренды", params.venue_rent_days),
            ("Дней манекена", params.manikin_days),
        ]

        for field_info in fields:
            label = field_info[0]
            value = field_info[1]
            suffix = field_info[2] if len(field_info) > 2 else ""
            if value is not None:
                lines.append(f"- {label}: {value}{suffix}")

        flags = []
        if params.has_full_time:
            flags.append("⚠️ Обнаружена очная часть / полигон")
        if params.is_urgent:
            flags.append(f"⚠️ Срочный тендер (до {params.urgency_days or 'N/A'} дней)")
        if params.is_seasonal:
            flags.append("⚠️ Сезонность (отопительный сезон / сезонные РМ)")
        if params.needs_siz_norms:
            flags.append("- Требуются нормы СИЗ")
        if params.needs_dsiz_norms:
            flags.append("- Требуются нормы ДСИЗ")
        if params.needs_iot_norms:
            flags.append("- Требуются ИОТ")

        if flags:
            lines.extend(["", "=== ФЛАГИ ==="])
            lines.extend(flags)

        if params.application_guarantee:
            lines.append(f"- Обеспечение заявки: {params.application_guarantee}")
        if params.contract_guarantee:
            lines.append(f"- Обеспечение контракта: {params.contract_guarantee}")

        lines.extend(
            [
                "",
                "=== ЗАДАЧА ===",
                "1. Подтверди найденные значения или укажи правильные",
                "2. Если значение не найдено в тексте — верни 0 (не придумывай)",
                "3. Определи тип тендера: sout|education|plk|opr|combined",
                "4. Извлеки обеспечение заявки и контракта",
                "5. Оцени сроки исполнения в днях",
                "6. Укажи категории РМ (1 и 2) если есть",
                "7. Укажи РМ с ИИИ если есть",
                "8. Укажи addresses_count и trip_days для СОУТ",
                "9. Укажи opr_positions и opr_persons для combined",
                "10. Укажи is_seasonal если есть сезонность",
                "",
                "=== ТЕКСТ ДОКУМЕНТОВ ===",
                original_text[:12000],
            ]
        )

        return "\n".join(lines)

    def merge_with_llm_result(
        self, extracted: ExtractedParams, llm_result: dict, llm_confidence: float = 0.0
    ) -> dict:
        """
        Объединяет извлечённые параметры с результатом LLM.
        При llm_confidence < 0.3 приоритет у extracted.
        """
        if not llm_result or not isinstance(llm_result, dict):
            return {}

        merged = dict(llm_result)
        low_confidence = llm_confidence < 0.3

        # Валидация РМ
        llm_rm = merged.get("rm_total", 0)
        extracted_rm = extracted.rm_total

        if llm_rm > 0:
            if low_confidence and llm_rm > 200:
                logger.warning(
                    f"merge: LLM rm_total={llm_rm} отклонён (confidence={llm_confidence:.2f} < 0.3, >200)"
                )
                merged["rm_total"] = extracted_rm or 0
                merged["rm_total_source"] = "extracted" if extracted_rm else "unknown"
            elif low_confidence and extracted_rm is not None and extracted_rm > 0:
                ratio = max(llm_rm, extracted_rm) / min(llm_rm, extracted_rm)
                if ratio > 3:
                    logger.warning(
                        f"merge: LLM rm_total={llm_rm} → extracted={extracted_rm} (confidence={llm_confidence:.2f}, ratio={ratio:.1f})"
                    )
                    merged["rm_total"] = extracted_rm
                    merged["rm_total_source"] = "extracted"
                else:
                    merged["rm_total_source"] = "llm_low_confidence"
            else:
                merged["rm_total_source"] = "llm"
        else:
            if extracted_rm is not None:
                merged["rm_total"] = extracted_rm
                merged["rm_total_source"] = "extracted"

        # Остальные поля: при низком confidence — приоритет extracted
        fields = [
            ("rm_category_1", extracted.rm_category_1),
            ("rm_category_2", extracted.rm_category_2),
            ("rm_with_iii", extracted.rm_with_iii),
            ("points_count", extracted.points_count),
            ("students_count", extracted.students_count),
            ("deadline_days", extracted.deadline_days),
            ("trip_days", extracted.trip_days),
            ("opr_positions", extracted.opr_positions),
            ("opr_persons", extracted.opr_persons),
        ]

        for field_name, extracted_value in fields:
            if low_confidence and extracted_value is not None:
                merged[field_name] = extracted_value
                merged[f"{field_name}_source"] = "extracted"
            elif merged.get(field_name, 0) == 0 and extracted_value is not None:
                merged[field_name] = extracted_value
                merged[f"{field_name}_source"] = "extracted"

        # Булевы флаги
        if not merged.get("is_seasonal") and extracted.is_seasonal:
            merged["is_seasonal"] = True
        if not merged.get("needs_siz_norms") and extracted.needs_siz_norms:
            merged["needs_siz_norms"] = True
        if not merged.get("needs_dsiz_norms") and extracted.needs_dsiz_norms:
            merged["needs_dsiz_norms"] = True
        if not merged.get("needs_iot_norms") and extracted.needs_iot_norms:
            merged["needs_iot_norms"] = True

        merged["llm_confidence"] = llm_confidence
        return merged
