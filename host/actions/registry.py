"""Реестр действий actions.yaml (ТЗ, раздел 8).

Ошибки структуры (битый YAML, дубли id, лишние поля) — отказ загрузки целиком.
Проблемы, зависящие от папки юриста (нет навыка, нет файла схемы), делают
недоступным одно действие: кнопка видна, но неактивна и объясняет причину.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .schema import SchemaError, load_schema_file
from .runtime import RuntimeCapability


class RegistryError(Exception):
    pass


class ActionDef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    label: str = Field(min_length=1)
    skill: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    input: Literal["selection", "paragraph", "document"]
    resultType: Literal["card", "review", "checklist", "file", "text"]
    schema_path: str | None = Field(None, alias="schema")
    duration: Literal["short", "long"] = "short"
    requiresConfirmation: bool = False
    requiresInstruction: bool = False
    autoApply: bool = False
    engine: Literal["default", "local-tools"] = "default"
    access: Literal["read", "write"] | None = None
    description: str = ""
    capabilities: tuple[RuntimeCapability, ...] = ()
    required_files: tuple[str, ...] = Field((), alias="requiredFiles")

    @model_validator(mode="after")
    def _structured_result_needs_schema(self) -> "ActionDef":
        if self.resultType in ("card", "review", "checklist") and not self.schema_path:
            raise ValueError("для resultType: card, review или checklist обязателен schema")
        if self.autoApply and self.resultType != "review":
            raise ValueError("autoApply допустим только для resultType: review")
        if "typical-template-read" in self.capabilities and (
            self.input != "document" or self.resultType != "review" or self.autoApply
        ):
            raise ValueError("typical-template-read требует обзор документа без автоматического внесения правок")
        if self.requiresInstruction:
            if self.input != "document" or self.resultType != "review":
                raise ValueError(
                    "requiresInstruction допустим только для input: document и resultType: review"
                )
            if self.requiresConfirmation:
                raise ValueError(
                    "requiresInstruction несовместим с requiresConfirmation: форма задачи уже подтверждает запуск"
                )
        if self.resultType == "file" or self.access == "write":
            raise ValueError(
                "действия с записью и resultType: file отключены политикой безопасности; "
                "используйте card, review или text"
            )
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities не должны повторяться")
        if len(set(self.required_files)) != len(self.required_files):
            raise ValueError("requiredFiles не должны повторяться")
        return self

    @property
    def writable(self) -> bool:
        return False

    def has_capability(self, capability: RuntimeCapability) -> bool:
        return capability in self.capabilities


@dataclass
class ActionCheck:
    problem: str = ""
    schema: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        return not self.problem


def _inside(root: Path, relative: str) -> Path | None:
    target = (root / relative).resolve()
    return target if target.is_relative_to(root.resolve()) else None


class ActionRegistry:
    def __init__(self, actions: list[ActionDef], source: Path | None = None) -> None:
        self.actions = actions
        self.source = source
        self._by_id = {a.id: a for a in actions}

    def __len__(self) -> int:
        return len(self.actions)

    def get(self, action_id: str) -> ActionDef | None:
        return self._by_id.get(action_id)

    def check(self, action: ActionDef, project: Path | None) -> ActionCheck:
        """Проверка действия против папки юриста: есть ли навык и схема."""
        if project is None:
            return ActionCheck("Папка legal-copilot не настроена")
        skill_file = project / "skills" / action.skill / "SKILL.md"
        if not skill_file.is_file():
            return ActionCheck(f"В папке legal-copilot нет навыка skills/{action.skill}/SKILL.md")
        for relative in action.required_files:
            required = _inside(project, relative)
            if required is None:
                return ActionCheck(f"Путь обязательного файла выходит за пределы папки проекта: {relative}")
            if not required.is_file():
                return ActionCheck(f"В папке legal-copilot нет обязательного файла {relative}")
        if not action.schema_path:
            return ActionCheck()
        schema_file = _inside(project, action.schema_path)
        if schema_file is None:
            return ActionCheck(f"Путь к схеме выходит за пределы папки проекта: {action.schema_path}")
        try:
            return ActionCheck(schema=load_schema_file(schema_file))
        except SchemaError as exc:
            return ActionCheck(str(exc))

    def descriptors(self, project: Path | None) -> list[dict[str, Any]]:
        result = []
        for action in self.actions:
            check = self.check(action, project)
            result.append(
                {
                    "id": action.id,
                    "label": action.label,
                    "description": action.description,
                    "input": action.input,
                    "resultType": action.resultType,
                    "expectedDuration": action.duration,
                    "requiresConfirmation": action.requiresConfirmation,
                    "requiresInstruction": action.requiresInstruction,
                    "autoApply": action.autoApply,
                    "available": check.available,
                    "problem": check.problem,
                }
            )
        return result


def load_registry(path: Path) -> ActionRegistry:
    try:
        data = yaml.safe_load(path.read_text("utf-8"))
    except OSError as exc:
        raise RegistryError(f"Не найден реестр действий: {path}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"Ошибка YAML в {path.name}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise RegistryError(f"{path.name}: ожидается ключ actions со списком действий")

    actions: list[ActionDef] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["actions"], start=1):
        try:
            action = ActionDef.model_validate(raw)
        except ValidationError as exc:
            name = raw.get("id") if isinstance(raw, dict) else None
            details = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'действие'}: {e['msg']}" for e in exc.errors())
            raise RegistryError(f"{path.name}, действие #{index}{f' ({name})' if name else ''}: {details}") from exc
        if action.id in seen:
            raise RegistryError(f"{path.name}: повторяется id «{action.id}»")
        seen.add(action.id)
        actions.append(action)
    return ActionRegistry(actions, path)
