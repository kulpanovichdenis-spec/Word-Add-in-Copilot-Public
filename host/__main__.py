"""Точка входа хоста.

    python -m host serve [--no-tls]      запустить сервер панели на 127.0.0.1
    python -m host health                состояние: папка, движки, действия
    python -m host run clause-review --text "..."   прогон действия из консоли (этап 2)
    python -m host cert                  выпустить TLS-сертификат localhost
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import app_home

LISTEN_HOST = "127.0.0.1"  # внешний интерфейс не биндится (ТЗ 11.4)


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "host.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:  # в собранном exe без консоли stderr нет
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(logging.INFO)


def _tls_paths(settings) -> tuple[Path, Path]:
    from .config import tls_dir
    from .security.tls import tls_files

    default = tls_files(tls_dir())
    return Path(settings.tls_cert or default.cert), Path(settings.tls_key or default.key)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import build_state, create_app

    state = build_state(journal_path=app_home() / "logs" / "journal.jsonl", scheme="http" if args.no_tls else "https")
    settings = state.config.settings
    ssl: dict[str, str] = {}
    if not args.no_tls:
        cert, key = _tls_paths(settings)
        if not (cert.is_file() and key.is_file()):
            print(f"Нет TLS-сертификата ({cert}). Выполните: python -m host cert", file=sys.stderr)
            return 2
        ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    app = create_app(state)
    if sys.stdout is not None and sys.stdout.isatty():
        print(f"Одноразовый код подключения панели (5 минут): {state.pairing.issue()}", flush=True)
    try:
        uvicorn.run(app, host=LISTEN_HOST, port=settings.port, log_config=None, access_log=False, **ssl)
    finally:
        state.tasks.shutdown()
        state.documents.close()
    return 0


def cmd_health(_: argparse.Namespace) -> int:
    from .api.routes import health_payload
    from .app import build_state

    print(json.dumps(health_payload(build_state()), ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .app import build_state
    from .errors import ActionFailure
    from .service import RunPayload
    from .tasks import Task

    text = Path(args.file).read_text("utf-8") if args.file else (args.text or sys.stdin.read())
    context = dict(item.split("=", 1) for item in args.context)
    state = build_state(journal_path=app_home() / "logs" / "journal.jsonl")
    payload = RunPayload(args.action, text, context, args.document or "")
    try:
        action = state.service.prepare(payload)
    except ActionFailure as failure:
        print(json.dumps({"error": failure.to_dict()}, ensure_ascii=False, indent=2))
        return 1
    task = Task(action.id)
    task.set_progress = lambda p: print(f"… {p}", file=sys.stderr)  # type: ignore[method-assign]
    try:
        result = state.service.execute(task, action, payload)
    except ActionFailure as failure:
        print(json.dumps({"error": failure.to_dict()}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_cert(args: argparse.Namespace) -> int:
    from .security.tls import generate

    folder = Path(args.dir) if args.dir else app_home() / "tls"
    files = generate(folder)
    print(f"Сертификат localhost: {files.cert}\nКлюч: {files.key}\nУЦ для доверенных корневых: {files.ca_cert}")
    print("Добавить УЦ в доверенные (для текущего пользователя, Windows покажет подтверждение):")
    print(f'  certutil -user -addstore Root "{files.ca_cert}"')
    return 0


def cmd_machine_install(_: argparse.Namespace) -> int:
    from .machine import install

    folder = install()
    logging.getLogger(__name__).info("Сертификат localhost выпущен в %s, УЦ добавлен в доверенные", folder)
    return 0


def cmd_machine_uninstall(_: argparse.Namespace) -> int:
    from .machine import uninstall

    uninstall()
    logging.getLogger(__name__).info("УЦ localhost удалён из доверенных")
    return 0


def cmd_user_uninstall(_: argparse.Namespace) -> int:
    from .machine import uninstall_user

    uninstall_user()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m host", description="Word-Add-in-Copilot-Public Host")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="запустить сервер панели")
    serve.add_argument("--no-tls", action="store_true", help="HTTP без TLS — только для отладки вне Word")
    sub.add_parser("health", help="состояние хоста")
    run = sub.add_parser("run", help="выполнить действие из консоли")
    run.add_argument("action")
    run.add_argument("--text")
    run.add_argument("--file", help="файл с текстом в UTF-8")
    run.add_argument("--document", help="имя документа для промпта")
    run.add_argument("--context", action="append", default=[], metavar="KEY=VALUE")
    cert = sub.add_parser("cert", help="выпустить TLS-сертификат localhost")
    cert.add_argument("--dir")
    sub.add_parser("machine-install", help="(для MSI) сертификат localhost в доверенные корневые")
    sub.add_parser("machine-uninstall", help="(для MSI) убрать сертификат localhost")
    sub.add_parser("user-uninstall", help="(для MSI без прав администратора) убрать сертификат localhost")
    args = parser.parse_args(argv)

    if args.cmd is None:
        args = parser.parse_args(["serve", *(argv or sys.argv[1:])])
    if args.cmd.startswith("machine-"):
        from .config import machine_home

        configure_logging(machine_home() / "logs")  # MSI запускает от имени SYSTEM
    else:
        configure_logging(app_home() / "logs")
    handlers = {
        "serve": cmd_serve,
        "health": cmd_health,
        "run": cmd_run,
        "cert": cmd_cert,
        "machine-install": cmd_machine_install,
        "machine-uninstall": cmd_machine_uninstall,
        "user-uninstall": cmd_user_uninstall,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
