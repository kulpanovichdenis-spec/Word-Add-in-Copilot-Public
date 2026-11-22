"""Настройки хоста: путь к папке legal-copilot, движок, модели и таймауты."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


log = logging.getLogger(__name__)

APP_NAME = "WordAddinCopilotPublic"
DEFAULT_PORT = 45889

# Инструменты, которые Claude Code может вызывать без запроса (режим dontAsk, ТЗ 11.6).
CLAUDE_READ_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Skill",
]
class Settings(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True
    )

    project_path: str = ""
    engine: Literal["auto", "openrouter", "codex", "claude"] = "auto"
    codex_path: str = ""
    claude_path: str = ""
    codex_model: str = ""
    claude_model: str = ""
    openrouter_model: str = "openrouter/auto"
    sharepoint_templates_url: str = Field("", max_length=4000)
    openrouter_max_tokens: int = Field(16_000, ge=256, le=128_000)
    short_timeout: int = Field(120, ge=1, le=600)
    long_timeout: int = Field(3600, ge=60, le=6 * 3600)
    max_parallel: int = Field(2, ge=1, le=8)
    max_selection_chars: int = Field(30_000, ge=100)
    max_document_chars: int = Field(500_000, ge=1_000)
    max_related_document_chars: int = Field(200_000, ge=1_000, le=2_000_000)
    max_related_file_bytes: int = Field(25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    max_related_files: int = Field(12, ge=1, le=50)
    port: int = Field(DEFAULT_PORT, ge=1024, le=65535)
    tls_cert: str = ""
    tls_key: str = ""
    actions_path: str = ""
    claude_read_tools: list[str] = Field(default_factory=lambda: list(CLAUDE_READ_TOOLS))

    def public(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


PUBLIC_KEYS = frozenset(Settings().public())


def app_home() -> Path:
    """Каталог данных приложения: config.json и logs/."""
    env = os.environ.get("WORD_ADDIN_COPILOT_PUBLIC_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def machine_home() -> Path:
    """Общий для всех пользователей каталог (ProgramData): сюда установщик кладёт TLS-сертификат."""
    env = os.environ.get("WORD_ADDIN_COPILOT_PUBLIC_MACHINE_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / APP_NAME


def tls_dir() -> Path:
    """Сертификат от установщика (ProgramData), иначе — выпущенный вручную `python -m host cert`."""
    machine = machine_home() / "tls"
    return machine if (machine / "localhost.pem").is_file() else app_home() / "tls"


def repo_root() -> Path:
    """Корень установки: рядом лежат actions.yaml и addin/."""
    return Path(__file__).resolve().parent.parent


class ConfigStore:
    """Потокобезопасное хранилище настроек в JSON-файле."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_home() / "config.json"
        self._lock = threading.Lock()
        self._settings = self._load()

    @property
    def settings(self) -> Settings:
        return self._settings

    def update(self, patch: dict[str, Any]) -> Settings:
        unknown = set(patch) - PUBLIC_KEYS
        if unknown:
            raise ValueError(f"Неизвестные параметры: {', '.join(sorted(unknown))}")
        with self._lock:
            data = self._settings.public() | patch
            new = Settings.model_validate(data)
            self._save(new)
            self._settings = new
            return new

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            return Settings.model_validate(json.loads(self.path.read_text("utf-8")))
        except (OSError, ValueError) as exc:
            log.warning("config.json не прочитан (%s), используются значения по умолчанию", type(exc).__name__)
            return Settings()

    def _save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings.public(), ensure_ascii=False, indent=2), "utf-8")
        for attempt in range(10):
            try:
                tmp.replace(self.path)
                return
            except PermissionError:
                # Windows: антивирус или индексатор ненадолго держит только что записанный файл.
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
