"""Очередь задач: короткие и длинные действия, прогресс, отмена (ТЗ 6.1-6.2)."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Callable

from .engines.base import EngineError, RunControl
from .errors import ActionFailure

log = logging.getLogger(__name__)


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINAL_STATES = {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}


class Task:
    def __init__(self, action_id: str) -> None:
        self.id = uuid.uuid4().hex
        self.action_id = action_id
        self.state = TaskState.QUEUED
        self.progress = "В очереди"
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.created = time.time()
        self.finished: float | None = None
        self.control = RunControl()
        self._done = threading.Event()

    def set_progress(self, text: str) -> None:
        if self.state not in FINAL_STATES:
            self.progress = text

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout)

    def to_dict(self) -> dict[str, Any]:
        ended = self.finished or time.time()
        return {
            "taskId": self.id,
            "actionId": self.action_id,
            "state": self.state.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "durationSeconds": round(max(0.0, ended - self.created), 1),
            "usage": self.control.usage().public(),
        }


Work = Callable[[Task], dict[str, Any]]


class TaskManager:
    def __init__(self, max_workers: int = 2, keep: int = 200) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="engine")
        self._tasks: OrderedDict[str, Task] = OrderedDict()
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, action_id: str, work: Work) -> Task:
        task = Task(action_id)
        with self._lock:
            self._tasks[task.id] = task
            self._prune()
        self._executor.submit(self._run, task, work)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> Task | None:
        task = self.get(task_id)
        if task is None:
            return None
        with self._lock:
            if task.state in FINAL_STATES:
                return task
            was_queued = task.state == TaskState.QUEUED
            self._finish(task, TaskState.CANCELLED, progress="Задача отменена")
        task.control.cancel()
        if was_queued:
            task._done.set()
        return task

    def shutdown(self) -> None:
        with self._lock:
            running = [t for t in self._tasks.values() if t.state not in FINAL_STATES]
        for task in running:
            self.cancel(task.id)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, task: Task, work: Work) -> None:
        with self._lock:
            if task.state == TaskState.CANCELLED:
                return
            task.state = TaskState.RUNNING
            task.progress = "Выполняется"
        try:
            result = work(task)
        except ActionFailure as failure:
            self._settle(task, TaskState.FAILED, error=failure.to_dict())
        except EngineError as exc:
            if exc.kind == "cancelled" or task.control.cancelled:
                self._settle(task, TaskState.CANCELLED)
            else:
                self._settle(task, TaskState.FAILED, error={"code": f"engine_{exc.kind}", "message": exc.message})
        except Exception as exc:  # noqa: BLE001
            # Текст исключения может цитировать документ — в лог только тип и стек.
            log.error("Сбой задачи %s: %s\n%s", task.action_id, type(exc).__name__, "".join(traceback.format_tb(exc.__traceback__)))
            self._settle(task, TaskState.FAILED, error={"code": "internal", "message": "Внутренняя ошибка приложения. Подробности в журнале"})
        else:
            if task.control.cancelled:
                self._settle(task, TaskState.CANCELLED)
            else:
                self._settle(task, TaskState.DONE, result=result)
        finally:
            task._done.set()

    def _settle(self, task: Task, state: TaskState, **kwargs: Any) -> None:
        with self._lock:
            if task.state == TaskState.CANCELLED:
                return  # отмена уже зафиксирована — не перетираем её результатом
            self._finish(task, state, **kwargs)

    @staticmethod
    def _finish(task: Task, state: TaskState, *, result: dict[str, Any] | None = None,
                error: dict[str, Any] | None = None, progress: str | None = None) -> None:
        task.state = state
        task.result = result
        task.error = error
        task.finished = time.time()
        task.progress = progress or {TaskState.DONE: "Готово", TaskState.FAILED: "Ошибка", TaskState.CANCELLED: "Задача отменена"}[state]

    def _prune(self) -> None:
        while len(self._tasks) > self._keep:
            oldest_id, oldest = next(iter(self._tasks.items()))
            if oldest.state not in FINAL_STATES:
                break
            del self._tasks[oldest_id]
