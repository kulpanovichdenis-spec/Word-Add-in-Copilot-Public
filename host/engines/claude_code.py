"""ClaudeCodeRunner: `claude -p` (ТЗ 7.3).

Флаги сверены с Claude Code 2.1.266 (`claude --help`, 2026-09-11):
- `--output-format stream-json` в режиме `-p` требует `--verbose`;
- `--json-schema` даёт принудительную схему ответа (ТЗ предполагало, что её нет);
- `--permission-mode dontAsk` отклоняет всё, что не разрешено явно через `--allowedTools`;
- `--no-session-persistence` не сохраняет сессию с текстом договора на диск.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePath
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

READ_ONLY_DENY = ["Write", "Edit", "NotebookEdit", "Bash", "PowerShell"]


def describe_tool(name: str, tool_input: Any) -> str:
    args = tool_input if isinstance(tool_input, dict) else {}
    if name == "Read":
        file_path = str(args.get("file_path", ""))
        return f"Читаю {PurePath(file_path).name}" if file_path else "Читаю файл"
    if name in ("Grep", "Glob"):
        return "Ищу в файлах проекта"
    if name == "WebSearch":
        return "Ищу в интернете"
    if name == "WebFetch":
        return "Открываю источник"
    if name in ("Write", "Edit", "NotebookEdit"):
        return "Сохраняю файл"
    if name in ("Bash", "PowerShell"):
        return "Выполняю команду"
    if name == "Skill":
        return "Загружаю навык"
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else ""
        tool = parts[2] if len(parts) > 2 else ""
        return f"Обращаюсь к {server}: {tool}"
    return f"Инструмент {name}"


class ClaudeStreamParser:
    """Разбор `--output-format stream-json` в строки прогресса и финальный результат."""

    def __init__(self, on_progress: ProgressFn) -> None:
        self.on_progress = on_progress
        self.events: list[dict[str, str]] = []
        self.result_text = ""
        self.structured: Any = None
        self.is_error = False
        self.got_result = False
        self.usage = TokenUsage()

    def feed(self, line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = str(event.get("type", ""))
        subtype = str(event.get("subtype", ""))
        self.events.append({"type": kind, "subtype": subtype} if subtype else {"type": kind})

        if kind == "system" and subtype == "init":
            self._progress("Сеанс Claude Code запущен")
        elif kind == "assistant":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    self._progress(describe_tool(str(block.get("name", "")), block.get("input")))
                elif block.get("type") == "text":
                    text = first_line(str(block.get("text", "")))
                    if text and not text.startswith(("{", "[")):
                        self._progress(text)
        elif kind == "result":
            self.got_result = True
            self.result_text = str(event.get("result") or "")
            self.structured = event.get("structured_output")
            self.is_error = bool(event.get("is_error")) or (subtype not in ("", "success"))
            if self.is_error and not self.result_text:
                errors = event.get("errors")
                self.result_text = "; ".join(map(str, errors)) if isinstance(errors, list) else subtype
            raw_usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            if event.get("total_cost_usd") is not None:
                raw_usage = dict(raw_usage, cost=event.get("total_cost_usd"))
            self.usage = TokenUsage.from_mapping(raw_usage)

    def _progress(self, text: str) -> None:
        if text:
            self.on_progress(text)


class ClaudeCodeRunner:
    name = "claude"
    display_name = "Claude Code"

    def __init__(
        self,
        command: list[str] | None,
        *,
        read_tools: list[str] | None = None,
    ) -> None:
        from ..config import CLAUDE_READ_TOOLS

        self.command = command
        self.read_tools = list(read_tools if read_tools is not None else CLAUDE_READ_TOOLS)

    def is_available(self) -> EngineStatus:
        if not self.command:
            return EngineStatus(self.name, False, error="Claude Code CLI не найден")
        path = self.command[-1]
        code, out = probe(self.command + ["--version"])
        if code != 0:
            return EngineStatus(self.name, False, path=path, error=first_line(out) or "claude --version завершился с ошибкой")
        version = first_line(out)
        _, out = probe(self.command + ["auth", "status"])
        authenticated: bool | None
        try:
            authenticated = bool(json.loads(out[out.index("{"): out.rindex("}") + 1]).get("loggedIn"))
        except ValueError:
            authenticated = None  # версия без `auth status` — узнаем при запуске
        return EngineStatus(self.name, True, version=version, path=path, authenticated=authenticated)

    def build_command(self, cwd: Path, output_schema: dict[str, Any] | None, *, writable: bool, model: str) -> list[str]:
        assert self.command
        if writable:
            raise EngineError("permission", "Записывающие действия LLM отключены политикой безопасности")
        cmd = self.command + [
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--add-dir", str(cwd),
            "--permission-mode", "dontAsk",
        ]
        cmd += ["--allowedTools", *self.read_tools, "--disallowedTools", *READ_ONLY_DENY]
        if output_schema is not None:
            # ensure_ascii: схема уходит аргументом командной строки — только ASCII, без сюрпризов кодировки.
            cmd += ["--json-schema", json.dumps(output_schema, ensure_ascii=True, separators=(",", ":"))]
        if model:
            cmd += ["--model", model]
        return cmd

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
            raise EngineError("not_found", "Claude Code CLI не найден")
        control = control or RunControl()
        usage_id = control.begin_usage(prompt)
        parser = ClaudeStreamParser(on_progress)
        cmd = self.build_command(cwd, output_schema, writable=writable, model=model)
        try:
            outcome = run_process(cmd, cwd=cwd, stdin_text=prompt, timeout=timeout, on_line=parser.feed, control=control)
        except EngineError:
            if parser.usage.total_tokens or parser.usage.input_tokens or parser.usage.output_tokens:
                control.complete_usage(usage_id, parser.usage)
            raise

        if outcome.exit_code != 0 or parser.is_error or not parser.got_result:
            reason = parser.result_text or first_line(tail(outcome.stderr)) or f"код возврата {outcome.exit_code}"
            kind = classify_failure(f"{parser.result_text}\n{outcome.stderr}")
            raise EngineError(kind, reason, details=tail(outcome.stderr))

        if parser.usage.total_tokens or parser.usage.input_tokens or parser.usage.output_tokens:
            control.complete_usage(usage_id, parser.usage)
        else:
            control.finish_estimate(usage_id)

        structured = parser.structured
        text = parser.result_text.strip()
        if structured is not None and not text:
            text = json.dumps(structured, ensure_ascii=False)
        return EngineResult(text=text, json=structured, exit_code=outcome.exit_code, duration=outcome.duration,
                            events=parser.events, usage=control.usage())

    def cancel(self, control: RunControl) -> None:
        control.cancel()
