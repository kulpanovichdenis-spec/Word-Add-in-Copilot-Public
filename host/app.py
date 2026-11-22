"""Сборка приложения хоста: состояние, middleware безопасности, маршруты."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import ConfigStore, repo_root
from .document_context import DocumentContextStore
from .engines.manager import EngineManager, RunnerFactory
from .errors import ActionFailure
from .security.journal import Journal
from .security.session import DEFAULT_HOSTS, PanelPairing, SessionGuard, new_token
from .service import ActionService
from .tasks import TaskManager


@dataclass
class HostState:
    config: ConfigStore
    engines: EngineManager
    service: ActionService
    tasks: TaskManager
    journal: Journal
    documents: DocumentContextStore
    token: str
    addin_dir: Path
    scheme: str = "https"
    pairing: PanelPairing = field(default_factory=PanelPairing)


def default_addin_dir() -> Path:
    dist = repo_root() / "addin" / "dist"
    return dist if (dist / "taskpane.html").is_file() else repo_root() / "addin" / "src" / "taskpane"


def build_state(
    config: ConfigStore | None = None,
    *,
    engine_factory: RunnerFactory | None = None,
    journal_path: Path | None = None,
    token: str | None = None,
    addin_dir: Path | None = None,
    scheme: str = "https",
) -> HostState:
    config = config or ConfigStore()
    engines = EngineManager(config, engine_factory) if engine_factory else EngineManager(config)
    journal = Journal(journal_path)
    documents = DocumentContextStore(
        max_files=config.settings.max_related_files,
        max_file_bytes=config.settings.max_related_file_bytes,
    )
    return HostState(
        config=config,
        engines=engines,
        service=ActionService(config, engines, journal, documents),
        tasks=TaskManager(config.settings.max_parallel),
        journal=journal,
        documents=documents,
        token=token or new_token(),
        addin_dir=addin_dir or default_addin_dir(),
        scheme=scheme,
    )


def create_app(state: HostState, *, hosts: Iterable[str] = DEFAULT_HOSTS) -> FastAPI:
    app = FastAPI(title="Word-Add-in-Copilot-Public Host", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.host = state
    port = state.config.settings.port
    origins = [f"{state.scheme}://localhost:{port}", f"{state.scheme}://127.0.0.1:{port}"]
    app.add_middleware(SessionGuard, token=state.token, origins=origins, hosts=hosts)

    @app.middleware("http")
    async def disable_panel_asset_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ActionFailure)
    async def _failure(_: Request, failure: ActionFailure) -> JSONResponse:
        return JSONResponse(status_code=failure.http_status, content={"error": failure.to_dict()})

    app.include_router(router)
    if state.addin_dir.is_dir():
        app.mount("/static", StaticFiles(directory=state.addin_dir), name="static")
    return app
