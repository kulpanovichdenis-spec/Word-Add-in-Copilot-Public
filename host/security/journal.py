"""Журнал запросов (ТЗ 9, 11.3).

Разрешено: время, действие, длина текста, движок, длительность, расход токенов/стоимость, код результата.
Сигнатура record() принимает только эти поля — текст документа передать в журнал нельзя.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
MAX_BYTES = 5 * 1024 * 1024


class Journal:
    def __init__(self, path: Path | None = None, keep: int = 200) -> None:
        self.path = path
        self._recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._lock = threading.Lock()

    def record(self, *, action_id: str, text_length: int, engine: str, duration: float, result: str,
               usage: dict[str, Any] | None = None) -> None:
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "actionId": action_id,
            "textLength": int(text_length),
            "engine": engine,
            "duration": round(float(duration), 1),
            "result": result,
            "usage": usage or {},
        }
        with self._lock:
            self._recent.append(entry)
            if self.path is None:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                    self.path.replace(self.path.with_suffix(".1.jsonl"))
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError as exc:
                log.warning("Журнал не записан: %s", type(exc).__name__)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)[-limit:][::-1]
