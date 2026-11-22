import os
import stat

import pytest

from helpers import EngineResult, ScriptedRunner, factory_for
from host.engines.base import EngineError, EngineStatus
from host.engines.manager import EngineManager
from host.setup import (
    CLAUDE_AUTH,
    CLAUDE_MISSING,
    CODEX_AUTH,
    CODEX_MISSING,
    PROBE_PROMPT,
    PortBusy,
    check_engine,
    choose_port,
    hints_for,
    port_moved_message,
    rewrite_manifest_port,
    switch_port,
)

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp>
  <IconUrl DefaultValue="https://localhost:45889/static/assets/icon-32.png"/>
  <SupportUrl DefaultValue="https://github.com/kulpanovichdenis-spec/Word-Add-in-Copilot-Public"/>
  <AppDomain>https://127.0.0.1:45889</AppDomain>
  <SourceLocation DefaultValue="https://localhost:45889/taskpane"/>
</OfficeApp>
"""


def _status(name: str, *, available: bool = True, authenticated: bool | None = True) -> EngineStatus:
    return EngineStatus(name, available, authenticated=authenticated)


def test_hint_when_codex_missing():
    text = hints_for({"codex": _status("codex", available=False)}, "codex")
    assert text == CODEX_MISSING
    assert "codex login" in text
    assert "git clone" not in text


def test_hint_when_codex_needs_login():
    assert hints_for({"codex": _status("codex", authenticated=False)}, "codex") == CODEX_AUTH


def test_hint_when_claude_missing_names_the_install_command():
    text = hints_for({"claude": _status("claude", available=False)}, "claude")
    assert text == CLAUDE_MISSING
    assert "npm i -g @anthropic-ai/claude-code" in text
    assert "claude auth login" in text
    assert "Приложение само ничего не устанавливает" in text
    assert "git clone" not in text


def test_hint_when_claude_needs_login():
    assert hints_for({"claude": _status("claude", authenticated=False)}, "claude") == CLAUDE_AUTH


def test_auto_with_working_engine_has_no_hint():
    statuses = {
        "codex": _status("codex", available=False),
        "claude": _status("claude"),
    }
    assert hints_for(statuses, "auto") == ""


def test_auto_lists_every_blocked_engine():
    statuses = {
        "codex": _status("codex", available=False),
        "claude": _status("claude", authenticated=False),
    }
    text = hints_for(statuses, "auto")
    assert text == f"{CODEX_MISSING}\n{CLAUDE_AUTH}"


def test_explicit_engine_ignores_the_other():
    statuses = {
        "codex": _status("codex", available=False),
        "claude": _status("claude"),
    }
    assert hints_for(statuses, "claude") == ""
    assert hints_for(statuses, "codex") == CODEX_MISSING


def test_unknown_auth_is_not_a_login_hint():
    assert hints_for({"claude": _status("claude", authenticated=None)}, "claude") == ""


def test_rewrite_manifest_port_replaces_localhost_urls_only():
    updated = rewrite_manifest_port(MANIFEST, 45890)
    assert updated.count("45890") == 3
    assert "45889" not in updated
    assert "https://github.com/kulpanovichdenis-spec/Word-Add-in-Copilot-Public" in updated
    assert "https://127.0.0.1:45890" in updated


def test_choose_port_keeps_a_free_port():
    assert choose_port(45889, lambda _port: False) == 45889


def test_choose_port_skips_busy_ports():
    busy = {45889, 45890}
    assert choose_port(45889, lambda port: port in busy) == 45891


def test_choose_port_raises_when_the_range_is_full():
    with pytest.raises(PortBusy, match="45889"):
        choose_port(45889, lambda _port: True, span=3)


def test_switch_port_rewrites_manifest_in_place(tmp_path):
    manifest = tmp_path / "manifest.xml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    result = switch_port(45889, lambda port: port == 45889, manifest_path=manifest, fallback_dir=tmp_path / "copy")
    assert result.port == 45890
    assert result.moved
    assert result.registry_update is None
    assert result.manifest == manifest
    assert "45890" in manifest.read_text(encoding="utf-8")
    assert result.warning == port_moved_message(45889, 45890, manifest_updated=True, copied=False)


def test_switch_port_copies_a_read_only_manifest(tmp_path):
    manifest = tmp_path / "locked" / "manifest.xml"
    manifest.parent.mkdir()
    manifest.write_text(MANIFEST, encoding="utf-8")
    os.chmod(manifest, stat.S_IREAD)
    fallback = tmp_path / "fallback"
    try:
        result = switch_port(45889, lambda port: port == 45889, manifest_path=manifest, fallback_dir=fallback)
    finally:
        os.chmod(manifest, stat.S_IWRITE)
    copy = fallback / "manifest.xml"
    assert result.manifest == copy
    assert result.registry_update == str(copy)
    assert "45890" in copy.read_text(encoding="utf-8")
    assert "45889" in manifest.read_text(encoding="utf-8")
    assert "копия манифеста" in result.warning


def test_switch_port_without_manifest_still_moves(tmp_path):
    result = switch_port(45889, lambda port: port == 45889, manifest_path=None, fallback_dir=tmp_path)
    assert result.port == 45890
    assert result.manifest is None
    assert "не переписан" in result.warning


def test_switch_port_does_nothing_when_preferred_is_free(tmp_path):
    manifest = tmp_path / "manifest.xml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    result = switch_port(45889, lambda _port: False, manifest_path=manifest, fallback_dir=tmp_path / "copy")
    assert result.port == 45889
    assert not result.moved
    assert result.warning == ""
    assert manifest.read_text(encoding="utf-8") == MANIFEST


def test_check_engine_sends_a_short_prompt_without_a_schema(config):
    runner = ScriptedRunner("codex", answers=["готово"])
    manager = EngineManager(config, factory_for({"codex": runner}))
    assert check_engine(manager, config.path.parent) == "готово"
    call = runner.calls[0]
    assert call["prompt"] == PROBE_PROMPT
    assert call["schema"] is None
    assert call["writable"] is False
    assert "договор" not in call["prompt"].lower()


def test_check_engine_rejects_an_empty_answer(config):
    runner = ScriptedRunner("codex", answers=[EngineResult(text="  ")])
    manager = EngineManager(config, factory_for({"codex": runner}))
    with pytest.raises(EngineError) as info:
        check_engine(manager, config.path.parent)
    assert info.value.kind == "failed"


def test_check_engine_reports_missing_login(config):
    runner = ScriptedRunner("codex", authenticated=False)
    manager = EngineManager(config, factory_for({"codex": runner}))
    with pytest.raises(EngineError) as info:
        check_engine(manager, config.path.parent)
    assert info.value.kind == "auth"
    assert runner.calls == []
