"""CodexRunner: `codex exec` (ТЗ 7.2). Флаги сверены с codex-cli 0.137.0-alpha.4."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .base import (
    EngineError,
    EngineResult,
    EngineStatus,
    ProgressFn,
    RunControl,
    TokenUsage,
    classify_failure,
    first_line,
    probe,
    run_process,
    tail,
)


_FILE_IN_COMMAND = re.compile(r"([\w\-.]+\.(?:md|ya?ml|json|py|txt|docx|xlsx))\b", re.I)


def describe_command(command: object) -> str:
    """Codex читает навыки и справочники командами оболочки — показываем имя файла, а не «команду»."""
    text = " ".join(map(str, command)) if isinstance(command, list) else str(command or "")
    names = _FILE_IN_COMMAND.findall(text)
    return f"Открываю {names[-1]}" if names else "Выполняю команду"


def unwrap_error(message: object) -> str:
    """Codex кладёт ответ API как JSON-строку: {"type":"error","error":{"message":"..."}} — достаём текст."""
    text = str(message or "").strip()
    for _ in range(3):
        try:
            data = json.loads(text)
        except ValueError:
            break
        if not isinstance(data, dict):
            break
        inner = data.get("error", data.get("message"))
        if isinstance(inner, dict):
            inner = inner.get("message")
        if not inner:
            break
        text = str(inner).strip()
    return text


class CodexEventParser:
    """Разбор JSONL-потока `codex exec --json` в строки прогресса."""

    def __init__(self, on_progress: ProgressFn) -> None:
        self.on_progress = on_progress
        self.events: list[dict[str, str]] = []
        self.last_message = ""
        self.error = ""
        self.usage = TokenUsage()

    def feed(self, line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = str(event.get("type", ""))
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type", ""))
        # В events храним только типы — без содержимого, которое может цитировать договор.
        self.events.append({"type": kind, "item": item_type} if item_type else {"type": kind})

        if kind == "thread.started":
            self._progress("Сеанс Codex запущен")
        elif kind == "turn.started":
            self._progress("Движок анализирует запрос")
        elif kind in ("item.started", "item.updated", "item.completed"):
            self._item(kind, item_type, item)
        elif kind == "turn.failed":
            error = event.get("error")
            self.error = unwrap_error(error.get("message", "") if isinstance(error, dict) else error)
        elif kind == "error":
            self.error = unwrap_error(event.get("message", ""))
        elif kind == "turn.completed":
            self.usage = TokenUsage.from_mapping(event.get("usage"))

    def _item(self, kind: str, item_type: str, item: dict[str, Any]) -> None:
        started = kind == "item.started"
        if item_type == "agent_message":
            if kind == "item.completed":
                self.last_message = str(item.get("text", ""))
        elif item_type == "reasoning":
            self._progress(first_line(str(item.get("text", ""))))
        elif item_type == "command_execution" and started:
            self._progress(describe_command(item.get("command")))
        elif item_type == "mcp_tool_call" and started:
            server, tool = item.get("server", ""), item.get("tool", "")
            self._progress(f"Обращаюсь к {server}: {tool}" if server else "Обращаюсь к базе знаний")
        elif item_type == "web_search" and started:
            self._progress("Ищу в интернете")
        elif item_type == "file_change":
            self._progress("Сохраняю файл")
        elif item_type == "todo_list" and started:
            self._progress("Составляю план")
        elif item_type == "error":
            self.error = str(item.get("message", ""))

    def _progress(self, text: str) -> None:
        if text:
            self.on_progress(text)


class CodexRunner:
    name = "codex"
    display_name = "Codex"

    def __init__(self, command: list[str] | None) -> None:
        self.command = command

    def is_available(self) -> EngineStatus:
        if not self.command:
            return EngineStatus(self.name, False, error="Codex CLI не найден")
        path = self.command[-1]
        code, out = probe(self.command + ["--version"])
        if code != 0:
            return EngineStatus(self.name, False, path=path, error=first_line(out) or "codex --version завершился с ошибкой")
        version = first_line(out)
        code, out = probe(self.command + ["login", "status"])
        authenticated = code == 0 and "logged in" in out.lower()
        return EngineStatus(self.name, True, version=version, path=path, authenticated=authenticated)

    def run(
        self,
        prompt: str,
        cwd: Path,
        output_schema: dict[str, Any] | None,
        timeout: int,
        on_progress: ProgressFn,
        *,
        writable: bool = False,
        model: str = "",
        control: RunControl | None = None,
    ) -> EngineResult:
        if not self.command:
            raise EngineError("not_found", "Codex CLI не найден")
        if writable:
            raise EngineError("permission", "Записывающие действия LLM отключены политикой безопасности")
        control = control or RunControl()
        usage_id = control.begin_usage(prompt)
        parser = CodexEventParser(on_progress)

        with tempfile.TemporaryDirectory(prefix="wac-codex-") as tmp:
            last_message = Path(tmp) / "last-message.txt"
            cmd = self.command + [
                "exec",
                "--cd", str(cwd),
                "--json",
                "--skip-git-repo-check",
                "--ephemeral",  # не сохранять сессию (с текстом договора) в ~/.codex/sessions
                "--color", "never",
                "--sandbox", "read-only",
                "--output-last-message", str(last_message),
            ]
            if output_schema is not None:
                schema_file = Path(tmp) / "schema.json"
                schema_file.write_text(json.dumps(output_schema, ensure_ascii=False), "utf-8")
                cmd += ["--output-schema", str(schema_file)]
            if model:
                cmd += ["--model", model]
            cmd.append("-")  # промпт — из stdin

            try:
                outcome = run_process(
                    cmd, cwd=cwd, stdin_text=prompt, timeout=timeout, on_line=parser.feed, control=control
                )
            except EngineError:
                if parser.usage.total_tokens or parser.usage.input_tokens or parser.usage.output_tokens:
                    control.complete_usage(usage_id, parser.usage)
                raise
            text = last_message.read_text("utf-8") if last_message.exists() else parser.last_message

        text = text.strip()
        if outcome.exit_code != 0 or (parser.error and not text):
            reason = parser.error or first_line(tail(outcome.stderr)) or f"код возврата {outcome.exit_code}"
            kind = classify_failure(f"{parser.error}\n{outcome.stderr}")
            raise EngineError(kind, reason, details=tail(outcome.stderr))

        if parser.usage.total_tokens or parser.usage.input_tokens or parser.usage.output_tokens:
            control.complete_usage(usage_id, parser.usage)
        else:
            control.finish_estimate(usage_id)

        parsed = None
        if output_schema is not None:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        return EngineResult(text=text, json=parsed, exit_code=outcome.exit_code, duration=outcome.duration,
                            events=parser.events, usage=control.usage())

    def cancel(self, control: RunControl) -> None:
        control.cancel()
