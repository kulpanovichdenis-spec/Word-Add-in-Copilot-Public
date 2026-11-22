"""Единый контракт движков и общий запуск подпроцесса (ТЗ 7.1).

Промпт передаётся через stdin, а не аргументом: так текст договора не попадает
в командную строку процесса (её видят другие программы) и не упирается в лимит
длины командной строки Windows.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import threading
import time
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

ProgressFn = Callable[[str], None]
ErrorKind = Literal["not_found", "auth", "limit", "timeout", "cancelled", "failed"]

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STDERR_TAIL = 16_000


@dataclass
class EngineStatus:
    name: str
    available: bool
    version: str = ""
    path: str = ""
    authenticated: bool | None = None
    error: str = ""


@dataclass
class EngineResult:
    text: str
    json: Any = None
    exit_code: int = 0
    duration: float = 0.0
    events: list[dict[str, str]] = field(default_factory=list)
    usage: "TokenUsage" = field(default_factory=lambda: TokenUsage())


@dataclass
class TokenUsage:
    """Нечувствительная статистика расхода одного или нескольких вызовов модели."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    estimated: bool = False

    @classmethod
    def estimated_from_text(cls, text: str) -> "TokenUsage":
        # Для смешанного русского текста и JSON это намеренно приблизительная оценка.
        tokens = max(1, math.ceil(len(text) / 2.5)) if text else 0
        return cls(input_tokens=tokens, total_tokens=tokens, estimated=True)

    @classmethod
    def from_mapping(cls, raw: object) -> "TokenUsage":
        data = raw if isinstance(raw, dict) else {}
        prompt = int(data.get("input_tokens") or data.get("prompt_tokens") or 0)
        cache_read = int(data.get("cache_read_input_tokens") or 0)
        cache_created = int(data.get("cache_creation_input_tokens") or 0)
        details = data.get("prompt_tokens_details") or data.get("input_tokens_details") or {}
        cached = int(details.get("cached_tokens") or data.get("cached_input_tokens") or
                     data.get("cached_tokens") or cache_read or 0) if isinstance(details, dict) else cache_read
        output = int(data.get("output_tokens") or data.get("completion_tokens") or 0)
        output_details = data.get("output_tokens_details") or data.get("completion_tokens_details") or {}
        reasoning = int(output_details.get("reasoning_tokens") or data.get("reasoning_tokens") or 0) if isinstance(output_details, dict) else 0
        input_total = prompt + cache_read + cache_created
        total = int(data.get("total_tokens") or (input_total + output))
        cost = data.get("cost")
        if cost is None:
            cost = data.get("total_cost")
        return cls(input_total, cached, output, reasoning, total, float(cost) if cost is not None else None)

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        costs = [value for value in (self.cost_usd, other.cost_usd) if value is not None]
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=sum(costs) if costs else None,
            estimated=self.estimated or other.estimated,
        )

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "inputTokens": self.input_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "outputTokens": self.output_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "totalTokens": self.total_tokens or self.input_tokens + self.output_tokens,
            "estimated": self.estimated,
        }
        if self.cost_usd is not None:
            result["costUsd"] = round(self.cost_usd, 8)
        return result


class EngineError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, details: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.details = details


class RunControl:
    """Ручка запущенного подпроцесса: через неё задачу снимают (ТЗ 6.1, /cancel)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._cancel_callback: Callable[[], None] | None = None
        self._usage = TokenUsage()
        self._estimate: tuple[int, TokenUsage] | None = None
        self._usage_seq = 0

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def attach(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._proc = proc
            if self._cancelled:
                kill_tree(proc)

    def attach_cancel(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._cancel_callback = callback
            cancelled = self._cancelled
        if cancelled:
            callback()

    def clear_cancel(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._cancel_callback is callback:
                self._cancel_callback = None

    def begin_usage(self, text: str) -> int:
        with self._lock:
            if self._estimate is not None:
                self._usage = self._usage + self._estimate[1]
            self._usage_seq += 1
            self._estimate = (self._usage_seq, TokenUsage.estimated_from_text(text))
            return self._usage_seq

    def complete_usage(self, usage_id: int, usage: TokenUsage) -> None:
        with self._lock:
            if self._estimate is not None and self._estimate[0] == usage_id:
                self._estimate = None
            self._usage = self._usage + usage

    def finish_estimate(self, usage_id: int) -> None:
        with self._lock:
            if self._estimate is not None and self._estimate[0] == usage_id:
                self._usage = self._usage + self._estimate[1]
                self._estimate = None

    def usage(self) -> TokenUsage:
        with self._lock:
            return self._usage + (self._estimate[1] if self._estimate is not None else TokenUsage())

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            proc = self._proc
            callback = self._cancel_callback
        if proc is not None:
            kill_tree(proc)
        if callback is not None:
            try:
                callback()
            except Exception:  # noqa: BLE001 — отмена должна быть идемпотентной
                pass


class EngineRunner(Protocol):
    name: str
    display_name: str

    def is_available(self) -> EngineStatus:
        """Найден ли бинарник, выполнен ли вход, версия."""

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
        """Запуск подпроцесса, разбор потока, финальный ответ."""

    def cancel(self, control: RunControl) -> None:
        """Снять задачу и завершить подпроцесс."""


def kill_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # CLI движков порождают дочерние процессы (shell, MCP-серверы) — гасим всё дерево.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
    if proc.poll() is None:
        proc.kill()


@dataclass
class ProcessOutcome:
    exit_code: int
    stderr: str
    duration: float


def run_process(
    cmd: list[str],
    *,
    cwd: Path,
    stdin_text: str,
    timeout: int,
    on_line: Callable[[str], None],
    control: RunControl,
) -> ProcessOutcome:
    """Запускает CLI движка, построчно отдаёт stdout в on_line, соблюдает таймаут и отмену."""
    start = time.monotonic()
    try:
        # Двоичные каналы: текстовый режим на Windows превращает \n в \r\n при записи в stdin.
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise EngineError("not_found", f"Не удалось запустить {Path(cmd[0]).name}: {exc.strerror or exc}") from exc
    control.attach(proc)

    stderr_parts: list[str] = []

    def pump_stderr() -> None:
        assert proc.stderr is not None
        size = 0
        for chunk in io.TextIOWrapper(proc.stderr, encoding="utf-8", errors="replace"):
            stderr_parts.append(chunk)
            size += len(chunk)
            while size > STDERR_TAIL and len(stderr_parts) > 1:
                size -= len(stderr_parts.pop(0))

    def pump_stdout() -> None:
        assert proc.stdout is not None
        for line in io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace"):
            line = line.rstrip("\r\n")
            if line:
                on_line(line)

    threads = [threading.Thread(target=pump_stdout, daemon=True), threading.Thread(target=pump_stderr, daemon=True)]
    for t in threads:
        t.start()

    try:
        assert proc.stdin is not None
        proc.stdin.write(stdin_text.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass  # процесс завершился раньше, чем прочитал stdin — причину покажет код возврата

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        proc.wait()
        for t in threads:
            t.join(timeout=5)
        raise EngineError("timeout", f"Движок не ответил за {timeout} с") from None
    for t in threads:
        t.join(timeout=5)

    if control.cancelled:
        raise EngineError("cancelled", "Задача отменена")
    return ProcessOutcome(proc.returncode, "".join(stderr_parts), time.monotonic() - start)


def probe(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """Короткий служебный вызов CLI (--version, статус входа)."""
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


_AUTH = re.compile(
    r"not logged in|please run /login|log ?in required|codex login|unauthori[sz]ed|\b401\b|invalid api key|authenticat",
    re.I,
)
_LIMIT = re.compile(r"usage limit|rate.?limit|limit reached|\b429\b|quota|too many requests", re.I)


def classify_failure(text: str) -> ErrorKind:
    if _AUTH.search(text):
        return "auth"
    if _LIMIT.search(text):
        return "limit"
    return "failed"


def first_line(text: str, limit: int = 140) -> str:
    for line in text.splitlines():
        line = line.strip().strip("*#_` ").strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def tail(text: str, limit: int = 2000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]
