"""Проверка папки legal-copilot (ТЗ 6.1: projectValid)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = (("CLAUDE.md", False), ("skills", True), ("references", True))


@dataclass
class ProjectStatus:
    path: str
    valid: bool
    missing: list[str] = field(default_factory=list)
    message: str = ""


def find_default_project(home: Path | None = None) -> Path | None:
    """Первый запуск: ищем legal-copilot в обычных местах, чтобы юристу не выбирать папку вручную."""
    home = home or Path.home()
    candidates = [home / "Desktop" / "legal-copilot", home / "Documents" / "legal-copilot", home / "legal-copilot"]
    for onedrive in sorted(home.glob("OneDrive*")):
        candidates += [onedrive / "Desktop" / "legal-copilot", onedrive / "Рабочий стол" / "legal-copilot",
                       onedrive / "Documents" / "legal-copilot", onedrive / "Документы" / "legal-copilot"]
    for candidate in candidates:
        if check_project(candidate).valid:
            return candidate
    return None


def check_project(path: str | Path | None) -> ProjectStatus:
    if not path:
        return ProjectStatus("", False, message="Папка legal-copilot не задана. Укажите путь в настройках")
    root = Path(path)
    if not root.is_dir():
        return ProjectStatus(str(root), False, message=f"Папка не найдена: {root}. Проверьте путь в настройках")

    missing = []
    for name, is_dir in REQUIRED:
        target = root / name
        if not (target.is_dir() if is_dir else target.is_file()):
            missing.append(name + ("/" if is_dir else ""))
    if not missing:
        return ProjectStatus(str(root), True)
    if "CLAUDE.md" in missing:
        message = "В указанной папке нет CLAUDE.md. Проверьте путь в настройках"
    else:
        message = f"В указанной папке нет: {', '.join(missing)}. Проверьте путь в настройках"
    return ProjectStatus(str(root), False, missing, message)
