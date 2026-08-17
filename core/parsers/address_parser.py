"""
Парсинг адресов: извлечение городов, регионов, расчёт выездов.
Вынесено из detailed_parser.py (v6.5).

Багфикс v6.6-r2:
  - Возвращает dict с regions_count (для calculator.py)
  - trips = regions_count (не cities_count)
  - Башкортостан=1 регион, не 9 городов
  - Улучшенная фильтрация административных слов
"""

import re
from typing import Dict, Any, List, Set
from loguru import logger

from knowledge.regions import RUSSIAN_REGIONS

class AddressParser:
    """Извлекает города, регионы и количество выездов из адресной строки."""

    # Расширенный список административных слов
    ADMIN_WORDS = {
        "республика", "область", "край", "автономный", "округ",
        "район", "муниципальный", "городской", "сельский",
        "поселение", "сельсовет", "территория", "автодорога",
        "километр", "здание", "строение", "корпус", "офис",
        "этаж", "комната", "ул.", "пр.", "пер.", "просп.",
        "б-р", "пл.", "ш.", "туп.", "наб.", "м.р-н",
    }

    # Ложные срабатывания
    FAKE_CITY_WORDS = [
        "поселение", "сельсовет", "муниципальный", "район",
        "территория", "автодорога", "километр", "здание",
        "строение", "корпус", "офис", "этаж", "комната",
        "участок", "квартал", "промышленная", "площадка",
        "база", "склад", "цех",
    ]

    # Паттерны городов
    CITY_PATTERNS = [
        r"г\.?\s*([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
        r"город\s+([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
        r"п\.?\s*([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
        r"пос(?:ёлок)?\.?\s*([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
        r"пгт\.?\s*([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
        r"с\.?\s*([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
        r"д\.?\s*([А-Я][а-я\-]+(?:\s+[А-Я][а-я\-]+)*)",
    ]

    def count_addresses(self, text: str, tender_type: str = "") -> Dict[str, Any]:
        """
        Возвращает детальную информацию об адресах.

        Багфикс v6.6-r2:
          - trips = regions_count (не cities_count)
          - Возвращает regions_count для calculator.py

        Returns:
            {
                'cities_count': int,        # Уникальные города
                'regions_count': int,       # Уникальные регионы
                'trips': int,               # Количество выездов = regions_count
                'unique_cities': List[str],
                'regions': List[str],
                'needs_manual_check': bool
            }
        """
        if not text:
            return {
                "cities_count": 0,
                "regions_count": 0,
                "trips": 1,
                "unique_cities": [],
                "regions": [],
                "needs_manual_check": False,
            }

        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

        # === Шаг 1: Проверяем нумерацию ===
        numbered_pattern = r"(?:^|\n)\s*\d+[\.\)\-]\s+"
        has_numbering = bool(re.search(numbered_pattern, text, re.MULTILINE))

        # === Шаг 2: Разбиваем на адреса ===
        if has_numbering:
            lines = re.split(r"(?:^|\n)\s*\d+[\.\)\-]\s+", text)
            lines = [l.strip() for l in lines if l.strip() and len(l.strip()) > 5]
        else:
            city_matches = []
            for pattern in self.CITY_PATTERNS[:2]:
                city_matches.extend(re.findall(pattern, text, re.IGNORECASE))

            if len(set(c.lower() for c in city_matches)) > 1:
                raw_lines = re.split(r"(?=г\.?\s+[А-Я])", text)
                lines = [l.strip() for l in raw_lines if l.strip() and len(l.strip()) > 10]
            else:
                lines = [text.strip()]

        # === Шаг 3: Извлекаем города и регионы ===
        cities_by_region: Dict[str, Set[str]] = {}
        current_region = None
        all_cities: Set[str] = set()
        all_regions: Set[str] = set()

        for line in lines:
            line_lower = line.lower()

            # Ищем регион
            region_match = re.search(
                r"(республика\s+[а-я\-]+|[а-я\-]+\s+(?:область|край|ао|автономный\s+округ))",
                line_lower,
            )
            if region_match:
                current_region = region_match.group(1).strip()
                all_regions.add(current_region)

            # Проверяем регионы из общего списка (для случаев без слова "область")
            for region_name in RUSSIAN_REGIONS:
                if region_name.lower() in line_lower:
                    all_regions.add(region_name)
                    if not current_region:
                        current_region = region_name

            # Ищем населённый пункт
            found_city = None
            for pattern in self.CITY_PATTERNS:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    found_city = match.group(1).strip()
                    break

            # Fallback: "п.Имя" без пробела
            if not found_city:
                match = re.search(r"[сдп]\.([А-Я][а-я\-]+)", line)
                if match:
                    found_city = match.group(1).strip()

            # Фильтрация
            if found_city and len(found_city) > 2:
                found_city_lower = found_city.lower()

                if any(admin in found_city_lower for admin in self.ADMIN_WORDS):
                    logger.debug(f"  [AddressParser] Пропущено (admin word): '{found_city}'")
                    continue

                if any(fake in found_city_lower for fake in self.FAKE_CITY_WORDS):
                    logger.debug(f"  [AddressParser] Пропущено (fake city): '{found_city}'")
                    continue

                # Фильтр: не похоже ли на район
                if any(suffix in found_city_lower for suffix in ["ский", "ской", "ный", "ной"]):
                    if " " not in found_city and len(found_city) > 8:
                        logger.debug(f"  [AddressParser] Пропущено (похоже на район): '{found_city}'")
                        continue

                region = current_region or "unknown"
                if region not in cities_by_region:
                    cities_by_region[region] = set()
                cities_by_region[region].add(found_city_lower)
                all_cities.add(found_city_lower)

        total_cities = len(all_cities)
        total_regions = max(1, len(all_regions))

        # === Шаг 4: Определяем выезды ===
        # БАГФИКС v6.6-r2: trips = regions_count (не cities_count)
        if total_cities <= 1:
            trips = 1
        else:
            trips = total_regions  # 1 выезд на регион, не на город

        needs_manual_check = total_cities > 5 or total_regions > 1

        logger.info(
            f"[AddressParser] Адресов: {len(lines)}, городов: {total_cities}, "
            f"регионов: {total_regions}, выездов: {trips}"
        )
        if total_regions > 1:
            logger.warning(
                f"[AddressParser] Несколько регионов — требуется ручная проверка выездов"
            )

        return {
            "cities_count": total_cities,
            "regions_count": total_regions,
            "trips": trips,
            "unique_cities": sorted(list(all_cities)),
            "regions": sorted(list(all_regions)),
            "needs_manual_check": needs_manual_check,
        }

    @staticmethod
    def extract_region(address: str) -> str:
        """Извлекает регион из адресной строки."""
        if not address:
            return ""

        address = re.sub(r"^\d{6},?\s*", "", address)

        address_lower = address.lower()
        for region in RUSSIAN_REGIONS:
            if region.lower() in address_lower:
                return region

        parts = address.split(",")
        if parts:
            first = parts[0].strip()
            if first:
                return first

        return ""
