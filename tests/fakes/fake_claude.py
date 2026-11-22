"""Подставной `claude` для автотестов адаптера (ТЗ 13.1). Поведение — через переменные окружения."""

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
        print("9.9.9 (Claude Code)")
        return 0
    if argv[:2] == ["auth", "status"]:
        logged = os.environ.get("FAKE_AUTH", "1") == "1"
        print(json.dumps({"loggedIn": logged, "authMethod": "claude.ai" if logged else "none"}))
        return 0 if logged else 1

    assert "-p" in argv, argv
    stdin = sys.stdin.buffer.read().decode("utf-8")
    record = os.environ.get("FAKE_RECORD")
    if record:
        Path(record).write_text(json.dumps({"argv": argv, "stdin": stdin}, ensure_ascii=False), "utf-8")
    mode = os.environ.get("FAKE_MODE", "ok")

    emit({"type": "system", "subtype": "init", "session_id": "s-1", "tools": ["Read"], "mcp_servers": []})
    emit({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Изучаю навык и пункт договора"},
        {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file_path": "C:/lc/skills/word-clause-review/SKILL.md"}},
    ]}})

    if mode == "sleep":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        Path(os.environ["FAKE_CHILD_PID"]).write_text(str(child.pid))
        time.sleep(120)
        return 0
    if mode == "auth":
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"})
        return 1

    answer = os.environ.get("FAKE_ANSWER") or json.dumps(VALID_CARD, ensure_ascii=False)
    structured = None
    if "--json-schema" in argv and os.environ.get("FAKE_STRUCTURED", "1") == "1":
        structured = json.loads(answer)
    emit({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 10,
          "result": "" if structured is not None else answer, "structured_output": structured,
          "usage": {"input_tokens": 2, "cache_read_input_tokens": 1, "output_tokens": 1},
          "total_cost_usd": 0.0001})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
