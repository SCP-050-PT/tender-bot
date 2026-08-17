"""
core/parsers/ktru_parser.py
Парсинг КТРУ из common-info (44-ФЗ) и lot-list (223-ФЗ).

v6.8.6-r3-p2:
  - Добавлен parse_223_lot_list() для 223-ФЗ
"""

import re
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup
from loguru import logger


class KtruParser:
    """Извлекает количества из КТРУ (44-ФЗ) и lot-list (223-ФЗ)."""

    @staticmethod
    def parse(soup: BeautifulSoup) -> Dict[str, Any]:
        result = {
            "rm_total": None,
            "students_count": None,
            "points_count": None,
            "opr_positions": None,
            "unit_type": None,
            "price_per_unit": None,
            "ktru_confidence": 0.0,
        }

        table_container = soup.find("div", id="purchaseObjectTruTable1")
        if not table_container:
            table_container = soup.find("table", {"class": "blockInfo__table"})

        if not table_container:
            return result

        table = table_container.find("table", {"class": "blockInfo__table"})
        if not table:
            table = table_container if table_container.name == "table" else None

        if not table:
            return result

        rows = table.find_all("tr", {"class": "tableBlock__row"})
        total_qty = 0.0
        has_rm = has_person = has_point = has_position = False
        prices = []
        row_count = 0

        for row in rows:
            if "tableBlock__foot" in " ".join(row.get("class", [])):
                continue

            cols = row.find_all("td", {"class": "tableBlock__col"})
            if len(cols) < 5:
                continue

            unit_idx, qty_idx, price_idx = (3, 4, 5) if len(cols) >= 7 else (2, 3, 4)

            unit = (
                cols[unit_idx].get_text(strip=True).lower()
                if len(cols) > unit_idx
                else ""
            )
            qty_text = (
                cols[qty_idx].get_text(strip=True) if len(cols) > qty_idx else "0"
            )
            price_text = (
                cols[price_idx].get_text(strip=True) if len(cols) > price_idx else ""
            )

            qty_clean = (
                qty_text.replace(",", ".")
                .replace(" ", "")
                .replace("\xa0", "")
                .replace("\u00a0", "")
            )
            price_clean = (
                price_text.replace(",", ".")
                .replace(" ", "")
                .replace("\xa0", "")
                .replace("\u00a0", "")
                .replace("₽", "")
            )

            qty = KtruParser._parse_float(qty_clean)
            price = KtruParser._parse_float(price_clean)

            if "рабочее место" in unit or "раб место" in unit:
                has_rm = True
                if qty:
                    total_qty += qty
                    row_count += 1
                if price:
                    prices.append(price)

            elif "человек" in unit:
                has_person = True
                if qty:
                    total_qty += qty
                    row_count += 1

            elif "точка" in unit or "точек" in unit:
                has_point = True
                if qty:
                    total_qty += qty
                    row_count += 1

            elif "должность" in unit:
                has_position = True
                if qty:
                    total_qty += qty
                    row_count += 1

        if has_rm and total_qty > 0:
            result["rm_total"] = int(total_qty)
            result["unit_type"] = "rm"
            result["ktru_confidence"] = 1.0
            if prices:
                result["price_per_unit"] = sum(prices) / len(prices)
            logger.info(f"[KTRU] Найдено {result['rm_total']} РМ ({row_count} позиций)")

        elif has_person and total_qty > 0:
            result["students_count"] = int(total_qty)
            result["unit_type"] = "person"
            result["ktru_confidence"] = 1.0
            logger.info(
                f"[KTRU] Найдено {result['students_count']} слушателей ({row_count} позиций)"
            )

        elif has_point and total_qty > 0:
            result["points_count"] = int(total_qty)
            result["unit_type"] = "point"
            result["ktru_confidence"] = 1.0
            logger.info(
                f"[KTRU] Найдено {result['points_count']} точек ({row_count} позиций)"
            )

        elif has_position and total_qty > 0:
            result["opr_positions"] = int(total_qty)
            result["unit_type"] = "position"
            result["ktru_confidence"] = 1.0
            logger.info(
                f"[KTRU] Найдено {result['opr_positions']} должностей ({row_count} позиций)"
            )

        return result

    @staticmethod
    def parse_223_lot_list(soup: BeautifulSoup) -> Dict[str, Any]:
        result = {
            "rm_total": None,
            "students_count": None,
            "points_count": None,
            "opr_positions": None,
            "unit_type": None,
            "ktru_confidence": 0.0,
        }

        table = soup.find("table", {"class": "table"})
        if not table:
            logger.debug("[KTRU-223] Таблица лотов не найдена")
            return result

        rows = table.find_all("tr")
        total_qty = 0.0
        has_rm = has_person = has_point = has_position = False
        row_count = 0

        for row in rows:
            if row.find("th"):
                continue
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            lot_name = cols[0].get_text(strip=True).lower() if len(cols) > 0 else ""
            qty = 0

            rm_match = re.search(r"(\d+)\s*рабоч", lot_name)
            if rm_match:
                qty = int(rm_match.group(1))
                has_rm = True
                total_qty += qty
                row_count += 1
                continue

            person_match = re.search(r"(\d+)\s*(?:человек|слушател)", lot_name)
            if person_match:
                qty = int(person_match.group(1))
                has_person = True
                total_qty += qty
                row_count += 1
                continue

            point_match = re.search(r"(\d+)\s*(?:точ|замер)", lot_name)
            if point_match:
                qty = int(point_match.group(1))
                has_point = True
                total_qty += qty
                row_count += 1
                continue

            position_match = re.search(r"(\d+)\s*(?:должност|позиц)", lot_name)
            if position_match:
                qty = int(position_match.group(1))
                has_position = True
                total_qty += qty
                row_count += 1
                continue

        if has_rm and total_qty > 0:
            result["rm_total"] = int(total_qty)
            result["unit_type"] = "rm"
            result["ktru_confidence"] = 0.8
            logger.info(
                f"[KTRU-223] Найдено {result['rm_total']} РМ ({row_count} лотов)"
            )

        elif has_person and total_qty > 0:
            result["students_count"] = int(total_qty)
            result["unit_type"] = "person"
            result["ktru_confidence"] = 0.8
            logger.info(
                f"[KTRU-223] Найдено {result['students_count']} слушателей ({row_count} лотов)"
            )

        elif has_point and total_qty > 0:
            result["points_count"] = int(total_qty)
            result["unit_type"] = "point"
            result["ktru_confidence"] = 0.8
            logger.info(
                f"[KTRU-223] Найдено {result['points_count']} точек ({row_count} лотов)"
            )

        elif has_position and total_qty > 0:
            result["opr_positions"] = int(total_qty)
            result["unit_type"] = "position"
            result["ktru_confidence"] = 0.8
            logger.info(
                f"[KTRU-223] Найдено {result['opr_positions']} должностей ({row_count} лотов)"
            )

        return result

    @staticmethod
    def _parse_float(text: str) -> Optional[float]:
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
