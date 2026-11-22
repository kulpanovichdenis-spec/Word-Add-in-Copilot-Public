"""Общие помощники автотестов."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent / "fakes"))

from answers import VALID_CARD, VALID_CHECKLIST, VALID_COUNTERPARTY, VALID_REVIEW  # noqa: E402,F401

from host.engines.base import EngineError, EngineResult, EngineStatus, RunControl  # noqa: E402

TESTS = Path(__file__).parent
REPO = TESTS.parent
FAKES = TESTS / "fakes"
FIXTURES = TESTS / "fixtures"
FAKE_CODEX = [sys.executable, str(FAKES / "fake_codex.py")]
FAKE_CLAUDE = [sys.executable, str(FAKES / "fake_claude.py")]
VALID_JSON = json.dumps(VALID_CARD, ensure_ascii=False)
VALID_REVIEW_JSON = json.dumps(VALID_REVIEW, ensure_ascii=False)
VALID_COUNTERPARTY_JSON = json.dumps(VALID_COUNTERPARTY, ensure_ascii=False)
VALID_CHECKLIST_JSON = json.dumps(VALID_CHECKLIST, ensure_ascii=False)

Answer = str | EngineResult | Exception | Callable[..., Any]


class FakeDaData:
    def __init__(self, card: dict[str, Any] | None = None) -> None:
        self.card = card or VALID_COUNTERPARTY
        self.calls: list[str] = []

    def configured(self) -> bool:
        return True

    def check(self, identifier: str) -> dict[str, Any]:
        self.calls.append(identifier)
        result = copy.deepcopy(self.card)
        result["identifier"] = identifier
        return result


class ScriptedRunner:
    """Подставной движок в памяти: отдаёт заранее заданные ответы и запоминает вызовы."""

    def __init__(self, name: str = "codex", answers: list[Answer] | None = None, *, available: bool = True,
                 authenticated: bool | None = True, delay: float = 0.0) -> None:
        self.name = name
        self.display_name = {"openrouter": "OpenRouter", "codex": "Codex", "claude": "Claude Code"}[name]
        self.answers = list(answers or [])
        self.available = available
        self.authenticated = authenticated
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    def is_available(self) -> EngineStatus:
        return EngineStatus(self.name, self.available, version=f"{self.name}-fake", authenticated=self.authenticated,
                            error="" if self.available else "не найден")

    def run(self, prompt, cwd, output_schema, timeout, on_progress, *, writable=False, model="", control=None):
        control = control or RunControl()
        self.calls.append({"prompt": prompt, "cwd": cwd, "schema": output_schema, "writable": writable, "model": model})
        on_progress("Подставной движок работает")
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if control.cancelled:
                raise EngineError("cancelled", "Задача отменена")
            time.sleep(0.02)
        if control.cancelled:
            raise EngineError("cancelled", "Задача отменена")
        answer = self.answers.pop(0) if self.answers else VALID_JSON
        if callable(answer) and not isinstance(answer, Exception):
            answer = answer(prompt=prompt, cwd=cwd)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, EngineResult):
            return answer
        return EngineResult(text=answer)

    def cancel(self, control: RunControl) -> None:
        control.cancel()


def factory_for(runners: dict[str, ScriptedRunner]):
    def factory(name, settings):
        return runners.setdefault(name, ScriptedRunner(name, available=False))
    return factory


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()
