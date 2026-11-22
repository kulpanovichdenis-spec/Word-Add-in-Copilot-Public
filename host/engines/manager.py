"""Выбор движка (ТЗ 7.4): явная настройка → первый доступный → ошибка с инструкцией."""

from __future__ import annotations

import threading
import time
from typing import Callable

from ..config import ConfigStore, Settings
from .base import EngineError, EngineRunner, EngineStatus
from .claude_code import ClaudeCodeRunner
from .codex import CodexRunner
from .openrouter import OpenRouterRunner
from .discovery import find_claude, find_codex
from ..security.credentials import CredentialStore

ENGINE_NAMES = ("openrouter", "codex", "claude")
RunnerFactory = Callable[[str, Settings], EngineRunner]

NOT_FOUND_MESSAGE = "Не настроен OpenRouter и не найден Codex или Claude Code. Откройте настройки приложения"
LOCAL_TOOLS_NOT_FOUND_MESSAGE = "Для проверки DaData нужен Codex или Claude Code с доступом к локальным инструментам"


def default_factory(name: str, settings: Settings) -> EngineRunner:
    if name == "openrouter":
        return OpenRouterRunner(CredentialStore().get_openrouter_key, max_tokens=settings.openrouter_max_tokens)
    if name == "codex":
        return CodexRunner(find_codex(settings.codex_path))
    if name == "claude":
        return ClaudeCodeRunner(
            find_claude(settings.claude_path),
            read_tools=settings.claude_read_tools,
        )
    raise ValueError(f"Неизвестный движок: {name}")


class EngineManager:
    STATUS_TTL = 30.0

    def __init__(self, config: ConfigStore, factory: RunnerFactory = default_factory) -> None:
        self._config = config
        self._factory = factory
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._runners: dict[str, EngineRunner] = {}
        self._status: dict[str, tuple[float, EngineStatus]] = {}

    def runner(self, name: str) -> EngineRunner:
        s = self._config.settings
        key = (s.codex_path, s.claude_path, s.openrouter_max_tokens, tuple(s.claude_read_tools))
        with self._lock:
            if key != self._key:
                self._key, self._runners, self._status = key, {}, {}
            if name not in self._runners:
                self._runners[name] = self._factory(name, s)
            return self._runners[name]

    def status(self, name: str, *, fresh: bool = False) -> EngineStatus:
        runner = self.runner(name)
        now = time.monotonic()
        cached = self._status.get(name)
        if cached and not fresh and now - cached[0] < self.STATUS_TTL:
            return cached[1]
        status = runner.is_available()
        self._status[name] = (now, status)
        return status

    def statuses(self, *, fresh: bool = False) -> dict[str, EngineStatus]:
        return {name: self.status(name, fresh=fresh) for name in ENGINE_NAMES}

    def invalidate(self) -> None:
        with self._lock:
            self._status = {}

    def model_for(self, name: str) -> str:
        s = self._config.settings
        if name == "openrouter":
            return s.openrouter_model
        return s.codex_model if name == "codex" else s.claude_model

    def select(self) -> tuple[EngineRunner, EngineStatus]:
        preferred = self._config.settings.engine
        names = ENGINE_NAMES if preferred == "auto" else (preferred,)
        unauthenticated: EngineStatus | None = None
        for name in names:
            status = self.status(name)
            if not status.available:
                continue
            if status.authenticated is False:
                unauthenticated = unauthenticated or status
                continue
            return self.runner(name), status
        if unauthenticated is not None:
            if unauthenticated.name == "openrouter":
                raise EngineError("auth", "Токен OpenRouter не задан. Откройте настройки Word-Add-in-Copilot-Public")
            display = self.runner(unauthenticated.name).display_name
            raise EngineError("auth", f"Требуется вход в {display}. Выполните вход и повторите")
        if preferred != "auto":
            display = self.runner(preferred).display_name
            raise EngineError("not_found", f"Не найден {display}. Откройте настройки приложения")
        raise EngineError("not_found", NOT_FOUND_MESSAGE)

    def select_local_tools(self) -> tuple[EngineRunner, EngineStatus]:
        """Выбрать CLI-движок, способный вызвать локальный MCP; OpenRouter этого не умеет."""
        preferred = self._config.settings.engine
        names = (preferred,) if preferred in ("codex", "claude") else ("codex", "claude")
        unauthenticated: EngineStatus | None = None
        for name in names:
            status = self.status(name)
            if not status.available:
                continue
            if status.authenticated is False:
                unauthenticated = unauthenticated or status
                continue
            return self.runner(name), status
        if unauthenticated is not None:
            display = self.runner(unauthenticated.name).display_name
            raise EngineError("auth", f"Требуется вход в {display}. Выполните вход и повторите")
        raise EngineError("not_found", LOCAL_TOOLS_NOT_FOUND_MESSAGE)
