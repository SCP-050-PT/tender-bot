"""
Regex-экстрактор параметров тендера.
Вынесено из param_extractor.py (v6.6-r2).
"""

import re
from typing import List, Tuple, Optional
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class RegexMatch:
    """Результат regex-извлечения."""
    field: str
    value: int
    weight: float
    pattern: str
    span: tuple = field(default_factory=tuple)


class RegexExtractor:
    """Извлекает параметры из текста через regex-паттерны."""

    # === ПАТТЕРНЫ С ВЕСАМИ ===
    # Формат: (regex, field_name, weight, max_value)

    RM_PATTERNS = [
        (r"(?:оценк[аеи]|спец[оа]ценк[аеи]|сout)[\s]+(\d+)[\s]+рабочих[\s]+мест", "rm_total", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]+рабочих[\s]+мест(?![\d\w])", "rm_total", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]+рабочее[\s]+место(?![\d\w])", "rm_total", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]+рабочих[\s]+места(?![\d\w])", "rm_total", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]+мест[\s]+оценки(?![\d\w])", "rm_total", 0.9, 50000),
        (r"(?:^[\s\(\[])[Рр][Мм][\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_total", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*[Рр][Мм](?![\d\w])", "rm_total", 0.8, 50000),
        (r"количество[\s]+[Рр][Мм][\s]*[\-—]?[\s]*(\d{1,5})", "rm_total", 0.9, 50000),
    ]

    RM_CATEGORY_PATTERNS = [
        (r"(?<![\d\w])(\d{1,5})[\s]*(?:рабочих[\s]+мест|РМ)[\s]+1[\s]*(?:категори|кat\.?)(?![\d\w])", "rm_category_1", 1.0, 50000),
        (r"1[\s]*(?:категори|кat\.?)[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_category_1", 0.9, 50000),
        (r"(?:категория|кat\.)[\s]*1[\s]*[\-—]?[\s]*(\d{1,5})[\s]*(?:РМ|рабочих)", "rm_category_1", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*(?:рабочих[\s]+мест|РМ)[\s]+2[\s]*(?:категори|кat\.?)(?![\d\w])", "rm_category_2", 1.0, 50000),
        (r"2[\s]*(?:категори|кat\.?)[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_category_2", 0.9, 50000),
        (r"1[\s]*(?:кат|категория)\.?[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_category_1", 0.8, 50000),
        (r"2[\s]*(?:кат|категория)\.?[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_category_2", 0.8, 50000),
    ]

    III_PATTERNS = [
        (r"(?<![\d\w])(\d{1,5})[\s]*(?:РМ|рабочих[\s]+мест)[\s]+(?:с[\s]+)?ИИИ(?![\d\w])", "rm_with_iii", 1.0, 50000),
        (r"ИИИ[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "rm_with_iii", 0.9, 50000),
        (r"ионизирующ[ие][\s]+излучен[ия][\s]*[\-—]?[\s]*(\d{1,5})", "rm_with_iii", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*(?:рентген|узи|рентгенолог|узист)(?![\d\w])", "rm_with_iii", 0.8, 50000),
    ]

    POINTS_PATTERNS = [
        (r"(?<![\d\w])(\d{1,5})[\s]*точек[\s]*замеров(?![\d\w])", "points_count", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*точек[\s]*контроля(?![\d\w])", "points_count", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*замерных[\s]*точек(?![\d\w])", "points_count", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*точек[\s]*ПЛК(?![\d\w])", "points_count", 0.9, 50000),
        (r"точ[еи][\s]*(?:замеров|контроля)[\s]*[\-—]?[\s]*(\d{1,5})(?![\d\w])", "points_count", 0.8, 50000),
    ]

    STUDENTS_PATTERNS = [
        (r"(?<![\d\w])(\d{1,5})[\s]*слушател[ейь](?![\d\w])", "students_count", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*человек(?![\d\w])", "students_count", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*участник[ов](?![\d\w])", "students_count", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*сотрудник[ов](?![\d\w])", "students_count", 0.8, 50000),
        (r"обучени[еяю][\s]+(\d{1,5})[\s]*слушател", "students_count", 0.9, 50000),
        (r"групп[аы][\s]*из[\s]*(\d{1,5})[\s]*(?:человек|слушател)", "students_count", 0.8, 50000),
    ]

    FACTORS_PATTERNS = [
        (r"(?<![\d\w])(\d{1,5})[\s]*вредных[\s]*факторов(?![\d\w])", "factors_count", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*факторов[\s]*вредности(?![\d\w])", "factors_count", 0.9, 50000),
    ]

    ADDRESSES_PATTERNS = [
        (r"(?<![\d\w])(\d{1,5})[\s]*адрес[аов](?![\d\w])", "addresses_count", 1.0, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*объект[аов](?![\d\w])", "addresses_count", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*площадок(?![\d\w])", "addresses_count", 0.9, 50000),
        (r"(?<![\d\w])(\d{1,5})[\s]*филиал[аов](?![\d\w])", "addresses_count", 0.8, 50000),
        (r"по[\s]+(\d{1,5})[\s]*адресам", "addresses_count", 0.8, 50000),
    ]

    DEADLINE_PATTERNS = [
        (r"в[\s]*течение[\s]*(\d{1,5})[\s]*(?:календарных|рабочих)?[\s]*дней", "deadline_days", 1.0, 1095),
        (r"срок[\s]*(?:исполнения|выполнения)[\s]*[\-—]?[\s]*(\d{1,5})[\s]*(?:календарных|рабочих|банковских)?[\s]*дней", "deadline_days", 1.0, 1095),
    ]

    OPR_POSITIONS_PATTERNS = [
        (r"(\d+)[\s]*должност[ейь]", "opr_positions", 1.0, 50000),
        (r"(\d+)[\s]*штатных[\s]*единиц", "opr_positions", 0.9, 50000),
        (r"штат[\s]*[\-—]?[\s]*(\d+)", "opr_positions", 0.8, 50000),
    ]

    OPR_PERSONS_PATTERNS = [
        (r"(?:численность|работников|персонала)[\s]*[\-—]?[\s]*(\d+)", "opr_persons", 1.0, 50000),
        (r"(\d+)[\s]*работников[\s]*(?:предприятия|организации)", "opr_persons", 0.9, 50000),
        (r"(\d+)[\s]*чел\.?[\s]*(?:персонала|сотрудников)", "opr_persons", 0.9, 50000),
    ]

    # Паттерны для булевых флагов
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
        (r"норм[ыы][\s]+СИЗ|норматив[ыы][\s]+СИЗ|средств[аы][\s]+индивидуальной[\s]*защиты", "needs_siz_norms"),
        (r"норм[ыы][\s]+ДСИЗ|дополнительные[\s]+СИЗ", "needs_dsiz_norms"),
        (r"ИОТ|инструкции[\s]+по[\s]+охране[\s]+труда", "needs_iot_norms"),
    ]

    SEASONAL_PATTERNS = [
        r"отопительный[\s]+сезон",
        r"сезонных[\s]+рабочих[\s]+мест",
        r"период[\s]+их[\s]+фактического[\s]+функционирования",
        r"в[\s]+период[\s]+[\w\s]+[\s]+сезона",
    ]
    
    GUARANTEE_PATTERNS = [
        (r"(?:обеспечение|требование)[\s]+(?:заявки|заявок)[\s]*[\-—]?\s*(.+?)(?:\n|$)", "application", 1.0, 500),
        (r"(?:обеспечение|требование)[\s]+(?:исполнения|контракта)[\s]*[\-—]?\s*(.+?)(?:\n|$)", "contract", 1.0, 500),
        (r"(?:способ|вид)[\s]+обеспечения[\s]*[\-—]?\s*(.+?)(?:\n|$)", "method", 1.0, 500),
        (r"не[\s]+требуется[\s]+обеспечение", "application", 1.0, 500),
        (r"не[\s]+требуется", "application", 0.9, 500),
    ]

    def extract_number(
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
                                logger.debug(f"[RegexExtractor] Пропущено {pat_field}={value} (похоже на ID)")
                                continue
                            logger.debug(f"[RegexExtractor] Найдено {pat_field}={value} (weight={weight})")
                            return value
                    except (ValueError, IndexError):
                        continue
            except re.error as e:
                logger.error(f'[RegexExtractor] Ошибка regex "{pattern}": {e}')
                continue
        return None

    def detect_full_time(self, text: str) -> bool:
        """Определяет, есть ли очная часть."""
        text_lower = text.lower()
        for pattern in self.FULL_TIME_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    def detect_polygon(self, text: str) -> bool:
        """Определяет, требуется ли полигон."""
        text_lower = text.lower()
        return "полигон" in text_lower or "практическая часть" in text_lower

    def detect_urgency(self, text: str) -> tuple[bool, Optional[int]]:
        """Определяет срочность."""
        text_lower = text.lower()
        for pattern, weight in self.URGENCY_PATTERNS:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                try:
                    days = int(match.group(1))
                    if days <= 14:
                        return True, days
                except (ValueError, IndexError):
                    continue
        urgent_keywords = [
            "срочно", "сжатые сроки", "в кратчайшие сроки",
            "не позднее 5 дней", "в течение недели",
        ]
        for kw in urgent_keywords:
            if kw in text_lower:
                return True, None
        return False, None

    def detect_norms(self, text: str, norm_type: str) -> bool:
        """Определяет, требуются ли нормы."""
        text_lower = text.lower()
        for pattern, nt in self.NORMS_PATTERNS:
            if nt == norm_type and re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False

    def detect_seasonal(self, text: str) -> bool:
        """Определяет сезонность."""
        text_lower = text.lower()
        for pattern in self.SEASONAL_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return True
        return False
