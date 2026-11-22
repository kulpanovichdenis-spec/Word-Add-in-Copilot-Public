"""Сертификат localhost: выпуск и доверие (ТЗ 10.1 п. 6, 10.4).

Два режима установки:

- для всех пользователей (MSI с правами администратора, выполняется от SYSTEM):
      WordAddinCopilotPublic.exe machine-install | machine-uninstall   → УЦ в LocalMachine\\Root
- без прав администратора (MSI для текущего пользователя):
      приложение при запуске само выпускает сертификат в %LOCALAPPDATA% и добавляет УЦ
      в доверенные текущего пользователя — Windows один раз просит подтверждение;
      WordAddinCopilotPublic.exe user-uninstall → снять при удалении.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from cryptography import x509

from .config import app_home, machine_home
from .engines.base import CREATE_NO_WINDOW
from .security.tls import CA_NAME, generate, tls_files

Run = Callable[..., subprocess.CompletedProcess]

SYSTEM_SID = "*S-1-5-18"
ADMINISTRATORS_SID = "*S-1-5-32-544"
USERS_SID = "*S-1-5-32-545"


def _certutil(args: list[str], run: Run, *, user: bool = False) -> int:
    cmd = ["certutil", *(["-user"] if user else []), *args]
    return run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW).returncode


def remove_trusted_ca(run: Run = subprocess.run, *, user: bool = False) -> int:
    """Удаляет из Root все наши УЦ (от прошлых установок). certutil удаляет по одному."""
    removed = 0
    for _ in range(20):
        if _certutil(["-delstore", "Root", CA_NAME], run, user=user) != 0:
            break
        removed += 1
    return removed


# ---------- для всех пользователей (от SYSTEM) ----------

def secure_machine_tls(folder: Path, run: Run = subprocess.run) -> None:
    """Явно ограничить ACL общего TLS-каталога.

    Хост запускается отдельно для каждого пользователя, поэтому группе Users нужен
    только доступ на чтение leaf-ключа; изменять или заменять файлы могут лишь
    SYSTEM и локальные администраторы.
    """
    command = [
        "icacls",
        str(folder),
        "/inheritance:r",
        "/grant:r",
        f"{SYSTEM_SID}:(OI)(CI)F",
        f"{ADMINISTRATORS_SID}:(OI)(CI)F",
        f"{USERS_SID}:(OI)(CI)RX",
        "/T",
        "/C",
    ]
    if run(command, capture_output=True, creationflags=CREATE_NO_WINDOW).returncode != 0:
        raise RuntimeError("icacls не смог ограничить права на TLS-файлы")


def install(folder: Path | None = None, run: Run = subprocess.run, acl_run: Run = subprocess.run) -> Path:
    folder = folder or machine_home() / "tls"
    remove_trusted_ca(run)
    files = generate(folder)
    secure_machine_tls(folder, acl_run)
    if _certutil(["-f", "-addstore", "Root", str(files.ca_cert)], run) != 0:
        raise RuntimeError("certutil не добавил УЦ localhost в доверенные корневые")
    return folder


def uninstall(folder: Path | None = None, run: Run = subprocess.run) -> None:
    remove_trusted_ca(run)
    shutil.rmtree(folder or machine_home() / "tls", ignore_errors=True)


# ---------- для текущего пользователя (без прав администратора) ----------

def _serial(ca_cert: Path) -> str:
    return format(x509.load_der_x509_certificate(ca_cert.read_bytes()).serial_number, "x")


def ensure_user_trust(folder: Path | None = None, run: Run = subprocess.run) -> bool:
    """Сертификат есть и ему доверяют? Если нет — выпустить и попросить Windows доверять.

    Возвращает True, если панель в Word сможет открыться по https://localhost.
    """
    if (machine_home() / "tls" / "localhost.pem").is_file():
        return True  # поставлено установщиком для всех пользователей
    folder = folder or app_home() / "tls"
    files = tls_files(folder)
    if not (files.exist() and files.ca_cert.is_file()):
        files = generate(folder)
    if _certutil(["-verifystore", "Root", _serial(files.ca_cert)], run, user=True) == 0:
        return True
    # Windows покажет окно «Установить этот сертификат?» — единственное подтверждение от юриста.
    return _certutil(["-addstore", "Root", str(files.ca_cert)], run, user=True) == 0


def uninstall_user(folder: Path | None = None, run: Run = subprocess.run) -> None:
    remove_trusted_ca(run, user=True)
    shutil.rmtree(folder or app_home() / "tls", ignore_errors=True)
