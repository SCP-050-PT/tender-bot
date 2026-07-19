"""
config/settings.py
Главный конфигурационный файл. Читает .env, предоставляет доступ ко всем настройкам.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корня проекта
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Единая точка доступа ко всем настройкам бота."""

    # === YANDEX GPT ===
    YANDEX_FOLDER_ID: str = os.getenv("YANDEX_FOLDER_ID", "")
    YANDEX_API_KEY: str = os.getenv("YANDEX_API_KEY", "")
    YANDEX_GPT_MODEL: str = os.getenv("YANDEX_GPT_MODEL", "yandexgpt-lite")

    # === GOOGLE SHEETS ===
    GOOGLE_SHEETS_ID: str = os.getenv(
        "GOOGLE_SHEETS_ID", "1taImEQire-tOjGT85xKglsTQ4PH9cvzryqaxARPKAk8"
    )
    GOOGLE_SHEETS_CREDENTIALS_PATH: str = os.getenv(
        "GOOGLE_SHEETS_CREDENTIALS_PATH", "./config/credentials.json"
    )

    # === TENDER SEARCH ===
    SEARCH_INTERVAL_HOURS: int = int(os.getenv("SEARCH_INTERVAL_HOURS", "4"))

    # === APP ===
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"

    # === BUSINESS CONSTANTS (из калькуляторов) ===
    MIN_CONTRACT_SUM: int = 10_000  # Минимальная сумма договора
    MIN_NMCK: int = 100_000  # Минимальная НМЦК для поиска
    MIN_MARGIN_PERCENT: float = 10.0  # Минимальная маржа (%)
    MIN_MARGIN_SIZ: float = 5.0  # Минимальная маржа для СИЗ (%)

    # Себестоимость документов (обучение)
    COST_CERTIFICATE: int = 60  # Корочка (удостоверение)
    COST_DIPLOMA: int = 265  # Диплом специалиста
    COST_CERT_WORKER: int = 80  # Свидетельство рабочему
    COST_CERT_QUALIFICATION: int = 130  # Свидетельство повышения квалификации

    # Себестоимость СОУТ (за 1 РМ)
    COST_SOUT_RM_BASE: float = 213.0  # Базовая себестоимость 1 РМ
    COST_SOUT_RM_HIGH: float = 337.5  # Высокая себестоимость 1 РМ

    # Себестоимость ПЛК (за 1 точку)
    COST_PLK_POINT: float = 41.9

    # Транспортные нормы
    FUEL_CONSUMPTION_L_PER_100KM: float = 11.0
    FUEL_PRICE_PER_LITER: float = 55.0  # Актуализировать при необходимости
    ACCOMMODATION_PER_NIGHT: int = 2500
    DAILY_ALLOWANCE: int = 500  # Суточные замерщика
    EXPERT_DAILY_RATE: int = 5000  # Ставка эксперта/замерщика за день

    # Субподряд ИИИ (рентген, УЗИ)
    SUBCONTRACTOR_III_RANGES = {
        "1-10": 5000,
        "11-15": 6000,
        "16-20": 7000,
    }

    # Обеспечение заявки / контракта (БГ)
    BG_COST_RANGES = [
        (50_000, 1_000),
        (100_000, 1_200),
        (500_000, 2_000),
        (1_000_000, 4_000),
        (5_000_000, 10_000),
    ]

    # === VALIDATION ===
    @classmethod
    def validate(cls) -> list[str]:
        """Проверяет, что все критичные настройки заполнены."""
        errors = []
        if not cls.YANDEX_FOLDER_ID:
            errors.append("YANDEX_FOLDER_ID не задан")
        if not cls.YANDEX_API_KEY:
            errors.append("YANDEX_API_KEY не задан")
        if not cls.GOOGLE_SHEETS_ID:
            errors.append("GOOGLE_SHEETS_ID не задан")
        return errors


# Глобальный инстанс для импорта
settings = Settings()
