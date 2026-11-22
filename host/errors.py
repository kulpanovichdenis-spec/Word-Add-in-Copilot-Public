"""Ошибки действий с понятными юристу сообщениями (ТЗ, раздел 12)."""

from __future__ import annotations

from typing import Any

# HTTP-статус для ошибок, возникающих до запуска задачи.
HTTP_STATUS = {
    "unknown_action": 404,
    "empty_text": 400,
    "text_too_large": 413,
    "action_unavailable": 409,
    "project_invalid": 409,
    "template_required": 422,
    "template_not_configured": 409,
    "template_invalid_url": 422,
    "template_selection_stale": 409,
    "template_access_denied": 403,
    "template_not_found": 404,
    "template_unreachable": 502,
    "template_bad_catalog": 502,
    "template_too_large": 413,
}


class ActionFailure(Exception):
    def __init__(self, code: str, message: str, *, raw: str = "", details: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw = raw
        self.details = details or []

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.raw:
            data["raw"] = self.raw
        if self.details:
            data["details"] = self.details
        return data
