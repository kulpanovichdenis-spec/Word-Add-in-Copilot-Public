"""Машинная часть установки, выбор TLS-каталога, автопоиск папки, личный реестр действий."""

import subprocess

import pytest

from host import machine
from host.config import tls_dir
from host.project import find_default_project
from host.security.tls import CA_NAME, generate


class FakeCertutil:
    def __init__(self, existing: int = 0, add_ok: bool = True, trusted: bool = False):
        self.existing = existing
        self.add_ok = add_ok
        self.trusted = trusted
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        verb = next(a for a in cmd[1:] if a in ("-delstore", "-addstore", "-verifystore"))
        if verb == "-delstore":
            code = 0 if self.existing else 1
            self.existing = max(0, self.existing - 1)
        elif verb == "-verifystore":
            code = 0 if self.trusted else 1
        else:
            code = 0 if self.add_ok else 1
        return subprocess.CompletedProcess(cmd, code)


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv("WORD_ADDIN_COPILOT_PUBLIC_MACHINE_HOME", str(tmp_path / "machine"))
    monkeypatch.setenv("WORD_ADDIN_COPILOT_PUBLIC_HOME", str(tmp_path / "user"))
    return tmp_path


def test_user_trust_generates_and_asks_windows_once(homes):
    certutil = FakeCertutil(trusted=False)
    assert machine.ensure_user_trust(run=certutil)
    assert (homes / "user" / "tls" / "localhost.pem").is_file()
    verify, add = certutil.calls
    assert verify[:3] == ["certutil", "-user", "-verifystore"]
    assert add == ["certutil", "-user", "-addstore", "Root", str(homes / "user" / "tls" / "ca.cer")]


def test_user_trust_already_trusted_does_not_prompt(homes):
    certutil = FakeCertutil(trusted=True)
    assert machine.ensure_user_trust(run=certutil)
    assert len(certutil.calls) == 1  # только проверка, окна Windows нет


def test_user_trust_declined(homes):
    assert not machine.ensure_user_trust(run=FakeCertutil(trusted=False, add_ok=False))


def test_user_trust_skipped_when_installed_for_all_users(homes):
    generate(homes / "machine" / "tls")
    certutil = FakeCertutil()
    assert machine.ensure_user_trust(run=certutil)
    assert certutil.calls == []


def test_user_uninstall_removes_user_ca(homes):
    generate(homes / "user" / "tls")
    certutil = FakeCertutil(existing=1)
    machine.uninstall_user(run=certutil)
    assert not (homes / "user" / "tls").exists()
    assert certutil.calls[0] == ["certutil", "-user", "-delstore", "Root", CA_NAME]


def test_install_replaces_old_ca_and_trusts_new(tmp_path):
    certutil = FakeCertutil(existing=2)
    acl_calls = []

    def acl_run(cmd, **kwargs):
        acl_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    folder = machine.install(tmp_path / "tls", run=certutil, acl_run=acl_run)
    deletes = [c for c in certutil.calls if c[1] == "-delstore"]
    assert len(deletes) == 3 and deletes[0] == ["certutil", "-delstore", "Root", CA_NAME]
    assert certutil.calls[-1] == ["certutil", "-f", "-addstore", "Root", str(folder / "ca.cer")]
    assert (folder / "localhost.pem").is_file() and (folder / "localhost-key.pem").is_file()
    assert acl_calls == [[
        "icacls", str(folder), "/inheritance:r", "/grant:r",
        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX",
        "/T", "/C",
    ]]


def test_install_fails_loudly_if_ca_not_trusted(tmp_path):
    with pytest.raises(RuntimeError, match="certutil"):
        machine.install(
            tmp_path / "tls",
            run=FakeCertutil(add_ok=False),
            acl_run=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
        )


def test_install_fails_loudly_if_tls_acl_is_not_applied(tmp_path):
    with pytest.raises(RuntimeError, match="icacls"):
        machine.install(
            tmp_path / "tls",
            run=FakeCertutil(),
            acl_run=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 5),
        )


def test_uninstall_removes_ca_and_files(tmp_path):
    folder = tmp_path / "tls"
    generate(folder)
    certutil = FakeCertutil(existing=1)
    machine.uninstall(folder, run=certutil)
    assert not folder.exists()
    assert all(c[1] == "-delstore" for c in certutil.calls)


def test_tls_dir_prefers_installer_certificate(tmp_path, monkeypatch):
    monkeypatch.setenv("WORD_ADDIN_COPILOT_PUBLIC_MACHINE_HOME", str(tmp_path / "machine"))
    monkeypatch.setenv("WORD_ADDIN_COPILOT_PUBLIC_HOME", str(tmp_path / "user"))
    assert tls_dir() == tmp_path / "user" / "tls"
    generate(tmp_path / "machine" / "tls")
    assert tls_dir() == tmp_path / "machine" / "tls"


def make_project(root):
    (root / "skills").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "CLAUDE.md").write_text("#", "utf-8")
    return root


def test_find_default_project_on_desktop(tmp_path):
    assert find_default_project(tmp_path) is None
    expected = make_project(tmp_path / "Desktop" / "legal-copilot")
    assert find_default_project(tmp_path) == expected


def test_find_default_project_in_onedrive(tmp_path):
    expected = make_project(tmp_path / "OneDrive - Компания" / "Рабочий стол" / "legal-copilot")
    assert find_default_project(tmp_path) == expected


def test_personal_actions_file_overrides_bundled(tmp_path, monkeypatch):
    from host.config import ConfigStore
    from host.engines.manager import EngineManager
    from host.security.journal import Journal
    from host.service import ActionService

    monkeypatch.setenv("WORD_ADDIN_COPILOT_PUBLIC_HOME", str(tmp_path))
    config = ConfigStore(tmp_path / "config.json")
    service = ActionService(config, EngineManager(config), Journal())
    assert service.actions_path().name == "actions.yaml" and service.actions_path().parent != tmp_path
    (tmp_path / "actions.yaml").write_text("actions: []\n", "utf-8")
    assert service.actions_path() == tmp_path / "actions.yaml"
