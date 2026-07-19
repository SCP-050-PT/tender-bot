# config/prompts/__init__.py
from pathlib import Path


def load_system_prompt() -> str:
    """Загружает системный промпт из файла."""
    prompt_path = Path(__file__).parent / "system_prompt.txt"
    return prompt_path.read_text(encoding="utf-8")


__all__ = ["load_system_prompt"]
