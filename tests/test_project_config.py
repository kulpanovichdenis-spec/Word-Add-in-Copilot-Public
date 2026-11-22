import pytest

from host.__main__ import LISTEN_HOST
from host.config import ConfigStore, Settings
from host.project import check_project


def test_project_not_set():
    status = check_project("")
    assert not status.valid
    assert "не задана" in status.message


def test_project_missing_folder(tmp_path):
    status = check_project(tmp_path / "nope")
    assert not status.valid
    assert "не найдена" in status.message


def test_project_without_claude_md(project):
    (project / "CLAUDE.md").unlink()
    status = check_project(project)
    assert not status.valid
    assert status.message == "В указанной папке нет CLAUDE.md. Проверьте путь в настройках"


def test_project_without_references(project):
    import shutil

    shutil.rmtree(project / "references")
    status = check_project(project)
    assert status.missing == ["references/"]


def test_project_valid(project):
    assert check_project(project).valid


def test_config_persists_and_reloads(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.update({"engine": "claude", "codexModel": "gpt-5.5"})
    again = ConfigStore(path)
    assert again.settings.engine == "claude"
    assert again.settings.codex_model == "gpt-5.5"


def test_openrouter_settings_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.update({"engine": "openrouter", "openrouterModel": "openai/gpt-5.4", "openrouterMaxTokens": 8000})
    again = ConfigStore(path)
    assert again.settings.engine == "openrouter"
    assert again.settings.openrouter_model == "openai/gpt-5.4"
    assert again.settings.openrouter_max_tokens == 8000
    assert "openrouterKey" not in again.settings.public()


def test_config_rejects_unknown_key(tmp_path):
    with pytest.raises(ValueError, match="Неизвестные параметры"):
        ConfigStore(tmp_path / "c.json").update({"bogus": 1})


def test_config_rejects_bad_engine(tmp_path):
    with pytest.raises(ValueError):
        ConfigStore(tmp_path / "c.json").update({"engine": "gpt"})


def test_broken_config_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", "utf-8")
    assert ConfigStore(path).settings == Settings()


def test_server_listens_on_loopback_only():
    assert LISTEN_HOST == "127.0.0.1"
