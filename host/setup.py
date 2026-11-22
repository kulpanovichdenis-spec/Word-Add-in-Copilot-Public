"""Первый запуск без установки чужих папок и без своих команд установки.

Приложение не клонирует legal-copilot и не запускает npm. Если CLI нет или
не выполнен вход — показывает команду. Если порт панели занят — берёт следующий
свободный и переписывает манифест, который Word уже зарегистрировал.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .engines.base import EngineError, EngineStatus
from .engines.manager import ENGINE_NAMES, EngineManager

CODEX_MISSING = (
    "Codex не найден. Установите Codex CLI и выполните вход командой: codex login. "
    "Приложение само ничего не устанавливает."
)
CODEX_AUTH = "Требуется вход в Codex. В командной строке выполните: codex login"
CLAUDE_MISSING = (
    "Claude Code не найден. Установите CLI командой: npm i -g @anthropic-ai/claude-code — "
    "затем выполните вход: claude auth login. Приложение само ничего не устанавливает."
)
CLAUDE_AUTH = "Требуется вход в Claude Code. В командной строке выполните: claude auth login"
OPENROUTER_AUTH = "Задайте токен OpenRouter в окне настроек Word-Add-in-Copilot-Public"

PROBE_PROMPT = "Ответь ровно одним словом: готово. Не вызывай инструменты и не читай файлы."
PROBE_TIMEOUT = 90

ADDIN_ID = "aa15c7aa-5190-47b1-8246-1f04218337ce"
DEVELOPER_KEY = r"Software\Microsoft\Office\16.0\WEF\Developer"
PORT_SPAN = 20

_HINTS = {
    "openrouter": ("", OPENROUTER_AUTH),
    "codex": (CODEX_MISSING, CODEX_AUTH),
    "claude": (CLAUDE_MISSING, CLAUDE_AUTH),
}


class PortBusy(Exception):
    """В окне рядом с предпочитаемым портом нет свободного."""


def hints_for(statuses: dict[str, EngineStatus], engine: str) -> str:
    """Пустая строка, если выбранный движок может работать. Иначе — что сделать юристу."""
    names = ENGINE_NAMES if engine == "auto" else (engine,)
    relevant = [statuses[name] for name in names if name in statuses]
    if any(status.available and status.authenticated is not False for status in relevant):
        return ""
    lines = []
    for status in relevant:
        missing, auth = _HINTS.get(status.name, ("", ""))
        if not status.available and missing:
            lines.append(missing)
        elif status.authenticated is False and auth:
            lines.append(auth)
    return "\n".join(lines)


def hints_from_payload(engines: dict, engine_setting: str) -> str:
    statuses = {
        name: EngineStatus(
            name,
            bool(info.get("available")),
            authenticated=info.get("authenticated"),
            error=str(info.get("error") or ""),
        )
        for name, info in engines.items()
    }
    return hints_for(statuses, engine_setting)


def check_engine(manager: EngineManager, cwd: Path, *, timeout: int = PROBE_TIMEOUT) -> str:
    """Короткий запрос без текста договора. Ответ — как есть, чтобы было видно, что движок жив."""
    runner, _status = manager.select()
    result = runner.run(
        PROBE_PROMPT,
        cwd,
        None,
        timeout,
        lambda _message: None,
        writable=False,
        model=manager.model_for(runner.name),
    )
    text = result.text.strip()
    if not text:
        raise EngineError("failed", "Движок не вернул ответ")
    return text


def rewrite_manifest_port(xml: str, port: int) -> str:
    """Меняет порт у https://localhost и https://127.0.0.1. Остальные адреса не трогает."""
    import re

    return re.sub(
        r"https://(localhost|127\.0\.0\.1):\d+",
        lambda match: f"https://{match.group(1)}:{port}",
        xml,
    )


def choose_port(preferred: int, is_busy, *, span: int = PORT_SPAN) -> int:
    last = min(preferred + span, 65536)
    for port in range(preferred, last):
        if not is_busy(port):
            return port
    raise PortBusy(f"Порт {preferred} занят, свободный рядом не найден")


def port_moved_message(old: int, new: int, *, manifest_updated: bool, copied: bool) -> str:
    text = (
        f"Порт {old} занят другой программой. Панель слушает {new}. "
        "Перезапустите Word, чтобы кнопка открыла новый адрес."
    )
    if not manifest_updated:
        text += " Файл манифеста Word не переписан."
    elif copied:
        text += " Для текущего пользователя записана копия манифеста с новым портом."
    return text


@dataclass(frozen=True)
class SwitchResult:
    port: int
    moved: bool
    manifest: Path | None
    registry_update: str | None
    warning: str


def publish_manifest(source: Path, xml: str, fallback_dir: Path) -> Path:
    """Пишет манифест на место. Если файл защищён — кладёт копию в каталог приложения."""
    try:
        source.write_text(xml, encoding="utf-8")
        return source
    except OSError:
        fallback_dir.mkdir(parents=True, exist_ok=True)
        dest = fallback_dir / "manifest.xml"
        dest.write_text(xml, encoding="utf-8")
        return dest


def switch_port(
    preferred: int,
    is_busy,
    *,
    manifest_path: Path | None,
    fallback_dir: Path,
    span: int = PORT_SPAN,
) -> SwitchResult:
    port = choose_port(preferred, is_busy, span=span)
    if port == preferred:
        return SwitchResult(port, False, manifest_path, None, "")
    published: Path | None = None
    copied = False
    if manifest_path is not None and manifest_path.is_file():
        xml = rewrite_manifest_port(manifest_path.read_text(encoding="utf-8"), port)
        published = publish_manifest(manifest_path, xml, fallback_dir)
        copied = published != manifest_path
    return SwitchResult(
        port=port,
        moved=True,
        manifest=published,
        registry_update=str(published) if copied and published is not None else None,
        warning=port_moved_message(preferred, port, manifest_updated=published is not None, copied=copied),
    )


def read_registered_manifest() -> Path | None:
    """Путь из HKCU\\...\\WEF\\Developer — туда MSI записывает манифест надстройки."""
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DEVELOPER_KEY) as key:
            value, _typ = winreg.QueryValueEx(key, ADDIN_ID)
    except OSError:
        return None
    path = Path(str(value))
    return path if path.is_file() else None


def write_registered_manifest(path: Path) -> None:
    """Перенаправляет регистрацию текущего пользователя на копию с новым портом."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, DEVELOPER_KEY) as key:
        winreg.SetValueEx(key, ADDIN_ID, 0, winreg.REG_SZ, str(path))
