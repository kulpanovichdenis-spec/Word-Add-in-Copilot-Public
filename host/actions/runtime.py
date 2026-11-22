"""Исполняемый контракт возможностей для Word-адаптеров legal-copilot."""

from __future__ import annotations

from typing import Iterable, Literal

RuntimeCapability = Literal[
    "legal-methodology-read",
    "clause-cards-read",
    "contract-checklist-read",
    "related-documents-read",
    "typical-template-read",
    "counterparty-summary-read",
]

RUNTIME_CONTRACT_VERSION = "word-review.v1"
RUNTIME_MANIFEST_PATH = "references/word-addin-runtime.json"

CAPABILITY_DESCRIPTIONS: dict[str, str] = {
    "legal-methodology-read": "чтение локальных references с методологией и шкалой рисков",
    "clause-cards-read": "чтение локальных карточек договоров и одобренных оговорок",
    "contract-checklist-read": "чтение корпоративного чек-листа договоров и его схемы ответа",
    "related-documents-read": "анализ переданных хостом обезличенных связанных документов",
    "typical-template-read": "сверка с одной выбранной юристом и обезличенной типовой формой; доступа к SharePoint у модели нет",
    "counterparty-summary-read": "использование обезличенной сводки локальной проверки DaData",
}


def build_runtime_contract(capabilities: Iterable[str]) -> str:
    granted = tuple(dict.fromkeys(capabilities))
    allowed = "; ".join(
        f"`{name}` — {CAPABILITY_DESCRIPTIONS[name]}" for name in granted
    ) or "нет дополнительных возможностей"
    return (
        f"Контракт среды Word Add-in: {RUNTIME_CONTRACT_VERSION}.\n"
        f"Разрешённые возможности (исчерпывающий allowlist): {allowed}.\n"
        f"Состав доступных локальных файлов перечислен в `{RUNTIME_MANIFEST_PATH}`.\n"
        "Хост допускает запуск модели только после локальной анонимизации и проверки границы данных; "
        "полученные плейсхолдеры считаются непрозрачными.\n"
        "Внешние сайты, MCP, базы данных и сетевые инструменты недоступны: не вызывай их и не имитируй их результаты. "
        "Запись файлов, команды оболочки, изменение `workflow/` и восстановление скрытых данных запрещены. "
        "Если общие правила проекта требуют недоступную возможность, не выполняй этот шаг: отрази неопределённость "
        "в предусмотренном схемой текстовом поле маркером `[ИНСТРУМЕНТ НЕДОСТУПЕН]` или `[ПРОВЕРИТЬ НОРМУ]`. "
        "Для режима, разрешений и формата ответа этот контракт конкретизирует общие правила проекта; "
        "правила конфиденциальности, запрет выдумывания и human review сохраняются полностью."
    )
