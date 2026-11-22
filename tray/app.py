"""Tray-приложение: хост в том же процессе + окно настроек (ТЗ, раздел 9).

    python -m tray            запустить (иконка в области уведомлений, сервер панели)
    python -m tray --selftest собрать окно и сразу закрыть — проверка без GUI-сессии

Технология — Python + pystray + tkinter: один рантайм с хостом, без отдельного Tauri.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import uvicorn

from host.__main__ import LISTEN_HOST, configure_logging
from host.actions.registry import RegistryError
from host.api.routes import health_payload
from host.app import HostState, build_state, create_app
from host.config import app_home, tls_dir
from host.engines.base import EngineError
from host.machine import ensure_user_trust
from host.project import check_project
from host.security.credentials import CredentialStore
from host.security.tls import tls_files
from host.sharepoint import LibraryFolder
from host.setup import (
    PortBusy,
    check_engine,
    hints_from_payload,
    read_registered_manifest,
    switch_port,
    write_registered_manifest,
)

log = logging.getLogger(__name__)
ICON = Path(__file__).parent / "assets" / "icon-64.png"
ENGINE_CHOICES = {
    "Автовыбор (OpenRouter первым)": "auto",
    "OpenRouter": "openrouter",
    "Codex": "codex",
    "Claude Code": "claude",
}
RESULT_LABELS = {"ok": "готово", "cancelled": "отменено"}


class HostServer:
    """uvicorn в фоновом потоке; перезапуск без выхода из приложения."""

    def __init__(self, state: HostState, *, tls: bool = True) -> None:
        self.state = state
        self.tls = tls
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.error = ""
        self.warning = ""

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.server and self.server.started)

    def start(self) -> None:
        settings = self.state.config.settings
        self.error = ""
        self.warning = ""
        try:
            moved = switch_port(
                settings.port,
                port_busy,
                manifest_path=read_registered_manifest(),
                fallback_dir=app_home() / "manifests",
            )
        except PortBusy as exc:
            self.error = str(exc)
            return
        if moved.moved:
            self.state.config.update({"port": moved.port})
            warning = moved.warning
            if moved.registry_update:
                try:
                    write_registered_manifest(Path(moved.registry_update))
                except OSError:
                    warning += " Регистрацию в Word обновить не удалось."
            self.warning = warning
            settings = self.state.config.settings
        ssl: dict[str, str] = {}
        if self.tls:
            # Установка без прав администратора: сертификат и доверие к нему ставит само приложение.
            if not ensure_user_trust():
                certificate = ("Сертификат не подтверждён — без него Word не откроет панель. "
                               "Нажмите «Перезапустить» и ответьте «Да» в окне Windows")
                self.warning = f"{self.warning} {certificate}".strip()
            default = tls_files(tls_dir())
            cert, key = settings.tls_cert or str(default.cert), settings.tls_key or str(default.key)
            if not (Path(cert).is_file() and Path(key).is_file()):
                self.error = "Нет TLS-сертификата localhost. Переустановите приложение"
                return
            ssl = {"ssl_certfile": cert, "ssl_keyfile": key}
        config = uvicorn.Config(create_app(self.state), host=LISTEN_HOST, port=settings.port,
                                log_config=None, access_log=False, **ssl)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="host-server", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=10)
        self.server = self.thread = None

    def restart(self) -> None:
        self.stop()
        self.start()


_instance_handle = None


def acquire_single_instance() -> bool:
    """Автозапуск, запуск после установки и ручной запуск не должны поднять два сервера."""
    global _instance_handle
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _instance_handle = kernel32.CreateMutexW(None, False, "Local\\WordAddinCopilotPublicTray")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((LISTEN_HOST, port)) == 0


class SettingsWindow:
    def __init__(self, root: tk.Tk, host: HostServer) -> None:
        self.root = root
        self.host = host
        self.state = host.state
        self.credentials = CredentialStore()
        root.title("Word-Add-in-Copilot-Public")
        root.minsize(690, 670)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)  # крестик прячет окно, приложение остаётся в трее

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self.status = tk.StringVar()
        ttk.Label(frame, text="Состояние:").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.status, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, columnspan=2, sticky="w")

        s = self.state.config.settings
        ttk.Label(frame, text="Папка legal-copilot:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.project = tk.StringVar(value=s.project_path)
        ttk.Entry(frame, textvariable=self.project).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(frame, text="Выбрать…", command=self.choose_folder).grid(row=1, column=2, padx=(6, 0), pady=(10, 0))
        self.project_hint = tk.StringVar()
        ttk.Label(frame, textvariable=self.project_hint, foreground="#8a1c1c").grid(row=2, column=1, columnspan=2, sticky="w")

        ttk.Label(frame, text="Движок:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        reverse = {v: k for k, v in ENGINE_CHOICES.items()}
        self.engine = tk.StringVar(value=reverse[s.engine])
        ttk.Combobox(frame, textvariable=self.engine, values=list(ENGINE_CHOICES), state="readonly", width=18).grid(
            row=3, column=1, sticky="w", pady=(8, 0))

        ttk.Label(frame, text="Модель OpenRouter:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.openrouter_model = tk.StringVar(value=s.openrouter_model)
        ttk.Entry(frame, textvariable=self.openrouter_model, width=34).grid(row=4, column=1, sticky="w", pady=(6, 0))

        ttk.Label(frame, text="Токен OpenRouter:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.openrouter_token = tk.StringVar()
        token_row = ttk.Frame(frame)
        token_row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Entry(token_row, textvariable=self.openrouter_token, show="•", width=34).pack(side="left")
        self.openrouter_token_status = tk.StringVar()
        ttk.Label(token_row, textvariable=self.openrouter_token_status, foreground="#616161").pack(side="left", padx=6)
        ttk.Button(token_row, text="Удалить", command=self.delete_openrouter_token).pack(side="left")

        ttk.Label(frame, text="API-ключ DaData:").grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.dadata_api_key = tk.StringVar()
        ttk.Entry(frame, textvariable=self.dadata_api_key, show="•", width=34).grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Секретный ключ DaData:").grid(row=7, column=0, sticky="w", pady=(6, 0))
        self.dadata_secret_key = tk.StringVar()
        dadata_row = ttk.Frame(frame)
        dadata_row.grid(row=7, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Entry(dadata_row, textvariable=self.dadata_secret_key, show="•", width=34).pack(side="left")
        self.dadata_status = tk.StringVar()
        ttk.Label(dadata_row, textvariable=self.dadata_status, foreground="#616161").pack(side="left", padx=6)
        ttk.Button(dadata_row, text="Удалить", command=self.delete_dadata_keys).pack(side="left")
        self._refresh_token_status()

        ttk.Label(frame, text="Модель Codex:").grid(row=8, column=0, sticky="w", pady=(6, 0))
        self.codex_model = tk.StringVar(value=s.codex_model)
        ttk.Entry(frame, textvariable=self.codex_model, width=24).grid(row=8, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Модель Claude:").grid(row=9, column=0, sticky="w", pady=(6, 0))
        self.claude_model = tk.StringVar(value=s.claude_model)
        ttk.Entry(frame, textvariable=self.claude_model, width=24).grid(row=9, column=1, sticky="w", pady=(6, 0))
        self.engines_info = tk.StringVar()
        ttk.Label(frame, textvariable=self.engines_info, foreground="#616161").grid(row=10, column=1, columnspan=2, sticky="w")
        ttk.Label(frame, text="Типовые формы SharePoint:").grid(row=11, column=0, sticky="w", pady=(8, 0))
        self.templates_url = tk.StringVar(value=s.sharepoint_templates_url)
        ttk.Entry(frame, textvariable=self.templates_url).grid(row=11, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.setup_hint = tk.StringVar()
        tk.Label(frame, textvariable=self.setup_hint, wraplength=520, justify="left", fg="#8a1c1c").grid(
            row=12, column=1, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Button(frame, text="Сохранить настройки", command=self.save).grid(row=13, column=1, sticky="w", pady=(8, 0))
        ttk.Button(frame, text="Проверить движок", command=self.on_check_engine).grid(row=13, column=2, padx=(6, 0), pady=(8, 0))

        ttk.Label(frame, text="Последние запросы (без текста документа):").grid(row=14, column=0, columnspan=3, sticky="w", pady=(12, 2))
        columns = ("time", "action", "engine", "tokens", "duration", "result")
        self.journal = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for col, title, width in zip(columns, ("Время", "Действие", "Движок", "Токены", "Длит., с", "Результат"), (140, 130, 85, 90, 70, 120)):
            self.journal.heading(col, text=title)
            self.journal.column(col, width=width, stretch=col == "action")
        self.journal.grid(row=15, column=0, columnspan=3, sticky="nsew")
        frame.rowconfigure(15, weight=1)

        buttons = ttk.Frame(frame)
        buttons.grid(row=16, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Перезапустить", command=self.restart).pack(side="left")
        ttk.Button(buttons, text="Перечитать действия", command=self.reload_actions).pack(side="left", padx=6)
        ttk.Button(buttons, text="Открыть папку логов", command=open_logs).pack(side="left")
        ttk.Button(buttons, text="Выдать код панели", command=self.show_pairing_code).pack(side="left", padx=6)

        self._checking = False
        self.refresh_health()
        self.refresh_journal()

    def show_pairing_code(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Подключение панели Word")
        dialog.transient(self.root)
        ttk.Label(dialog, text="Вставьте этот одноразовый код в панель Word. Он действует 5 минут.").pack(padx=12, pady=8)
        code = tk.StringVar(dialog, value=self.state.pairing.issue())
        entry = ttk.Entry(dialog, textvariable=code, state="readonly", width=42)
        entry.pack(padx=12, pady=(0, 8))
        entry.selection_range(0, "end")
        entry.focus_set()
        ttk.Button(dialog, text="Закрыть", command=dialog.destroy).pack(pady=(0, 8))

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(title="Папка legal-copilot", initialdir=self.project.get() or str(Path.home()))
        if folder:
            self.project.set(str(Path(folder)))
            status = check_project(folder)
            self.project_hint.set("" if status.valid else status.message)

    def save(self) -> None:
        status = check_project(self.project.get())
        self.project_hint.set("" if status.valid else status.message)
        try:
            templates_url = self.templates_url.get().strip()
            if templates_url:
                LibraryFolder.from_url(templates_url)
            self.state.config.update({
                "projectPath": self.project.get().strip(),
                "engine": ENGINE_CHOICES[self.engine.get()],
                "codexModel": self.codex_model.get().strip(),
                "claudeModel": self.claude_model.get().strip(),
                "openrouterModel": self.openrouter_model.get().strip() or "openrouter/auto",
                "sharepointTemplatesUrl": templates_url,
            })
            token = self.openrouter_token.get().strip()
            if token:
                self.credentials.set_openrouter_key(token)
                self.openrouter_token.set("")
            dadata_api = self.dadata_api_key.get().strip()
            dadata_secret = self.dadata_secret_key.get().strip()
            if dadata_api or dadata_secret:
                self.credentials.set_dadata_keys(dadata_api, dadata_secret)
                self.dadata_api_key.set("")
                self.dadata_secret_key.set("")
        except (ValueError, OSError) as exc:
            messagebox.showerror("Настройки", str(exc))
            return
        self.state.engines.invalidate()
        self._refresh_token_status()
        self.refresh_health(background=True)

    def _refresh_token_status(self) -> None:
        self.openrouter_token_status.set("сохранён" if self.credentials.has_openrouter_key() else "не задан")
        self.dadata_status.set("сохранены" if self.credentials.has_dadata_keys() else "не заданы")

    def delete_openrouter_token(self) -> None:
        if not self.credentials.has_openrouter_key():
            return
        if not messagebox.askyesno("OpenRouter", "Удалить сохранённый токен OpenRouter с этого компьютера?"):
            return
        self.credentials.delete_openrouter_key()
        self.state.engines.invalidate()
        self._refresh_token_status()
        self.refresh_health(background=True)

    def delete_dadata_keys(self) -> None:
        if not self.credentials.has_dadata_keys():
            return
        if not messagebox.askyesno("DaData", "Удалить сохранённые ключи DaData с этого компьютера?"):
            return
        self.credentials.delete_dadata_keys()
        self._refresh_token_status()
        self.refresh_health(background=True)

    def restart(self) -> None:
        self.host.restart()
        if self.host.error:
            messagebox.showerror("Перезапуск", self.host.error)
        self.refresh_health(background=True)

    def reload_actions(self) -> None:
        try:
            registry = self.state.service.reload_actions()
        except RegistryError as exc:
            messagebox.showerror("Реестр действий", str(exc))
            return
        messagebox.showinfo("Реестр действий", f"Загружено действий: {len(registry)}")

    def refresh_health(self, background: bool = False) -> None:
        def work() -> None:
            health = health_payload(self.state)
            self.root.after(0, lambda: self.show_health(health))

        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()

    def show_health(self, h: dict) -> None:
        if self.host.warning:
            text = self.host.warning
        elif not self.host.running:
            text = f"не запущен — {self.host.error}" if self.host.error else "не запущен"
        elif not h["engineAvailable"]:
            text = f"работает, но {h['engineMessage']}"
        elif not h["projectValid"]:
            text = f"работает, но {h['projectMessage']}"
        else:
            text = f"работает · {h['engine']} {h['engineVersion']}"
        self.status.set(text)
        self.project_hint.set("" if h["projectValid"] else h["projectMessage"])
        self.setup_hint.set(hints_from_payload(h["engines"], h["engineSetting"]))
        parts = []
        for name, st in h["engines"].items():
            if not st["available"]:
                parts.append(f"{name}: не найден")
            elif st["authenticated"] is False:
                parts.append(f"{name}: {'нужен токен' if name == 'openrouter' else 'нужен вход'}")
            else:
                parts.append(f"{name}: {st['version']}")
        self.engines_info.set(" · ".join(parts))

    def on_check_engine(self) -> None:
        """Короткий запрос к выбранному движку. Текст договора не отправляется и в журнал не пишется."""
        if self._checking:
            return
        self._checking = True
        self.status.set("проверяю движок…")

        def work() -> None:
            try:
                self.state.engines.invalidate()
                with tempfile.TemporaryDirectory(prefix="wac-probe-") as folder:
                    text = check_engine(self.state.engines, Path(folder))
                self.root.after(0, lambda: self._show_probe(text, error=False))
            except EngineError as exc:
                message = exc.message
                self.root.after(0, lambda: self._show_probe(message, error=True))
            except Exception:
                log.exception("проверка движка")
                self.root.after(0, lambda: self._show_probe("Не удалось проверить движок", error=True))

        threading.Thread(target=work, daemon=True).start()

    def _show_probe(self, text: str, *, error: bool) -> None:
        self._checking = False
        shown = text.strip()[:1000]
        if error:
            messagebox.showerror("Проверка движка", shown)
        else:
            messagebox.showinfo("Проверка движка", shown or "Движок ответил")
        self.refresh_health(background=True)

    def refresh_journal(self) -> None:
        self.journal.delete(*self.journal.get_children())
        for entry in self.state.journal.recent(50):
            usage = entry.get("usage") or {}
            tokens = usage.get("totalTokens", 0)
            if usage.get("estimated") and tokens:
                tokens = f"≈{tokens}"
            self.journal.insert("", "end", values=(
                entry["time"].replace("T", " ")[:19], entry["actionId"], entry["engine"],
                tokens or "—", entry["duration"], RESULT_LABELS.get(entry["result"], entry["result"])))
        self.root.after(3000, self.refresh_journal)


def open_logs() -> None:
    folder = app_home() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    os.startfile(folder)  # type: ignore[attr-defined]


def run_tray(root: tk.Tk, window: SettingsWindow, host: HostServer):
    import pystray
    from PIL import Image

    def show(*_):
        root.after(0, lambda: (root.deiconify(), root.lift(), window.refresh_health(background=True)))

    def restart(*_):
        root.after(0, window.restart)

    def reload(*_):
        root.after(0, window.reload_actions)

    def quit_app(icon, *_):
        icon.stop()
        root.after(0, root.quit)

    menu = pystray.Menu(
        pystray.MenuItem("Открыть", show, default=True),
        pystray.MenuItem("Перезапустить", restart),
        pystray.MenuItem("Перечитать действия", reload),
        pystray.MenuItem("Открыть папку логов", lambda *_: open_logs()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", quit_app),
    )
    icon = pystray.Icon("WordAddinCopilotPublic", Image.open(ICON), "Word-Add-in-Copilot-Public", menu)
    icon.run_detached()
    return icon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tray")
    parser.add_argument("--no-tls", action="store_true", help="HTTP — только для отладки вне Word")
    parser.add_argument("--selftest", action="store_true", help="собрать окно и закрыть")
    args = parser.parse_args(argv)

    if not args.selftest and not acquire_single_instance():
        return 0
    configure_logging(app_home() / "logs")
    state = build_state(journal_path=app_home() / "logs" / "journal.jsonl", scheme="http" if args.no_tls else "https")
    host = HostServer(state, tls=not args.no_tls)
    root = tk.Tk()
    if args.selftest:
        SettingsWindow(root, host)
        root.update()
        root.destroy()
        print("selftest ok")
        return 0

    host.start()
    window = SettingsWindow(root, host)
    if host.error or host.warning or not state.config.settings.project_path:
        root.deiconify()  # первый запуск или ошибка — сразу показываем окно
    else:
        root.withdraw()
    icon = run_tray(root, window, host)
    try:
        root.mainloop()
    finally:
        icon.stop()
        host.stop()
        state.tasks.shutdown()
        state.documents.close()
    return 0
