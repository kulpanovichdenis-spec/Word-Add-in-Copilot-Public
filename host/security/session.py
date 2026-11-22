"""Защита API от посторонних запросов (ТЗ 11.5).

- Токен выдаётся после ввода одноразового кода, а защищённые запросы
  предъявляют его в заголовке X-Session-Token.
- Заголовок Origin, если он есть, должен совпадать с адресом панели.
- Заголовок Host — только localhost/127.0.0.1 (защита от DNS rebinding).
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from typing import Any, Awaitable, Callable, Iterable

TOKEN_HEADER = "x-session-token"
DEFAULT_HOSTS = frozenset({"localhost", "127.0.0.1"})

Scope = dict[str, Any]
ASGIApp = Callable[[Scope, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


def new_token() -> str:
    return secrets.token_urlsafe(32)


class PanelPairing:
    """Одноразовый код выдаётся в доверенном локальном интерфейсе, вне HTTP."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: tuple[str, float] | None = None

    def issue(self) -> str:
        with self._lock:
            code = secrets.token_urlsafe(24)
            self._pending = code, time.monotonic() + 300
            return code

    def redeem(self, code: str) -> bool:
        with self._lock:
            if self._pending is None:
                return False
            expected, expires_at = self._pending
            if time.monotonic() >= expires_at:
                self._pending = None
                return False
            if not hmac.compare_digest(code.encode("utf-8"), expected.encode("ascii")):
                return False
            self._pending = None
            return True


def _hostname(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


class SessionGuard:
    def __init__(self, app: ASGIApp, *, token: str, origins: Iterable[str], hosts: Iterable[str] = DEFAULT_HOSTS) -> None:
        self.app = app
        self.token = token.encode()
        self.origins = {o.lower().rstrip("/") for o in origins}
        self.hosts = {h.lower() for h in hosts}

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        if _hostname(headers.get("host", "")) not in self.hosts:
            await self._reject(send, 403, "host_rejected", "Недопустимый адрес")
            return
        origin = headers.get("origin")
        if origin is not None and origin.lower().rstrip("/") not in self.origins:
            await self._reject(send, 403, "origin_rejected", "Запрос с чужой страницы отклонён")
            return
        path = scope.get("path", "")
        method = scope.get("method")
        public = (
            (method == "GET" and (path == "/taskpane" or path.startswith("/static/")))
            or (method == "POST" and path == "/session/pair")
        )
        if not public:
            supplied = headers.get(TOKEN_HEADER, "").encode()
            if not supplied or not hmac.compare_digest(supplied, self.token):
                await self._reject(send, 401, "token_required", "Нет токена сессии. Перезагрузите панель")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Any, status: int, code: str, message: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
