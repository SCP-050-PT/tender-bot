"""
core/daily_limiter.py
Лимитер запусков + очистка файлов + кэш дубликатов.
v7.2.2
"""

import json
import time
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger


class DailyLimiter:
    """Контролирует лимиты, очистку файлов и кэш дубликатов."""

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent
        self.data_dir = self.base_dir / "data"
        self.downloads_dir = self.base_dir / "core" / "downloads"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.state_file = self.data_dir / "daily_limiter.json"
        self.cache_file = self.data_dir / "tender_cache_ids.json"

        # === ЛИМИТЫ ===
        self.MAX_PER_RUN = 10  # Максимум тендеров за запуск
        self.MAX_PER_DAY = 60  # Максимум тендеров в день
        self.MAX_PER_HOUR = 15  # Максимум тендеров в час

        # === ОЧИСТКА ===
        self.DOWNLOADS_MAX_AGE_DAYS = 1  # downloads/ — 1 день
        self.DATA_MAX_AGE_DAYS = 7  # data/ — 7 дней
        self.CACHE_TTL_DAYS = 3  # Кэш ID — 3 дня

        # Загрузка состояния
        self.state = self._load_state()
        self.cache = self._load_cache()

    # ================================================================
    # СОСТОЯНИЕ (счётчики)
    # ================================================================

    def _load_state(self) -> dict:
        default = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "hour": datetime.now().hour,
            "tenders_today": 0,
            "tenders_this_hour": 0,
            "last_run": None,
        }
        if not self.state_file.exists():
            return default
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Сброс при смене дня
            today = datetime.now().strftime("%Y-%m-%d")
            if state.get("date") != today:
                logger.info(
                    f"[DailyLimiter] Новый день {today}, сброс: "
                    f"{state.get('tenders_today', 0)} → 0"
                )
                state["date"] = today
                state["tenders_today"] = 0
                state["tenders_this_hour"] = 0
            # Сброс при смене часа
            if state.get("hour") != datetime.now().hour:
                state["hour"] = datetime.now().hour
                state["tenders_this_hour"] = 0
            return state
        except Exception as e:
            logger.error(f"[DailyLimiter] Ошибка загрузки: {e}")
            return default

    def _save_state(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[DailyLimiter] Ошибка сохранения: {e}")

    # ================================================================
    # КЭШ ДУБЛИКАТОВ
    # ================================================================

    def _load_cache(self) -> dict:
        """Загружает кэш ID тендеров с метками времени."""
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            # Удаляем устаревшие записи
            cutoff = (datetime.now() - timedelta(days=self.CACHE_TTL_DAYS)).isoformat()
            expired = [k for k, v in cache.items() if v < cutoff]
            if expired:
                for k in expired:
                    del cache[k]
                logger.info(
                    f"[DailyLimiter] Кэш: удалено {len(expired)} устаревших ID "
                    f"(TTL {self.CACHE_TTL_DAYS} дней), осталось {len(cache)}"
                )
                self._save_cache(cache)
            return cache
        except Exception as e:
            logger.error(f"[DailyLimiter] Ошибка загрузки кэша: {e}")
            return {}

    def _save_cache(self, cache: dict = None):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(cache or self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[DailyLimiter] Ошибка сохранения кэша: {e}")

    def is_cached(self, tender_id: str) -> bool:
        """Проверяет, есть ли тендер в кэше (уже обрабатывался)."""
        return tender_id in self.cache

    def add_to_cache(self, tender_id: str):
        """Добавляет тендер в кэш."""
        self.cache[tender_id] = datetime.now().isoformat()
        self._save_cache()

    def get_cache_size(self) -> int:
        return len(self.cache)

    # ================================================================
    # ПРОВЕРКА ЛИМИТОВ
    # ================================================================

    def can_run(self) -> tuple[bool, str]:
        """Проверяет, можно ли запустить агент."""
        self.state = self._load_state()

        if self.state["tenders_today"] >= self.MAX_PER_DAY:
            reason = f"Дневной лимит: {self.state['tenders_today']}/{self.MAX_PER_DAY}"
            logger.warning(f"[DailyLimiter] 🚫 {reason}")
            return False, reason

        if self.state["tenders_this_hour"] >= self.MAX_PER_HOUR:
            reason = (
                f"Часовой лимит: {self.state['tenders_this_hour']}/{self.MAX_PER_HOUR}"
            )
            logger.warning(f"[DailyLimiter] 🚫 {reason}")
            return False, reason

        return True, ""

    def record_tenders(self, count: int):
        """Записывает количество обработанных тендеров."""
        self.state = self._load_state()
        self.state["tenders_today"] += count
        self.state["tenders_this_hour"] += count
        self.state["last_run"] = datetime.now().isoformat()
        self._save_state()
        logger.info(
            f"[DailyLimiter] 📊 +{count} тендеров. "
            f"День: {self.state['tenders_today']}/{self.MAX_PER_DAY}, "
            f"Час: {self.state['tenders_this_hour']}/{self.MAX_PER_HOUR}, "
            f"Кэш: {self.get_cache_size()} ID"
        )

    def get_status(self) -> str:
        self.state = self._load_state()
        return (
            f"📊 Лимиты: "
            f"день {self.state['tenders_today']}/{self.MAX_PER_DAY}, "
            f"час {self.state['tenders_this_hour']}/{self.MAX_PER_HOUR}, "
            f"кэш {self.get_cache_size()} ID"
        )

    # ================================================================
    # ОЧИСТКА ФАЙЛОВ
    # ================================================================

    def cleanup_downloads(self):
        """Очищает папку downloads/ (файлы старше 1 дня)."""
        cutoff = time.time() - (self.DOWNLOADS_MAX_AGE_DAYS * 86400)
        deleted = 0
        freed_bytes = 0

        if not self.downloads_dir.exists():
            return

        for f in self.downloads_dir.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                freed_bytes += f.stat().st_size
                f.unlink()
                deleted += 1

        if deleted > 0:
            logger.info(
                f"[DailyLimiter] 🧹 Downloads: удалено {deleted} файлов, "
                f"освобождено {freed_bytes / 1024 / 1024:.1f} МБ"
            )

    def cleanup_data(self):
        """Очищает папку data/ (файлы старше 7 дней, кроме лимитера и кэша)."""
        cutoff = time.time() - (self.DATA_MAX_AGE_DAYS * 86400)
        deleted = 0
        freed_bytes = 0

        # Не удаляем служебные файлы
        protected = {"daily_limiter.json", "tender_cache_ids.json", "tender_cache.db"}

        if not self.data_dir.exists():
            return

        for f in self.data_dir.iterdir():
            if f.is_file() and f.name not in protected and f.stat().st_mtime < cutoff:
                freed_bytes += f.stat().st_size
                f.unlink()
                deleted += 1

        if deleted > 0:
            logger.info(
                f"[DailyLimiter] 🧹 Data: удалено {deleted} файлов, "
                f"освобождено {freed_bytes / 1024 / 1024:.1f} МБ"
            )

    def cleanup_all(self):
        """Полная очистка перед запуском."""
        self.cleanup_downloads()
        self.cleanup_data()
