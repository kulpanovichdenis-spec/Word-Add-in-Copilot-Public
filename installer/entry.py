"""Точка входа собранного WordAddinCopilotPublic.exe.

Без аргументов — tray-приложение (автозапуск, запуск после установки).
С командой хоста (serve, health, run, cert, machine-install, machine-uninstall) — она.
"""

import sys

HOST_COMMANDS = {"serve", "health", "run", "cert", "machine-install", "machine-uninstall", "user-uninstall"}


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in HOST_COMMANDS:
        from host.__main__ import main as host_main

        return host_main(args)
    from tray.app import main as tray_main

    return tray_main(args)


if __name__ == "__main__":
    sys.exit(main())
