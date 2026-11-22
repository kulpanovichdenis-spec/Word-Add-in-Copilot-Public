"""Подставной `codex` для автотестов адаптера (ТЗ 13.1). Поведение — через переменные окружения."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from answers import VALID_CARD  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("codex-cli 0.0.0-fake")
        return 0
    if argv[:2] == ["login", "status"]:
        if os.environ.get("FAKE_AUTH", "1") == "1":
            print("Logged in using ChatGPT")
            return 0
        print("Not logged in", file=sys.stderr)
        return 1

    assert argv[0] == "exec", argv
    stdin = sys.stdin.buffer.read().decode("utf-8")
    record = os.environ.get("FAKE_RECORD")
    if record:
        Path(record).write_text(json.dumps({"argv": argv, "stdin": stdin}, ensure_ascii=False), "utf-8")
    out_file = Path(argv[argv.index("--output-last-message") + 1])
    mode = os.environ.get("FAKE_MODE", "ok")

    emit({"type": "thread.started", "thread_id": "t-1"})
    emit({"type": "turn.started"})
    emit({"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "**Изучаю раздел об ответственности**\n\nдетали"}})

    if mode == "sleep":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        Path(os.environ["FAKE_CHILD_PID"]).write_text(str(child.pid))
        time.sleep(120)
        return 0
    if mode == "auth":
        print("Error: unauthorized: please run `codex login`", file=sys.stderr)
        return 1
    if mode == "limit":
        inner = json.dumps({"type": "error", "status": 429, "error": {"message": "You've hit your usage limit."}})
        emit({"type": "error", "message": inner})
        emit({"type": "turn.failed", "error": {"message": inner}})
        return 1
    if mode == "crash":
        print("panic: boom", file=sys.stderr)
        return 3

    emit({"type": "item.started", "item": {"id": "c1", "type": "command_execution",
                                            "command": "Get-Content skills/word-clause-review/SKILL.md"}})
    time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    answer = os.environ.get("FAKE_ANSWER") or json.dumps(VALID_CARD, ensure_ascii=False)
    if "RESULT_FILE:" in stdin and not os.environ.get("FAKE_ANSWER"):
        # Действие класса file: кладём документ в workflow/ и сообщаем путь, как требует промпт.
        workflow = Path(argv[argv.index("--cd") + 1]) / "workflow"
        workflow.mkdir(exist_ok=True)
        (workflow / "Анализ договора (тест).docx").write_bytes(b"fake docx")
        answer = "Анализ готов: 3 высоких риска.\nRESULT_FILE: workflow/Анализ договора (тест).docx"
    emit({"type": "item.started", "item": {"id": "m1", "type": "mcp_tool_call", "server": "legal-kb", "tool": "search"}})
    emit({"type": "item.completed", "item": {"id": "a1", "type": "agent_message", "text": answer}})
    if mode != "no_file":
        out_file.write_text(answer, "utf-8")
    emit({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
