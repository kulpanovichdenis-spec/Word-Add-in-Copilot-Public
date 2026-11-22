"""Схема ответа: извлечение из markdown-файла legal-copilot, разбор и валидация ответа движка (ТЗ 6.3)."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError as JsonSchemaError

_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.S)
_WRAPPED = re.compile(r"^```(?:json)?[ \t]*\r?\n(.*?)\r?\n?```$", re.S)

# Ключи, которые structured outputs OpenAI не поддерживает. Из схемы для движка они
# убираются, а полная схема применяется при валидации на бэкенде.
_ENGINE_UNSUPPORTED = {"minLength", "maxLength", "$schema"}


class SchemaError(Exception):
    pass


def load_schema_file(path: Path) -> dict[str, Any]:
    """Читает JSON-схему из .json или из первого подходящего ```json-блока в .md."""
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        raise SchemaError(f"Файл схемы не найден: {path.name}") from exc

    schema: Any = None
    if path.suffix.lower() == ".json":
        try:
            schema = json.loads(text)
        except ValueError as exc:
            raise SchemaError(f"Файл схемы {path.name} — не JSON") from exc
    else:
        for block in _FENCE.findall(text):
            try:
                candidate = json.loads(block)
            except ValueError:
                continue
            if isinstance(candidate, dict) and ("$schema" in candidate or "properties" in candidate):
                schema = candidate
                break
    if not isinstance(schema, dict):
        raise SchemaError(f"В файле {path.name} нет JSON-схемы")
    try:
        Draft202012Validator.check_schema(schema)
    except JsonSchemaError as exc:
        raise SchemaError(f"Схема в {path.name} некорректна: {exc.message}") from exc
    return schema


def engine_schema(schema: dict[str, Any]) -> dict[str, Any]:
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _ENGINE_UNSUPPORTED}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(copy.deepcopy(schema))


def extract_json(text: str) -> Any:
    """Достаёт JSON из ответа: чистый JSON, JSON в markdown-обёртке или JSON внутри текста."""
    s = text.strip()
    if not s:
        raise ValueError("пустой ответ")
    wrapped = _WRAPPED.match(s)
    if wrapped:
        s = wrapped.group(1).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(s[start : end + 1])
        except ValueError as exc:
            raise ValueError(f"JSON не разобран: {exc}") from exc
    raise ValueError("в ответе нет JSON")


def validation_errors(obj: Any, schema: dict[str, Any], limit: int = 10) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(obj), key=lambda e: [str(p) for p in e.path])
    return [f"{'/'.join(map(str, e.path)) or '(корень)'}: {e.message}" for e in errors[:limit]]
