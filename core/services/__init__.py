"""
Единые сервисы TENDER-BOT.
Устраняют дублирование логики между analyzer.py, llm_wrapper.py, param_extractor.py.
"""

from core.services.type_service import TypeService
from core.services.llm_service import LlmService
from core.services.fallback_service import FallbackService

__all__ = ["TypeService", "LlmService", "FallbackService"]
