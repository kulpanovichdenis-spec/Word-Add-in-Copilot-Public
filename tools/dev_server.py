"""Стенд для разработки панели без Word: HTTP на 127.0.0.1 и подставные движки.

    python tools/dev_server.py                 # подставной Codex, временная папка проекта
    python tools/dev_server.py --delay 6       # имитация долгого ответа (фоновый режим, отмена)
    python tools/dev_server.py --project C:\\...\\legal-copilot --real   # настоящие движки

Панель: http://127.0.0.1:45889/taskpane — вне Word она открывается в режиме отладки
с полем для текста вместо выделения.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn  # noqa: E402

from helpers import FAKE_CLAUDE, FAKE_CODEX, FIXTURES, FakeDaData  # noqa: E402
from host.app import build_state, create_app  # noqa: E402
from host.config import ConfigStore  # noqa: E402
from host.engines.claude_code import ClaudeCodeRunner  # noqa: E402
from host.engines.codex import CodexRunner  # noqa: E402


def temp_project(home: Path) -> Path:
    root = home / "legal-copilot"
    for skill in ("word-clause-review", "word-contract-review", "counterparty-check"):
        (root / "skills" / skill).mkdir(parents=True)
        (root / "skills" / skill / "SKILL.md").write_text(f"# {skill}\n", "utf-8")
    (root / "references").mkdir()
    shutil.copy(FIXTURES / "clause-review-response-schema.md", root / "references")
    shutil.copy(FIXTURES / "contract-review-response-schema.md", root / "references")
    shutil.copy(FIXTURES / "counterparty-check-response-schema.md", root / "references")
    (root / "references" / "contract-playbook.md").write_text("# Dev playbook\n", "utf-8")
    (root / "references" / "risk-scale.md").write_text("# Dev risk scale\n", "utf-8")
    (root / "workflow").mkdir()
    (root / "CLAUDE.md").write_text("# dev\n", "utf-8")
    return root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="папка legal-copilot (по умолчанию — временная)")
    parser.add_argument("--real", action="store_true", help="настоящие движки вместо подставных")
    parser.add_argument("--delay", type=float, default=1.5, help="задержка подставного движка, с")
    parser.add_argument("--port", type=int, default=45889)
    parser.add_argument("--codex-model", default="")
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="wac-dev-"))
    project = Path(args.project) if args.project else temp_project(home)
    config = ConfigStore(home / "config.json")
    config.update({"projectPath": str(project), "port": args.port, "shortTimeout": 5, "codexModel": args.codex_model})

    factory = None
    if not args.real:
        os.environ["FAKE_DELAY"] = str(args.delay)
        factory = lambda name, s: CodexRunner(FAKE_CODEX) if name == "codex" else ClaudeCodeRunner(FAKE_CLAUDE)  # noqa: E731

    state = build_state(config, engine_factory=factory, journal_path=home / "journal.jsonl", scheme="http")
    if not args.real:
        state.service.dadata = FakeDaData()
    print(f"Панель: http://127.0.0.1:{args.port}/taskpane   проект: {project}   данные: {home}")
    print(f"Одноразовый код подключения (5 минут): {state.pairing.issue()}", flush=True)
    uvicorn.run(create_app(state), host="127.0.0.1", port=args.port, log_config=None)


if __name__ == "__main__":
    main()
