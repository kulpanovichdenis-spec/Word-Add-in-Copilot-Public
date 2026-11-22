"""Выполнение действия: папка → движок → промпт → запуск → проверка результата."""

from __future__ import annotations

import copy
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .actions.prompt import build_prompt, build_retry_prompt
from .actions.registry import ActionDef, ActionRegistry, RegistryError, load_registry
from .actions.schema import engine_schema, extract_json, validation_errors
from .config import ConfigStore, app_home, repo_root
from .dadata import DaDataClient
from .document_context import DocumentContextStore, legal_copilot_convert
from .engines.base import EngineError, EngineResult, EngineRunner
from .engines.manager import NOT_FOUND_MESSAGE, EngineManager
from .errors import ActionFailure
from .project import check_project
from .privacy import PrivacyPreview, PrivacySession, valid_identifier
from .privacy.anonymizer import extract_identifiers, split_document
from .privacy.workspace import isolated_project
from .security.credentials import CredentialStore
from .security.journal import Journal
from .sharepoint import SharePointTemplates, TypicalForm
from .tasks import Task

PUBLIC_CONTRACT_TYPES = frozenset({
    "поставка", "подряд", "оказание услуг", "лицензионный", "контрактное производство",
    "аренда", "дополнительное соглашение", "купля-продажа", "nda",
})
PUBLIC_ROLES = frozenset({
    "покупатель", "поставщик", "заказчик", "исполнитель", "лицензиар", "лицензиат",
    "арендодатель", "арендатор", "продавец", "подрядчик",
})

LAWYER_INSTRUCTION_LIMIT = 4_000

@dataclass
class RunPayload:
    action_id: str
    text: str
    context: dict[str, str] = field(default_factory=dict)
    document_name: str = ""
    document_scope: str = "default"
    template_id: str = ""


class ActionService:
    def __init__(self, config: ConfigStore, engines: EngineManager, journal: Journal,
                 documents: DocumentContextStore | None = None,
                 dadata: DaDataClient | None = None) -> None:
        self.config = config
        self.engines = engines
        self.journal = journal
        self.documents = documents
        self.templates = SharePointTemplates()
        credentials = CredentialStore()
        self.dadata = dadata or DaDataClient(
            credentials.get_dadata_api_key,
            credentials.get_dadata_secret_key,
        )
        self._lock = threading.Lock()
        self._registry = ActionRegistry([])
        self.registry_error = ""
        try:
            self.reload_actions()
        except RegistryError:
            pass  # причина — в registry_error, её показывают /health и tray

    # ---- реестр ----

    @property
    def registry(self) -> ActionRegistry:
        return self._registry

    def actions_path(self) -> Path:
        custom = self.config.settings.actions_path
        if custom:
            return Path(custom)
        # Свой реестр юриста в %LOCALAPPDATA% правится без прав администратора; иначе — поставляемый.
        personal = app_home() / "actions.yaml"
        return personal if personal.is_file() else repo_root() / "actions.yaml"

    def reload_actions(self) -> ActionRegistry:
        """Перечитать actions.yaml. При ошибке остаётся прежний реестр."""
        try:
            registry = load_registry(self.actions_path())
        except RegistryError as exc:
            self.registry_error = str(exc)
            raise
        with self._lock:
            self._registry = registry
            self.registry_error = ""
        return registry

    def project(self) -> Path | None:
        status = check_project(self.config.settings.project_path)
        return Path(status.path) if status.valid else None

    # ---- проверка запроса до постановки в очередь ----

    def prepare(self, payload: RunPayload) -> ActionDef:
        action = self.registry.get(payload.action_id)
        if action is None:
            raise ActionFailure("unknown_action", f"Действие «{payload.action_id}» не найдено. Перечитайте действия")
        if not payload.text.strip():
            message = "Документ пуст" if action.input == "document" else "Выделите пункт договора"
            raise ActionFailure("empty_text", message)
        if action.requiresInstruction:
            instruction = str(payload.context.get("lawyerInstruction") or "").strip()
            if not instruction:
                raise ActionFailure(
                    "instruction_required",
                    "Опишите, что именно нужно проверить или изменить в документе",
                )
            if len(instruction) > LAWYER_INSTRUCTION_LIMIT:
                raise ActionFailure(
                    "instruction_too_large",
                    f"Задача юриста не должна превышать {LAWYER_INSTRUCTION_LIMIT} знаков",
                )
        s = self.config.settings
        if action.has_capability("typical-template-read"):
            if not payload.template_id:
                raise ActionFailure("template_required", "Выберите типовую форму и подтвердите сверку")
            self.templates.selected(s.sharepoint_templates_url, payload.template_id)
        limit = s.max_document_chars if action.input == "document" else s.max_selection_chars
        if len(payload.text) > limit:
            raise ActionFailure("text_too_large", "Фрагмент слишком большой. Выделите конкретный пункт")
        status = check_project(s.project_path)
        if not status.valid:
            raise ActionFailure("project_invalid", status.message)
        check = self.registry.check(action, Path(status.path))
        if not check.available:
            raise ActionFailure("action_unavailable", check.problem)
        return action

    # ---- выполнение (в рабочем потоке очереди) ----

    def execute(self, task: Task, action: ActionDef, payload: RunPayload) -> dict[str, Any]:
        started = time.monotonic()
        engine_name = "-"
        outcome = "error"
        try:
            status = check_project(self.config.settings.project_path)
            if not status.valid:
                raise ActionFailure("project_invalid", status.message)
            project = Path(status.path)
            check = self.registry.check(action, project)
            if not check.available:
                raise ActionFailure("action_unavailable", check.problem)
            if action.id == "counterparty-check":
                engine_name = "local-dadata"
                task.set_progress("Запрашиваю DaData")
                identifier = str(payload.context.get("counterpartyIdentifier") or "").strip()
                if not identifier:
                    _, _, requisites = split_document(payload.text)
                    if not requisites.strip():
                        raise ActionFailure(
                            "requisites_section_not_found",
                            "Не удалось надёжно отделить раздел реквизитов. Введите ИНН или ОГРН вручную",
                        )
                    identifiers = extract_identifiers(requisites)
                    if len(identifiers) != 1:
                        raise ActionFailure(
                            "counterparty_id_required",
                            "Выберите один ИНН или ОГРН из раздела реквизитов",
                        )
                    identifier = identifiers[0]
                if not valid_identifier(identifier):
                    raise ActionFailure("invalid_counterparty_id", "ИНН или ОГРН не прошёл проверку контрольной суммы")
                # В DaData передаётся только выбранный публичный идентификатор.
                # Текст договора не используется для сверки и клиенту не передаётся.
                card = self.dadata.check(identifier)
                if check.schema is not None:
                    errors = validation_errors(card, check.schema)
                    if errors:
                        raise ActionFailure("dadata_invalid_response", "Локальная карточка DaData не прошла проверку", details=errors)
                outcome = "ok"
                return {"type": "card", "card": card, "engine": engine_name}

            runner = self._select_engine(action)
            engine_name = runner.name
            task.set_progress(f"Запускаю {runner.display_name}")

            privacy = PrivacySession()
            anonymized = privacy.anonymize(payload.text, whole_document=action.input == "document")
            if not anonymized.preview.safe_to_send:
                raise ActionFailure(
                    "privacy_boundary_uncertain",
                    anonymized.preview.warning,
                )
            safe_context = dict(payload.context)
            for key, allowed in (("contractType", PUBLIC_CONTRACT_TYPES), ("ourRole", PUBLIC_ROLES)):
                value = safe_context.get(key, "").strip().casefold()
                safe_context[key] = value if value in allowed else ""
            safe_context["counterpartyCheck"] = privacy.safe_counterparty_context(
                str(payload.context.get("counterpartyCheck") or "")
            )
            safe_context.pop("counterpartyIdentifier", None)
            if action.requiresInstruction:
                instruction = privacy.anonymize(
                    str(payload.context.get("lawyerInstruction") or "").strip(),
                    whole_document=False,
                )
                if not instruction.preview.safe_to_send:
                    raise ActionFailure(
                        "privacy_boundary_uncertain",
                        instruction.preview.warning,
                    )
                safe_context["lawyerInstruction"] = instruction.text
            else:
                safe_context.pop("lawyerInstruction", None)

            related_context = ""
            related_count = 0
            if (
                self.documents is not None
                and action.id != "counterparty-check"
                and action.resultType in ("card", "review", "checklist")
                and action.has_capability("related-documents-read")
            ):
                related_context, related_count = self.documents.prompt_context(
                    self.config.settings.max_related_document_chars,
                    privacy,
                    scope=payload.document_scope,
                )
            if related_count:
                task.set_progress(f"Добавляю связанные документы: {related_count}")
            template_text = ""
            template_form: TypicalForm | None = None
            if action.has_capability("typical-template-read"):
                task.set_progress("Загружаю выбранную типовую форму из SharePoint")
                template_form, template_text = self._read_template(project, payload.template_id, privacy)
            prompt = build_prompt(
                action,
                anonymized.text,
                safe_context,
                privacy.anonymize_name(payload.document_name, 0) if payload.document_name else "",
                related_documents=related_context,
                template_document=template_text,
            )
            with isolated_project(project, action.skill, action.capabilities) as runner_project:
                if action.resultType in ("card", "review", "checklist"):
                    assert check.schema is not None
                    result = self._structured(
                        task,
                        runner,
                        action,
                        runner_project,
                        prompt,
                        check.schema,
                        anonymized.text,
                    )
                    result = privacy.restore(result)
                else:
                    result = {"type": "text", "text": privacy.restore(self._run(task, runner, action, runner_project, prompt, None).text)}
            outcome = "ok"
            if template_form:
                result["template"] = template_form.public()
            return result | {"engine": runner.name}
        except ActionFailure as failure:
            outcome = failure.code
            raise
        except EngineError as exc:
            outcome = "cancelled" if exc.kind == "cancelled" else f"engine_{exc.kind}"
            if exc.kind == "cancelled":
                raise
            raise self._engine_failure(exc, engine_name) from exc
        finally:
            self.journal.record(
                action_id=action.id,
                text_length=len(payload.text),
                engine=engine_name,
                duration=time.monotonic() - started,
                result=outcome,
                usage=task.control.usage().public(),
            )

    def privacy_preview(self, text: str, *, whole_document: bool = True) -> PrivacyPreview:
        return PrivacySession().anonymize(text, whole_document=whole_document).preview

    def _select_engine(self, action: ActionDef) -> EngineRunner:
        try:
            runner, _ = self.engines.select_local_tools() if action.engine == "local-tools" else self.engines.select()
        except EngineError as exc:
            raise self._engine_failure(exc, "") from exc
        return runner

    def _engine_failure(self, exc: EngineError, engine_name: str) -> ActionFailure:
        display = self.engines.runner(engine_name).display_name if engine_name in ("openrouter", "codex", "claude") else "движок"
        if exc.kind == "not_found":
            return ActionFailure("engine_not_found", exc.message if engine_name else exc.message or NOT_FOUND_MESSAGE)
        if exc.kind == "auth":
            message = exc.message if not engine_name or engine_name == "openrouter" else f"Требуется вход в {display}. Выполните вход и повторите"
            return ActionFailure("engine_auth", message)
        if exc.kind == "limit":
            return ActionFailure("engine_limit", exc.message)  # текст движка без интерпретации (ТЗ 12)
        if exc.kind == "timeout":
            return ActionFailure("engine_timeout", f"{exc.message}. Повторите запрос или уменьшите фрагмент")
        # stderr движка в панель не отдаём: при ошибке API Codex печатает туда тело запроса с текстом договора.
        message = f"{display}: {exc.message}"
        if "newer version" in exc.message.lower():
            message += ". Обновите CLI движка или укажите другую модель в настройках приложения"
        return ActionFailure("engine_failed", message)

    def _run(self, task: Task, runner: EngineRunner, action: ActionDef, project: Path,
             prompt: str, schema: dict[str, Any] | None) -> EngineResult:
        return runner.run(
            prompt,
            project,
            schema,
            self.config.settings.long_timeout,
            task.set_progress,
            writable=action.writable,
            model=self.engines.model_for(runner.name),
            control=task.control,
        )

    def _read_template(self, project: Path, form_id: str, privacy: PrivacySession) -> tuple[TypicalForm, str]:
        settings = self.config.settings
        location, form = self.templates.selected(settings.sharepoint_templates_url, form_id)
        data = self.templates.download(location, form, settings.max_related_file_bytes)
        if not data:
            raise ActionFailure("template_unreadable", "Выбранная типовая форма пуста. Обновите список и выберите другой файл")
        converter = self.documents.converter if self.documents is not None else legal_copilot_convert
        try:
            with tempfile.TemporaryDirectory(prefix="word-addin-template-") as folder:
                path = Path(folder) / ("template" + Path(form.name).suffix.lower())
                path.write_bytes(data)
                markdown = str(converter(project, path).get("markdown") or "").strip()
        except Exception:
            raise ActionFailure("template_unreadable", "Не удалось извлечь текст типовой формы. Выберите другой файл") from None
        if not markdown or len(markdown) > settings.max_related_document_chars:
            raise ActionFailure("template_unreadable", "Типовая форма пуста или её текст превышает лимит сверки")
        protected = privacy.anonymize(markdown, whole_document=True)
        if not protected.preview.safe_to_send or not protected.text.strip():
            raise ActionFailure("privacy_boundary_uncertain", "Не удалось безопасно подготовить текст типовой формы к отправке модели")
        return form, protected.text

    def _structured(self, task: Task, runner: EngineRunner, action: ActionDef, project: Path,
                    prompt: str, schema: dict[str, Any], source_text: str) -> dict[str, Any]:
        engine_side = engine_schema(schema)
        result = self._run(task, runner, action, project, prompt, engine_side)
        value, errors = parse_structured(result, schema)
        if not errors:
            errors = structured_semantic_errors(action, value, source_text, schema)
        if errors:
            # Одна повторная попытка с уточняющей инструкцией (ТЗ 6.3).
            task.set_progress("Ответ не прошёл проверку — повторяю запрос")
            result = self._run(task, runner, action, project, build_retry_prompt(prompt, errors), engine_side)
            value, errors = parse_structured(result, schema)
            if not errors:
                errors = structured_semantic_errors(action, value, source_text, schema)
                if errors and action.resultType == "review":
                    value, errors = downgrade_unbound_review_findings(action, value, source_text)
                    if not errors:
                        errors = validation_errors(value, schema)
            if errors:
                raise ActionFailure(
                    "invalid_response",
                    "Не удалось подготовить результат для безопасной работы с документом; показан ответ как есть",
                    raw=result.text,
                    details=errors,
                )
        key = {"card": "card", "review": "review", "checklist": "checklist"}[action.resultType]
        return {"type": action.resultType, key: value}

def parse_structured(result: EngineResult, schema: dict[str, Any]) -> tuple[Any, list[str]]:
    value = result.json if isinstance(result.json, dict) else None
    if value is None:
        try:
            value = extract_json(result.text)
        except ValueError as exc:
            return None, [f"(ответ): {exc}"]
    return value, validation_errors(value, schema)


def structured_semantic_errors(
    action: ActionDef,
    value: Any,
    source_text: str,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Проверки, которые JSON Schema не может выразить без исходного документа."""
    if action.resultType == "checklist" and isinstance(value, dict) and schema:
        findings = value.get("findings")
        expected = (
            schema.get("properties", {})
            .get("findings", {})
            .get("items", {})
            .get("properties", {})
            .get("checkId", {})
            .get("enum", [])
        )
        if isinstance(findings, list) and isinstance(expected, list):
            actual = [item.get("checkId") for item in findings if isinstance(item, dict)]
            if actual != expected:
                return [
                    "findings/checkId: каждый пункт чек-листа должен присутствовать "
                    "ровно один раз в порядке схемы"
                ]
        return []
    if action.resultType != "review" or not isinstance(value, dict):
        return []
    findings = value.get("findings")
    if not isinstance(findings, list):
        return []  # Структурную ошибку уже вернула JSON Schema.

    errors: list[str] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            continue
        expected_id = f"R{index}"
        if finding.get("id") != expected_id:
            errors.append(f"findings/{index - 1}/id: замечания должны идти последовательно R1, R2, …")
        quote = finding.get("sourceText")
        if not isinstance(quote, str) or not quote:
            continue
        occurrences = source_text.count(quote)
        if occurrences == 0:
            errors.append(
                f"findings/{index - 1}/sourceText: дословный фрагмент не найден во входном договоре"
            )
        elif occurrences > 1:
            errors.append(
                f"findings/{index - 1}/sourceText: фрагмент не уникален во входном договоре ({occurrences} совпадения)"
            )
    return errors


_BINDING_WARNING = (
    "Автопривязка Word недоступна: модель не вернула точный уникальный "
    "фрагмент исходного документа. Замечание сохранено без автоматической правки."
)


def downgrade_unbound_review_findings(
    action: ActionDef,
    value: Any,
    source_text: str,
) -> tuple[Any, list[str]]:
    """Сохранить корректный обзор, отключив только небезопасные привязки.

    После одной повторной попытки неточная или неуникальная цитата не должна
    уничтожать весь полезный результат. Пустой sourceText штатно отключает
    поиск, замену и комментарий по диапазону в Word.
    """
    if action.resultType != "review" or not isinstance(value, dict):
        return value, structured_semantic_errors(action, value, source_text)
    findings = value.get("findings")
    if not isinstance(findings, list):
        return value, []

    sanitized = copy.deepcopy(value)
    remaining: list[str] = []
    for index, finding in enumerate(sanitized["findings"], start=1):
        if not isinstance(finding, dict):
            continue
        if finding.get("id") != f"R{index}":
            remaining.append(f"findings/{index - 1}/id: замечания должны идти последовательно R1, R2, …")
        quote = finding.get("sourceText")
        if not isinstance(quote, str) or not quote:
            continue
        if source_text.count(quote) == 1:
            continue
        finding["sourceText"] = ""
        note = str(finding.get("lawyerNote") or "").strip()
        separator = "\n" if note else ""
        available = max(0, 1500 - len(_BINDING_WARNING) - len(separator))
        note = note[:available].rstrip()
        finding["lawyerNote"] = f"{note}{separator if note else ''}{_BINDING_WARNING}"
    return sanitized, remaining


# Совместимость для импортов расширений и старых тестовых интеграций.
parse_card = parse_structured
