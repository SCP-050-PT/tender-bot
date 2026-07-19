"""
config/prompts.py
Загрузчик системного промпта для LLM.
"""

import os
from pathlib import Path
from loguru import logger


def load_system_prompt() -> str:
    """
    Загружает системный промпт из файла.
    Ищет по путям: config/promts/system_prompt.txt → config/prompts/system_prompt.txt
    """
    base_dir = Path(__file__).resolve().parent.parent

    # Возможные пути
    possible_paths = [
        base_dir / "config" / "promts" / "system_prompt.txt",
        base_dir / "config" / "prompts" / "system_prompt.txt",
        base_dir / "knowledge" / "system_prompt.txt",
    ]

    for path in possible_paths:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.info(f"✅ Системный промпт загружен: {path}")
            return content

    # Fallback — базовый промпт
    logger.warning("⚠️ Системный промпт не найден, используется fallback")
    return _get_fallback_prompt()


def _get_fallback_prompt() -> str:
    """Базовый промпт если файл не найден."""
    return """Ты — эксперт по тендерам в сфере охраны труда, СОУТ, ПЛК и обучения.
Проанализируй тендер и верни строгий JSON с параметрами для расчёта стоимости."""


# Для обратной совместимости с analyzer.py
def get_system_prompt() -> str:
    """Алиас для load_system_prompt()."""
    return load_system_prompt()
