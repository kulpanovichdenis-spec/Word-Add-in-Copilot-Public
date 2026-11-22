"""Адаптеры движков на подставных исполняемых файлах (ТЗ 13.1)."""

import json
import threading
import time
from pathlib import Path

import pytest

from helpers import FAKE_CLAUDE, FAKE_CODEX, FIXTURES, VALID_CARD, process_alive, wait_until
from host.actions.schema import engine_schema, load_schema_file
from host.engines.base import EngineError, RunControl
from host.engines.claude_code import ClaudeCodeRunner
from host.engines.codex import CodexRunner, describe_command, unwrap_error

SCHEMA = engine_schema(load_schema_file(FIXTURES / "clause-review-response-schema.md"))
PROMPT = "Примени навык `skills/clause-review/SKILL.md`.\n<<<ТЕКСТ-1\nПункт 7.3 «Цена»\nТЕКСТ-1>>>"


@pytest.fixture
def record(tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(path))
    return path


def recorded(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


RUNNERS = {
    "codex": lambda: CodexRunner(FAKE_CODEX),
    "claude": lambda: ClaudeCodeRunner(FAKE_CLAUDE),
}


# ---------- Codex ----------

def test_codex_happy_path(tmp_path, record):
    progress = []
    result = CodexRunner(FAKE_CODEX).run(PROMPT, tmp_path, SCHEMA, 30, progress.append)
    assert result.json == VALID_CARD
    assert result.exit_code == 0
    assert progress[:3] == ["Сеанс Codex запущен", "Движок анализирует запрос", "Изучаю раздел об ответственности"]
    assert "Обращаюсь к legal-kb: search" in progress
    assert "Открываю SKILL.md" in progress
    assert {"type": "item.completed", "item": "agent_message"} in result.events
    assert result.usage.input_tokens == 1 and result.usage.output_tokens == 1
    assert not result.usage.estimated

    call = recorded(record)
    argv = call["argv"]
    assert argv[0] == "exec" and argv[-1] == "-"
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--cd") + 1] == str(tmp_path)
    for flag in ("--json", "--skip-git-repo-check", "--ephemeral", "--output-schema", "--output-last-message"):
        assert flag in argv
    assert call["stdin"] == PROMPT
    assert all("Пункт 7.3" not in a for a in argv)  # текст договора — только через stdin


def test_codex_refuses_writable_mode(tmp_path, record):
    with pytest.raises(EngineError, match="отключены политикой безопасности"):
        CodexRunner(FAKE_CODEX).run(PROMPT, tmp_path, None, 30, lambda p: None, writable=True, model="gpt-5.5")
    assert not record.exists()


def test_codex_falls_back_to_stream_message(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "no_file")
    result = CodexRunner(FAKE_CODEX).run(PROMPT, tmp_path, SCHEMA, 30, lambda p: None)
    assert result.json == VALID_CARD


@pytest.mark.parametrize("mode,kind", [("auth", "auth"), ("limit", "limit"), ("crash", "failed")])
def test_codex_failures(tmp_path, monkeypatch, mode, kind):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(EngineError) as info:
        CodexRunner(FAKE_CODEX).run(PROMPT, tmp_path, SCHEMA, 30, lambda p: None)
    assert info.value.kind == kind
    if mode == "limit":
        assert info.value.message == "You've hit your usage limit."


def test_describe_command():
    assert describe_command(["powershell", "-c", "Get-Content 'references/risk-scale.md'"]) == "Открываю risk-scale.md"
    assert describe_command("rg -n cap .") == "Выполняю команду"


def test_unwrap_error_extracts_api_message():
    raw = json.dumps({"type": "error", "status": 400, "error": {"type": "invalid_request_error", "message": "Model requires newer Codex"}})
    assert unwrap_error(raw) == "Model requires newer Codex"
    assert unwrap_error("plain text") == "plain text"


def test_codex_status(monkeypatch):
    status = CodexRunner(FAKE_CODEX).is_available()
    assert status.available and status.authenticated and status.version == "codex-cli 0.0.0-fake"
    monkeypatch.setenv("FAKE_AUTH", "0")
    assert CodexRunner(FAKE_CODEX).is_available().authenticated is False


def test_codex_not_found(tmp_path):
    assert not CodexRunner(None).is_available().available
    with pytest.raises(EngineError) as info:
        CodexRunner([str(tmp_path / "missing.exe")]).run(PROMPT, tmp_path, None, 5, lambda p: None)
    assert info.value.kind == "not_found"


# ---------- Claude Code ----------

def test_claude_happy_path_with_structured_output(tmp_path, record):
    progress = []
    result = ClaudeCodeRunner(FAKE_CLAUDE).run(PROMPT, tmp_path, SCHEMA, 30, progress.append)
    assert result.json == VALID_CARD
    assert "Сеанс Claude Code запущен" in progress and "Читаю SKILL.md" in progress
    assert result.usage.input_tokens == 3 and result.usage.cached_input_tokens == 1
    assert result.usage.cost_usd == pytest.approx(0.0001)

    call = recorded(record)
    argv = call["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    for flag in ("-p", "--verbose", "--no-session-persistence", "--json-schema", "--disallowedTools"):
        assert flag in argv
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert "Bash" in denied and "Write" in denied
    schema_arg = argv[argv.index("--json-schema") + 1]
    assert schema_arg.isascii() and json.loads(schema_arg) == SCHEMA
    assert call["stdin"] == PROMPT


def test_claude_refuses_writable_mode(tmp_path, record):
    with pytest.raises(EngineError, match="отключены политикой безопасности"):
        ClaudeCodeRunner(FAKE_CLAUDE).run(PROMPT, tmp_path, None, 30, lambda p: None, writable=True, model="opus")
    assert not record.exists()


def test_claude_text_result_without_structured(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_STRUCTURED", "0")
    result = ClaudeCodeRunner(FAKE_CLAUDE).run(PROMPT, tmp_path, SCHEMA, 30, lambda p: None)
    assert result.json is None
    assert json.loads(result.text) == VALID_CARD


def test_claude_not_logged_in(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "auth")
    with pytest.raises(EngineError) as info:
        ClaudeCodeRunner(FAKE_CLAUDE).run(PROMPT, tmp_path, SCHEMA, 30, lambda p: None)
    assert info.value.kind == "auth"


def test_claude_status(monkeypatch):
    status = ClaudeCodeRunner(FAKE_CLAUDE).is_available()
    assert status.available and status.authenticated and status.version == "9.9.9 (Claude Code)"
    monkeypatch.setenv("FAKE_AUTH", "0")
    assert ClaudeCodeRunner(FAKE_CLAUDE).is_available().authenticated is False


# ---------- таймаут и отмена: общее для обоих адаптеров ----------

@pytest.mark.parametrize("engine", ["codex", "claude"])
def test_timeout_kills_process_tree(tmp_path, monkeypatch, engine):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_CHILD_PID", str(pid_file))
    started = time.monotonic()
    with pytest.raises(EngineError) as info:
        RUNNERS[engine]().run(PROMPT, tmp_path, None, 3, lambda p: None)
    assert info.value.kind == "timeout"
    assert time.monotonic() - started < 20
    assert wait_until(lambda: not process_alive(int(pid_file.read_text())))


@pytest.mark.parametrize("engine", ["codex", "claude"])
def test_cancel_kills_process_tree(tmp_path, monkeypatch, engine):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_MODE", "sleep")
    monkeypatch.setenv("FAKE_CHILD_PID", str(pid_file))
    control = RunControl()
    outcome = {}

    def target():
        try:
            RUNNERS[engine]().run(PROMPT, tmp_path, None, 60, lambda p: None, control=control)
        except EngineError as exc:
            outcome["kind"] = exc.kind

    thread = threading.Thread(target=target)
    thread.start()
    assert wait_until(pid_file.exists, 20)
    control.cancel()
    thread.join(20)
    assert not thread.is_alive()
    assert outcome["kind"] == "cancelled"
    assert wait_until(lambda: not process_alive(int(pid_file.read_text())))
