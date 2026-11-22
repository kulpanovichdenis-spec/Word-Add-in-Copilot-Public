"""Изолированное read-only представление Legal Copilot для LLM-движка."""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..actions.runtime import RUNTIME_CONTRACT_VERSION, RUNTIME_MANIFEST_PATH

SAFE_SUFFIXES = frozenset({".md", ".txt", ".json", ".yaml", ".yml"})
MAX_SAFE_FILE_BYTES = 2 * 1024 * 1024
MAX_SAFE_TOTAL_BYTES = 20 * 1024 * 1024
# Корпоративный чек-лист — данные компании в личном слое профиля legal-copilot.
CHECKLIST_DIR = Path("profile/knowledge/contract-checklist")
CHECKLIST_REFERENCE_FILES = (
    CHECKLIST_DIR / "word-contract-checklist.md",
    CHECKLIST_DIR / "contract-checklist-response-schema.md",
)


def _copy_file(source: Path, target: Path, budget: list[int]) -> None:
    if source.is_symlink() or source.suffix.lower() not in SAFE_SUFFIXES:
        return
    try:
        size = source.stat().st_size
    except OSError:
        return
    if size > MAX_SAFE_FILE_BYTES or budget[0] + size > MAX_SAFE_TOTAL_BYTES:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    budget[0] += size


def _copy_tree(source: Path, target: Path, budget: list[int]) -> None:
    if not source.is_dir() or source.is_symlink():
        return
    for item in source.rglob("*"):
        if item.is_dir() or item.is_symlink():
            continue
        try:
            relative = item.relative_to(source)
        except ValueError:
            continue
        _copy_file(item, target / relative, budget)


def _listed_files(root: Path, base: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(
        item.relative_to(base).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


@contextmanager
def isolated_project(source: Path, skill: str, capabilities: tuple[str, ...] = ()) -> Iterator[Path]:
    """Скопировать только инструкции/справочники, исключив рабочие документы и секреты."""
    with tempfile.TemporaryDirectory(prefix="word-addin-copilot-public-llm-") as folder:
        target = Path(folder)
        budget = [0]
        for name in ("CLAUDE.md", "AGENTS.md"):
            _copy_file(source / name, target / name, budget)
        _copy_tree(source / "skills" / skill, target / "skills" / skill, budget)
        if "legal-methodology-read" in capabilities:
            _copy_tree(source / "references", target / "references", budget)
        if "contract-checklist-read" in capabilities:
            for relative in CHECKLIST_REFERENCE_FILES:
                _copy_file(source / relative, target / relative, budget)
        if "clause-cards-read" in capabilities:
            _copy_tree(
                source / "profile" / "knowledge" / "clauses" / "cards",
                target / "profile" / "knowledge" / "clauses" / "cards",
                budget,
            )
        manifest_path = target / RUNTIME_MANIFEST_PATH
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "contractVersion": RUNTIME_CONTRACT_VERSION,
            "skill": skill,
            "inputProtection": "host-verified-anonymized",
            "capabilities": sorted(capabilities),
            "referenceFiles": _listed_files(target / "references", target) + _listed_files(target / CHECKLIST_DIR, target),
            "clauseCardFiles": _listed_files(
                target / "profile" / "knowledge" / "clauses" / "cards",
                target,
            ),
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        # Некоторые навыки проверяют наличие каталога, но исходные документы туда не попадают.
        (target / "workflow").mkdir(exist_ok=True)
        yield target
