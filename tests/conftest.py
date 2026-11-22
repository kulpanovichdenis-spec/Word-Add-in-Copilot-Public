from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from helpers import FIXTURES, REPO
from host.config import ConfigStore


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Минимальная папка legal-copilot: CLAUDE.md, навыки панели и их схемы."""
    root = tmp_path / "legal-copilot"
    for skill in (
        "word-clause-review",
        "word-contract-review",
        "word-lawyer-instruction",
        "word-contract-checklist",
        "counterparty-check",
    ):
        (root / "skills" / skill).mkdir(parents=True)
        (root / "skills" / skill / "SKILL.md").write_text(f"# {skill}\n", "utf-8")
    (root / "references").mkdir()
    shutil.copy(FIXTURES / "clause-review-response-schema.md", root / "references" / "clause-review-response-schema.md")
    shutil.copy(FIXTURES / "contract-review-response-schema.md", root / "references" / "contract-review-response-schema.md")
    shutil.copy(FIXTURES / "counterparty-check-response-schema.md", root / "references" / "counterparty-check-response-schema.md")
    checklist_dir = root / "profile/knowledge/contract-checklist"
    checklist_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "contract-checklist-response-schema.md", checklist_dir / "contract-checklist-response-schema.md")
    (root / "workflow").mkdir()
    (root / "references" / "contract-playbook.md").write_text("# Playbook\n", "utf-8")
    (root / "references" / "risk-scale.md").write_text("# Risk scale\n", "utf-8")
    (checklist_dir / "word-contract-checklist.md").write_text("# Checklist\n", "utf-8")
    (root / "references" / "contract-playbook-cards").mkdir()
    (root / "references" / "contract-playbook-cards" / "INDEX.md").write_text("# Cards\n", "utf-8")
    (root / "profile" / "knowledge" / "clauses" / "cards").mkdir(parents=True)
    (root / "profile" / "knowledge" / "clauses" / "cards" / "approved.md").write_text(
        "# Approved clause\n", "utf-8"
    )
    (root / "CLAUDE.md").write_text("# Профиль практики (тест)\n", "utf-8")
    return root


@pytest.fixture
def config(tmp_path: Path, project: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "home" / "config.json")
    store.update({"projectPath": str(project), "actionsPath": str(REPO / "actions.yaml"), "shortTimeout": 10})
    return store
