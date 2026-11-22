"""Прямой локальный read-only клиент DaData."""

from __future__ import annotations

import json
import os
import re
import ssl
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

import httpx

from .errors import ActionFailure
from .privacy.anonymizer import valid_identifier

API_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


@lru_cache(maxsize=1)
def system_trust_ssl_context() -> ssl.SSLContext:
    """Контекст TLS с публичными и корпоративными корнями Windows.

    httpx по умолчанию использует certifi. В корпоративной сети HTTPS может
    проходить через доверенный Windows прокси-сертификат, которого в certifi
    нет. Добавляем системные корни, не отключая проверку цепочки и имени узла.
    """
    context = ssl.create_default_context()
    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return context
    certificates: list[str] = []
    for certificate, encoding, _trust in ssl.enum_certificates("ROOT"):
        if encoding == "x509_asn":
            certificates.append(ssl.DER_cert_to_PEM_cert(certificate))
    if certificates:
        context.load_verify_locations(cadata="\n".join(certificates))
    # Python 3.14 включает OpenSSL X509_STRICT. Некоторые корпоративные CA,
    # которым доверяет Windows/Schannel, выпущены без critical-флага у Basic
    # Constraints. Снимаем только несовместимый strict-режим; проверка цепочки,
    # доверенного корня, срока и имени suggestions.dadata.ru остаётся включена.
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        context.verify_flags &= ~strict
    return context


@dataclass
class DaDataClient:
    api_key: Callable[[], str]
    secret_key: Callable[[], str]
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None
    # Последний полный ответ держим только в памяти локального хоста. В API
    # панели возвращается нормализованная карточка, а в LLM — лишь статусные флаги.
    raw_response: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def configured(self) -> bool:
        return bool(self.api_key().strip() and self.secret_key().strip())

    def lookup(self, identifier: str) -> dict[str, Any]:
        identifier = re.sub(r"\D", "", identifier)
        if not valid_identifier(identifier):
            raise ActionFailure("invalid_counterparty_id", "ИНН или ОГРН не прошёл проверку контрольной суммы")
        api_key = self.api_key().strip()
        secret_key = self.secret_key().strip()
        if not api_key or not secret_key:
            raise ActionFailure(
                "dadata_not_configured",
                "Ключи DaData не заданы. Откройте настройки Word-Add-in-Copilot-Public",
            )
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=False,
                verify=system_trust_ssl_context(),
            ) as client:
                response = client.post(
                    API_URL,
                    json={"query": identifier, "count": 1},
                    headers={
                        "Authorization": f"Token {api_key}",
                        "X-Secret": secret_key,
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise ActionFailure("dadata_timeout", "DaData не ответила вовремя") from exc
        except httpx.HTTPError as exc:
            raise ActionFailure("dadata_unavailable", "Не удалось подключиться к DaData") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ActionFailure("dadata_invalid_response", "Ответ DaData превышает допустимый размер")
        if response.status_code == 429:
            raise ActionFailure("dadata_limit", "Превышен лимит запросов DaData")
        if response.status_code >= 400:
            raise ActionFailure("dadata_unavailable", f"DaData вернула HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ActionFailure("dadata_invalid_response", "DaData вернула некорректный ответ") from exc
        suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
        if not isinstance(suggestions, list):
            raise ActionFailure("dadata_invalid_response", "В ответе DaData отсутствует список результатов")
        self.raw_response = payload
        return suggestions[0] if suggestions and isinstance(suggestions[0], dict) else {}

    def check(self, identifier: str) -> dict[str, Any]:
        suggestion = self.lookup(identifier)
        if not suggestion:
            return _empty_card(identifier, "не найдено", "По идентификатору ничего не найдено в DaData.")
        data = suggestion.get("data") if isinstance(suggestion.get("data"), dict) else {}
        name = data.get("name") if isinstance(data.get("name"), dict) else {}
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        address = data.get("address") if isinstance(data.get("address"), dict) else {}
        management = data.get("management") if isinstance(data.get("management"), dict) else {}
        company = {
            "fullName": str(name.get("full_with_opf") or suggestion.get("value") or ""),
            "shortName": str(name.get("short_with_opf") or suggestion.get("value") or ""),
            "inn": str(data.get("inn") or ""),
            "kpp": str(data.get("kpp") or ""),
            "ogrn": str(data.get("ogrn") or ""),
            "status": str(state.get("status") or ""),
            "legalAddress": str(address.get("unrestricted_value") or address.get("value") or ""),
            "management": _management(management),
        }
        return {
            "identifier": re.sub(r"\D", "", identifier),
            "source": "DaData",
            "lookupStatus": "найдено",
            "company": company,
            "summary": "Контрагент найден в DaData.",
            "lawyerNote": "Результат получен напрямую из DaData. Сверка с договором не выполнялась.",
        }


def _management(value: dict[str, Any]) -> str:
    name = str(value.get("name") or "").strip()
    post = str(value.get("post") or "").strip()
    return ", ".join(item for item in (name, post) if item)


def _empty_card(identifier: str, status: str, summary: str) -> dict[str, Any]:
    return {
        "identifier": re.sub(r"\D", "", identifier),
        "source": "DaData",
        "lookupStatus": status,
        "company": {key: "" for key in ("fullName", "shortName", "inn", "kpp", "ogrn", "status", "legalAddress", "management")},
        "summary": summary,
        "lawyerNote": "Результат получен напрямую из DaData. Сверка с договором не выполнялась.",
    }
