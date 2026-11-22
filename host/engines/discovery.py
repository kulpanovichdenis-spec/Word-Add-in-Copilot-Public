"""Поиск исполняемых файлов движков (ТЗ 10.2, п. 2)."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


def _existing(path: str) -> list[str] | None:
    return [path] if path and Path(path).is_file() else None


def find_codex(override: str = "") -> list[str] | None:
    if override:
        return _existing(override)
    found = shutil.which("codex")
    if found:
        return [found]
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    candidates = sorted(base.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(candidates[0])] if candidates else None


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", path.parent.name)) or (0,)


def find_claude(override: str = "") -> list[str] | None:
    if override:
        return _existing(override)
    found = shutil.which("claude")
    if found:
        return [found]
    # Настольное приложение Claude несёт полноценный CLI в %APPDATA%\Claude\claude-code\<версия>\.
    # Это не GUI-лаунчер из %LOCALAPPDATA%\AnthropicClaude — он отвечает на --help и работает в -p.
    base = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"
    candidates = sorted(base.glob("*/claude.exe"), key=_version_key, reverse=True)
    return [str(candidates[0])] if candidates else None
