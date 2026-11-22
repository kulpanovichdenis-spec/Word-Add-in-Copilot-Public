"""Слой-адаптер движков (ТЗ, раздел 7)."""

from .base import EngineError, EngineResult, EngineRunner, EngineStatus, RunControl
from .claude_code import ClaudeCodeRunner
from .codex import CodexRunner
from .manager import ENGINE_NAMES, EngineManager

__all__ = [
    "ENGINE_NAMES",
    "ClaudeCodeRunner",
    "CodexRunner",
    "EngineError",
    "EngineManager",
    "EngineResult",
    "EngineRunner",
    "EngineStatus",
    "RunControl",
]
