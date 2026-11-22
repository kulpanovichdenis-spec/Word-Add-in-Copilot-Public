"""Endpoints панели и tray (ТЗ 6.1)."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..engines.base import EngineError
from ..document_context import DocumentContextError
from ..project import check_project
from ..service import RunPayload

if TYPE_CHECKING:
    from ..app import HostState

router = APIRouter()
BACKGROUND_MESSAGE = "Действие выполняется дольше обычного, переведено в фоновый режим"


def _host(request: Request) -> "HostState":
    return request.app.state.host


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _document_scope(request: Request) -> str:
    """Идентификатор открытого Word-документа для изоляции комплекта файлов."""
    value = request.headers.get("X-Document-Scope", "default").strip()
    if not value or len(value) > 128 or not all(character.isalnum() or character in "_-" for character in value):
        return "default"
    return value


class RunContext(BaseModel):
    contractType: str = Field("", max_length=200)
    ourRole: str = Field("", max_length=200)
    counterpartyIdentifier: str = Field("", max_length=15, pattern=r"^(?:|[0-9]{10}|[0-9]{12}|[0-9]{13}|[0-9]{15})$")
    counterpartyCheck: str = Field("", max_length=20_000)
    lawyerInstruction: str = Field("", max_length=4_000)


class PairRequest(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")


class RunRequest(BaseModel):
    actionId: str = Field(max_length=100)
    text: str
    context: RunContext = Field(default_factory=RunContext)
    documentName: str = Field("", max_length=500)
    templateId: str = Field("", pattern=r"^(?:[0-9a-f]{24})?$")


class PrivacyPreviewRequest(BaseModel):
    text: str
    wholeDocument: bool = True


class DocumentPatch(BaseModel):
    role: str | None = Field(None, max_length=50)
    included: bool | None = None


def health_payload(host: "HostState") -> dict[str, Any]:
    s = host.config.settings
    project = check_project(s.project_path)
    statuses = host.engines.statuses()
    try:
        runner, status = host.engines.select()
        engine, available, version, message = runner.name, True, status.version, ""
    except EngineError as exc:
        engine = s.engine if s.engine != "auto" else ""
        available, message = False, exc.message
        version = statuses[engine].version if engine in statuses else ""
    return {
        "status": "ok",
        "version": __version__,
        "engine": engine,
        "engineSetting": s.engine,
        "engineAvailable": available,
        "engineVersion": version,
        "engineMessage": message,
        "engines": {name: asdict(st) for name, st in statuses.items()},
        "projectPath": s.project_path,
        "projectValid": project.valid,
        "projectMessage": project.message,
        "actionsLoaded": len(host.service.registry),
        "actionsError": host.service.registry_error,
        "dadataConfigured": host.service.dadata.configured(),
    }


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    return health_payload(_host(request))


@router.post("/session/pair")
def pair(request: Request, body: PairRequest) -> JSONResponse:
    host = _host(request)
    if not host.pairing.redeem(body.code):
        return _error(403, "pairing_invalid", "Код неверен, истёк или уже использован. Создайте новый код в приложении.")
    return JSONResponse({"token": host.token}, headers={"Cache-Control": "no-store"})


@router.get("/actions")
def actions(request: Request) -> list[dict[str, Any]]:
    service = _host(request).service
    return service.registry.descriptors(service.project())


@router.get("/documents")
def documents(request: Request) -> list[dict[str, Any]]:
    return _host(request).documents.list(_document_scope(request))


@router.get("/templates")
def templates(request: Request, response: Response) -> dict[str, Any]:
    host = _host(request)
    forms = host.service.templates.catalog(host.config.settings.sharepoint_templates_url)
    response.headers["Cache-Control"] = "no-store"
    return {"forms": [form.public() for form in forms]}


@router.post("/documents")
async def add_document(request: Request, file: UploadFile = File(...),
                       role: str = Form("reference")) -> Any:
    host = _host(request)
    project = host.service.project()
    if project is None:
        return _error(422, "project_invalid", "Папка Legal Copilot не настроена")
    limit = host.config.settings.max_related_file_bytes
    filename = file.filename or ""
    try:
        content = await file.read(limit + 1)
    finally:
        await file.close()
    try:
        item = await run_in_threadpool(
            host.documents.add,
            project,
            filename,
            content,
            role,
            _document_scope(request),
        )
    except DocumentContextError as exc:
        return _error(exc.status, exc.code, exc.message)
    return JSONResponse(status_code=201, content=item)


@router.patch("/documents/{document_id}")
def update_document(request: Request, document_id: str, patch: DocumentPatch) -> Any:
    try:
        return _host(request).documents.update(
            document_id,
            scope=_document_scope(request),
            role=patch.role,
            included=patch.included,
        )
    except DocumentContextError as exc:
        return _error(exc.status, exc.code, exc.message)


@router.delete("/documents/{document_id}")
def delete_document(request: Request, document_id: str) -> Any:
    try:
        _host(request).documents.remove(document_id, scope=_document_scope(request))
    except DocumentContextError as exc:
        return _error(exc.status, exc.code, exc.message)
    return {"removed": document_id}


@router.post("/run")
def run(request: Request, body: RunRequest) -> Any:
    host = _host(request)
    payload = RunPayload(
        body.actionId,
        body.text,
        body.context.model_dump(),
        body.documentName,
        _document_scope(request),
        body.templateId,
    )
    action = host.service.prepare(payload)
    task = host.tasks.submit(action.id, lambda t: host.service.execute(t, action, payload))
    if action.duration == "long":
        return JSONResponse(status_code=202, content=task.to_dict())
    if task.wait(host.config.settings.short_timeout):
        return task.to_dict()
    return JSONResponse(status_code=202, content=task.to_dict() | {"message": BACKGROUND_MESSAGE})


@router.post("/privacy/preview")
def privacy_preview(request: Request, body: PrivacyPreviewRequest) -> Any:
    host = _host(request)
    if len(body.text) > host.config.settings.max_document_chars:
        return _error(413, "text_too_large", "Документ слишком большой для локального анализа")
    return host.service.privacy_preview(body.text, whole_document=body.wholeDocument).public()


@router.get("/task/{task_id}")
def task_status(request: Request, task_id: str) -> Any:
    task = _host(request).tasks.get(task_id)
    if task is None:
        return _error(404, "task_not_found", "Задача не найдена")
    return task.to_dict()


@router.post("/task/{task_id}/cancel")
def task_cancel(request: Request, task_id: str) -> Any:
    task = _host(request).tasks.cancel(task_id)
    if task is None:
        return _error(404, "task_not_found", "Задача не найдена")
    return task.to_dict()


@router.get("/journal")
def journal(request: Request) -> dict[str, Any]:
    return {"entries": _host(request).journal.recent()}


@router.get("/taskpane", response_class=HTMLResponse)
def taskpane(request: Request) -> HTMLResponse:
    host = _host(request)
    page = host.addin_dir / "taskpane.html"
    if not page.is_file():
        return HTMLResponse("<h1>Панель не собрана</h1><p>Выполните сборку addin/ (npm run build).</p>", status_code=500)
    html = (
        page.read_text("utf-8")
        .replace("{{APP_VERSION}}", __version__)
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
