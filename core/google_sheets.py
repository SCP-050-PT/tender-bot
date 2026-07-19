"""
core/google_sheets.py
Работа с Google Sheets. Чтение, запись, проверка дубликатов, форматирование.
"""

import json
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass
from loguru import logger

try:
    import gspread
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("gspread не установлен. Google Sheets недоступен.")

from config.settings import settings

# Стандартные колонки таблицы заказчицы
SHEET_COLUMNS = [
    "Наименование услуг",
    "Количество",
    "Способ проведения закупки",
    "НМЦК",
    "Ссылка на тендер",
    "ЭТП",
    "Регион",
    "Обеспечение заявки",
    "Обеспечение контракта",
    "Срок подачи заявки до",
    "Решение по участию",
    "Цена предложения",
    "Результат",
    "Комментарии руководителя отдела по участию",
]


@dataclass
class TenderRecord:
    """Запись о тендере в таблице."""

    row_number: int
    tender_id: Optional[str]
    service_name: str
    nmck: float
    decision: str
    price: float
    comment: str

    def is_duplicate_of(self, tender_id: str) -> bool:
        """Проверяет, является ли запись дубликатом."""
        return self.tender_id == tender_id


class GoogleSheetsManager:
    """
    Менеджер для работы с Google Sheets.
    """

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(
        self,
        spreadsheet_id: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ):
        self.spreadsheet_id = spreadsheet_id or settings.GOOGLE_SHEETS_ID
        self.credentials_path = (
            credentials_path or settings.GOOGLE_SHEETS_CREDENTIALS_PATH
        )
        self.client = None
        self.sheet = None
        self.worksheet = None

        if not GOOGLE_AVAILABLE:
            raise ImportError(
                "gspread не установлен. Установите: pip install gspread google-auth"
            )

        self._connect()

    def _connect(self):
        """Устанавливает соединение с Google Sheets."""
        try:
            creds_path = Path(self.credentials_path)
            if not creds_path.exists():
                logger.error(f"Файл credentials не найден: {self.credentials_path}")
                raise FileNotFoundError(
                    f"Credentials not found: {self.credentials_path}"
                )

            credentials = Credentials.from_service_account_file(
                str(creds_path), scopes=self.SCOPES
            )

            self.client = gspread.authorize(credentials)
            self.sheet = self.client.open_by_key(self.spreadsheet_id)
            self.worksheet = self.sheet.sheet1  # Первый лист (gid=0)

            logger.info(f"Подключено к таблице: {self.spreadsheet_id}")

        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            raise

    def get_all_records(self) -> List[Dict]:
        """Получает все записи из таблицы."""
        try:
            records = self.worksheet.get_all_records()
            logger.info(f"Получено {len(records)} записей из таблицы")
            return records
        except Exception as e:
            logger.error(f"Ошибка чтения таблицы: {e}")
            return []

    def find_duplicate(self, tender_id: str) -> Optional[int]:
        """
        Ищет дубликат тендера по ID.

        Returns:
            int: Номер строки (1-based) или None
        """
        try:
            # Ищем в колонке "Ссылка на тендер" или первой колонке
            all_values = self.worksheet.get_all_values()

            for i, row in enumerate(all_values[1:], start=2):  # Пропускаем заголовок
                # Проверяем, содержит ли строка ID тендера
                row_text = " ".join(row)
                if tender_id in row_text:
                    logger.info(f"Найден дубликат тендера {tender_id} в строке {i}")
                    return i

            return None

        except Exception as e:
            logger.error(f"Ошибка поиска дубликата: {e}")
            return None

    def add_tender(self, data: Dict, check_duplicate: bool = True) -> bool:
        """
        Добавляет тендер в таблицу.

        Args:
            data: Словарь с данными тендера (соответствует SHEET_COLUMNS)
            check_duplicate: Проверять ли дубликаты

        Returns:
            bool: Успешно ли добавлено
        """
        try:
            tender_id = data.get("Ссылка на тендер", "")

            # Проверка дубликата
            if check_duplicate and tender_id:
                existing_row = self.find_duplicate(tender_id)
                if existing_row:
                    logger.info(
                        f"Тендер {tender_id} уже есть в таблице (строка {existing_row})"
                    )
                    return False

            # Формируем строку в правильном порядке
            row = [data.get(col, "") for col in SHEET_COLUMNS]

            # Добавляем в конец
            self.worksheet.append_row(row, value_input_option="USER_ENTERED")

            # Форматирование по решению
            decision = data.get("Решение по участию", "")
            if decision == "не участвуем":
                self._format_row_red(len(self.worksheet.get_all_values()))
            elif decision == "рекомендуется":
                self._format_row_green(len(self.worksheet.get_all_values()))

            logger.info(
                f"Тендер добавлен в таблицу: {data.get('Наименование услуг', 'N/A')}"
            )
            return True

        except Exception as e:
            logger.error(f"Ошибка добавления тендера: {e}")
            return False

    def add_tender_to_top(self, data: Dict, check_duplicate: bool = True) -> bool:
        """
        Добавляет тендер в начало таблицы (новые сверху).
        """
        try:
            tender_id = data.get("Ссылка на тендер", "")

            if check_duplicate and tender_id:
                existing_row = self.find_duplicate(tender_id)
                if existing_row:
                    logger.info(f"Тендер {tender_id} уже есть в таблице")
                    return False

            # Получаем текущие данные
            all_values = self.worksheet.get_all_values()

            # Формируем новую строку
            row = [data.get(col, "") for col in SHEET_COLUMNS]

            # Вставляем после заголовка (строка 2)
            self.worksheet.insert_row(row, index=2, value_input_option="USER_ENTERED")

            # Форматирование
            decision = data.get("Решение по участию", "")
            if decision == "не участвуем":
                self._format_row_red(2)
            elif decision == "рекомендуется":
                self._format_row_green(2)

            logger.info(f"Тендер добавлен в начало таблицы")
            return True

        except Exception as e:
            logger.error(f"Ошибка добавления: {e}")
            return False

    def update_tender(self, row_number: int, data: Dict) -> bool:
        """Обновляет существующую строку."""
        try:
            row = [data.get(col, "") for col in SHEET_COLUMNS]
            self.worksheet.update(f"A{row_number}:N{row_number}", [row])
            logger.info(f"Строка {row_number} обновлена")
            return True
        except Exception as e:
            logger.error(f"Ошибка обновления: {e}")
            return False

    def _format_row_red(self, row_number: int):
        """Закрашивает строку красным (высокий риск / отказ)."""
        try:
            self.worksheet.format(
                f"A{row_number}:N{row_number}",
                {"backgroundColor": {"red": 0.95, "green": 0.8, "blue": 0.8}},
            )
        except Exception as e:
            logger.warning(f"Не удалось применить форматирование: {e}")

    def _format_row_green(self, row_number: int):
        """Закрашивает строку зелёным (рекомендуется)."""
        try:
            self.worksheet.format(
                f"A{row_number}:N{row_number}",
                {"backgroundColor": {"red": 0.8, "green": 0.95, "blue": 0.8}},
            )
        except Exception as e:
            logger.warning(f"Не удалось применить форматирование: {e}")

    def _format_row_yellow(self, row_number: int):
        """Закрашивает строку жёлтым (средний риск)."""
        try:
            self.worksheet.format(
                f"A{row_number}:N{row_number}",
                {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}},
            )
        except Exception as e:
            logger.warning(f"Не удалось применить форматирование: {e}")

    def get_last_row_number(self) -> int:
        """Возвращает номер последней заполненной строки."""
        try:
            return len(self.worksheet.get_all_values())
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return 1


# Глобальный инстанс (ленивая инициализация)
_sheets_manager: Optional[GoogleSheetsManager] = None


def get_sheets_manager() -> GoogleSheetsManager:
    """Возвращает менеджер Google Sheets (singleton)."""
    global _sheets_manager
    if _sheets_manager is None:
        _sheets_manager = GoogleSheetsManager()
    return _sheets_manager
