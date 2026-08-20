#!/usr/bin/env python3
"""
scheduler.py
Планировщик запуска ИИ-агента каждые 4 часа.
Запуск: python scheduler.py
Остановка: Ctrl+C
"""

import subprocess
import sys
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

# === НАСТРОЙКИ ===
INTERVAL_HOURS = 4  # Интервал между запусками
MAX_RESULTS = 10  # Максимум тендеров за запуск
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Логирование планировщика
logger.add(
    LOG_DIR / "scheduler.log",
    rotation="10 MB",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}",
)
logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
)

# Глобальный флаг для graceful shutdown
running = True


def signal_handler(signum, frame):
    global running
    logger.info(f"🛑 Получен сигнал {signum}, завершение...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def run_agent():
    """Запускает ИИ-агента и возвращает код возврата."""
    logger.info("=" * 60)
    logger.info("🚀 Запуск ИИ-агента...")
    logger.info("=" * 60)

    start_time = datetime.now()

    # v7.2.2: Проверка лимитов ДО запуска (вне try — если импорт упадёт, не запускаем)
    try:
        from core.daily_limiter import DailyLimiter

        limiter = DailyLimiter()
        can_run, reason = limiter.can_run()
        if not can_run:
            logger.info(f"⏭️  Пропуск запуска: {reason}")
            return 0
    except ImportError as e:
        logger.error(f"❌ Не удалось импортировать DailyLimiter: {e}")
        logger.error("   Запуск без проверки лимитов!")
        # Продолжаем без лимитов — лучше запустить, чем пропустить

    try:
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "--analyze",
                "--max-results",
                str(MAX_RESULTS),
            ],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=600,  # 10 минут максимум
            encoding="utf-8",
            errors="replace",
        )

        elapsed = datetime.now() - start_time

        if result.returncode == 0:
            logger.info(f"✅ Агент завершён успешно за {elapsed.total_seconds():.0f}с")
        else:
            logger.error(f"❌ Агент завершился с кодом {result.returncode}")
            if result.stderr:
                logger.error(f"STDERR: ...{result.stderr[-500:]}")

        # Сохраняем stdout в отдельный лог
        if result.stdout:
            run_log = LOG_DIR / f"run_{start_time.strftime('%Y%m%d_%H%M%S')}.log"
            with open(run_log, "w", encoding="utf-8") as f:
                f.write(result.stdout)
            logger.info(f"💾 Лог запуска сохранён: {run_log.name}")

        return result.returncode

    except subprocess.TimeoutExpired:
        elapsed = datetime.now() - start_time
        logger.error(
            f"⏰ Таймаут! Агент не завершился за {elapsed.total_seconds():.0f}с"
        )
        return -1

    except Exception as e:
        logger.error(f"💥 Ошибка запуска агента: {e}")
        return -1


def main():
    global running

    logger.info("🤖 Планировщик ИИ-агента запущен")
    logger.info(f"⏱️  Интервал: каждые {INTERVAL_HOURS} часов")
    logger.info(f"📊 Максимум тендеров за запуск: {MAX_RESULTS}")
    logger.info(f"🛑 Остановка: Ctrl+C")

    # Первый запуск — сразу
    next_run = datetime.now()

    while running:
        now = datetime.now()

        if now >= next_run:
            run_agent()
            next_run = now + timedelta(hours=INTERVAL_HOURS)
            logger.info(
                f"⏭️  Следующий запуск: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        # Спим 60 секунд между проверками
        for _ in range(60):
            if not running:
                break
            time.sleep(1)

    logger.info("👋 Планировщик остановлен")


if __name__ == "__main__":
    main()
