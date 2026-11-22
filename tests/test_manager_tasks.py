import threading

import pytest

from helpers import ScriptedRunner, factory_for, wait_until
from host.engines.base import EngineError
from host.engines.manager import LOCAL_TOOLS_NOT_FOUND_MESSAGE, NOT_FOUND_MESSAGE, EngineManager
from host.errors import ActionFailure
from host.tasks import TaskManager, TaskState


def manager(config, **runners):
    return EngineManager(config, factory_for(runners))


def test_auto_prefers_codex(config):
    m = manager(config, codex=ScriptedRunner("codex"), claude=ScriptedRunner("claude"))
    assert m.select()[0].name == "codex"


def test_auto_prefers_authenticated_openrouter(config):
    m = manager(config, openrouter=ScriptedRunner("openrouter"), codex=ScriptedRunner("codex"))
    assert m.select()[0].name == "openrouter"


def test_local_tools_action_skips_openrouter(config):
    m = manager(config, openrouter=ScriptedRunner("openrouter"), codex=ScriptedRunner("codex"))
    assert m.select()[0].name == "openrouter"
    assert m.select_local_tools()[0].name == "codex"


def test_local_tools_action_falls_back_to_claude(config):
    m = manager(
        config,
        openrouter=ScriptedRunner("openrouter"),
        codex=ScriptedRunner("codex", available=False),
        claude=ScriptedRunner("claude"),
    )
    assert m.select_local_tools()[0].name == "claude"


def test_local_tools_action_requires_cli_engine(config):
    m = manager(config, openrouter=ScriptedRunner("openrouter"))
    with pytest.raises(EngineError) as info:
        m.select_local_tools()
    assert info.value.kind == "not_found" and info.value.message == LOCAL_TOOLS_NOT_FOUND_MESSAGE


def test_auto_falls_back_to_claude(config):
    m = manager(config, codex=ScriptedRunner("codex", available=False), claude=ScriptedRunner("claude"))
    assert m.select()[0].name == "claude"


def test_no_engine(config):
    m = manager(config)
    with pytest.raises(EngineError) as info:
        m.select()
    assert info.value.kind == "not_found" and info.value.message == NOT_FOUND_MESSAGE


def test_engine_needs_login(config):
    m = manager(config, codex=ScriptedRunner("codex", authenticated=False))
    with pytest.raises(EngineError) as info:
        m.select()
    assert info.value.kind == "auth"
    assert info.value.message == "Требуется вход в Codex. Выполните вход и повторите"


def test_explicit_engine_and_switch_without_restart(config):
    m = manager(config, codex=ScriptedRunner("codex"), claude=ScriptedRunner("claude"))
    config.update({"engine": "claude"})
    assert m.select()[0].name == "claude"
    config.update({"engine": "codex"})
    assert m.select()[0].name == "codex"


def test_explicit_engine_unavailable_does_not_silently_switch(config):
    m = manager(config, codex=ScriptedRunner("codex"), claude=ScriptedRunner("claude", available=False))
    config.update({"engine": "claude"})
    with pytest.raises(EngineError) as info:
        m.select()
    assert "Claude Code" in info.value.message


# ---------- очередь задач ----------

def test_task_done():
    tasks = TaskManager(1)
    task = tasks.submit("a", lambda t: {"ok": 1})
    assert task.wait(5)
    assert task.state == TaskState.DONE and task.result == {"ok": 1}


def test_task_failure_carries_message():
    tasks = TaskManager(1)

    def work(t):
        raise ActionFailure("invalid_response", "Ответ не соответствует схеме, показан как есть", raw="сырой")

    task = tasks.submit("a", work)
    task.wait(5)
    assert task.state == TaskState.FAILED
    assert task.error == {"code": "invalid_response", "message": "Ответ не соответствует схеме, показан как есть", "raw": "сырой"}


def test_unexpected_exception_does_not_leak_text():
    tasks = TaskManager(1)

    def work(t):
        raise RuntimeError("Договор поставки № 15 с ООО «Ромашка»")

    task = tasks.submit("a", work)
    task.wait(5)
    assert task.error["code"] == "internal"
    assert "Ромашка" not in task.error["message"]


def test_cancel_queued_task_never_runs():
    tasks = TaskManager(1)
    gate = threading.Event()
    ran = []
    first = tasks.submit("a", lambda t: (gate.wait(5), {})[1])
    second = tasks.submit("b", lambda t: ran.append(1) or {})
    tasks.cancel(second.id)
    gate.set()
    first.wait(5)
    assert second.wait(5)
    assert second.state == TaskState.CANCELLED and not ran


def test_cancel_running_task_stops_engine():
    tasks = TaskManager(1)
    runner = ScriptedRunner("codex", delay=30)
    task = tasks.submit("a", lambda t: runner.run("p", None, None, 60, t.set_progress, control=t.control) and {})
    assert wait_until(lambda: task.state == TaskState.RUNNING and runner.calls)
    tasks.cancel(task.id)
    assert task.wait(5)
    assert task.state == TaskState.CANCELLED and task.control.cancelled


def test_engine_error_maps_to_failed():
    tasks = TaskManager(1)

    def work(t):
        raise EngineError("limit", "You've hit your usage limit.")

    task = tasks.submit("a", work)
    task.wait(5)
    assert task.error == {"code": "engine_limit", "message": "You've hit your usage limit."}
