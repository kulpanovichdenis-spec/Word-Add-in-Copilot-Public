"""Детерминированная локальная анонимизация договоров и дополнительных соглашений.

Модуль не вызывает сеть и не использует LLM. Карта замен существует только в памяти
одного запроса. Суммы и даты намеренно сохраняются для юридического анализа.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_SPACE = r"[ \t\u00a0]"
_LINE_START = r"(?:\A|(?<=[\r\n]))"
_LINE_END = r"(?=[\r\n]|\Z)"
_SECTION_NUMBER = (
    r"(?:(?:(?:раздел|статья|section|article)\s*)?"
    r"(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.)]?\s*)?"
)
_PARTY_NAMES = (
    r"(?:сторон|поставщика|покупателя|продавца|заказчика|исполнителя|"
    r"лицензиара|лицензиата|арендодателя|арендатора|подрядчика)"
)
_REQ_HEADING = re.compile(
    rf"(?i){_LINE_START}[ \t\u00a0|#*_\-–—]*{_SECTION_NUMBER}[ \t\u00a0*_]*"
    rf"(?:"
    rf"(?:(?:юридические\s+)?адреса|местонахождение)"
    rf"(?:\s*(?:,|и)\s*(?:(?:банковские|прочие|иные)\s+)?реквизиты)?"
    rf"(?:\s+и\s+подписи)?(?:\s+{_PARTY_NAMES})?"
    rf"|(?:банковские\s+)?реквизиты(?:\s+и\s+подписи)?(?:\s+{_PARTY_NAMES})?"
    rf"|подписи(?:\s+и\s+(?:банковские\s+)?реквизиты)?(?:\s+{_PARTY_NAMES})?"
    r"|signatures?\b|bank\s+details|details\s+of\s+the\s+parties|"
    r"addresses\s+and\s+details|in\s+witness\s+whereof"
    rf")[ \t\u00a0|:;,.№#*_()\-–—]{{0,40}}{_LINE_END}"
)
_GENERAL_REQUISITES = re.compile(r"(?is)(?=.*\bсторон\b)(?=.*\bреквизиты\b)")
_SIGNATURE_FALLBACK = re.compile(
    rf"(?i){_LINE_START}[ \t\u00a0|]*(?:за\s+(?:заказчика|поставщика|исполнителя|покупателя|продавца)|"
    r"от\s+имени\s+стороны|authorized\s+signature|подпись)\s*[:_]"
)
_PREAMBLE = re.compile(
    r"(?i)(?:именуем\w*\s+в\s+дальнейшем|в\s+лице\b|действующ\w*\s+на\s+основании|"
    r"hereinafter\s+referred\s+to\s+as|represented\s+by)"
)
_NUMBERED_SECTION = re.compile(r"^\s*(?:раздел\s+)?1(?:\.\d+)*[.)]?\s+\S", re.I)
_RU_LEGAL_FORM = (
    r"(?:ООО|ОАО|ЗАО|ПАО|НАО|АО|АНО|ФГУП|ГУП|МУП|НКО|ИП|"
    r"общество\s+с\s+ограниченной\s+ответственностью|"
    r"публичное\s+акционерное\s+общество|непубличное\s+акционерное\s+общество|"
    r"акционерное\s+общество|индивидуальный\s+предприниматель)"
)
_COMPANY_MARKER = re.compile(
    rf"(?i)\b(?:{_RU_LEGAL_FORM}|LLC|LTD\.?|INC\.?)\b"
)

_EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w-])")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\[\]]+")
_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)")
_SNILS = re.compile(r"(?i)(?:(?:СНИЛС|SNILS)\s*[:№#-]?\s*)(\d{3}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2})")
_PASSPORT = re.compile(
    r"(?i)(?:(?:паспорт|passport)\s*(?:серия|series)?\s*[:№#-]?\s*)"
    r"(\d{2}[ -]?\d{2}[ №#-]*\d{6})"
)
_INN_LABEL = re.compile(r"(?i)(?:(?:ИНН|INN)\s*(?:/\s*КПП)?\s*[:№#-]?\s*)(\d{10}|\d{12})(?!\d)")
_OGRN_LABEL = re.compile(r"(?i)(?:(?:ОГРНИП|ОГРН|OGRN)\s*[:№#-]?\s*)(\d{13}|\d{15})(?!\d)")
_KPP = re.compile(r"(?i)(?:(?:КПП|KPP)\s*[:№#-]?\s*)(\d{9})(?!\d)")
_BIK = re.compile(r"(?i)(?:(?:БИК|BIK)\s*[:№#-]?\s*)(\d{9})(?!\d)")
_ACCOUNT = re.compile(
    r"(?i)(?:(?:р/?с|к/?с|расч[её]тн\w*\s+сч[её]т|корр\w*\s+сч[её]т|account)"
    r"\s*[:№#-]?\s*)(\d{20})(?!\d)"
)
_DOC_NUMBER = re.compile(
    r"(?i)((?:договор|контракт|соглашение|дополнительное\s+соглашение|agreement|contract)"
    r"[^\n]{0,30}?(?:№|No\.?|N\.?|#)\s*)([A-Za-zА-Яа-яЁё0-9][\w./-]{0,29})"
)
_ADDRESS = re.compile(
    r"(?im)((?:(?:юридический|почтовый|фактический)\s+адрес|адрес(?:\s+места\s+нахождения)?|"
    r"место\s+нахождения|registered\s+(?:office\s+)?address|legal\s+address)\s*[:\-]\s*)"
    r"([^\n|]{5,250})"
)
_COMPANY = re.compile(
    rf"(?iu)\b((?:{_RU_LEGAL_FORM})"
    r"\s*(?:«[^»\n]{2,150}»|\"[^\"\n]{2,150}\"|[А-ЯЁA-Z][^,;\n|]{2,120})|"
    r"(?:LLC|Ltd\.?|Inc\.?|Corporation|Corp\.?|GmbH)\s+[A-Z][^,;\n|]{2,120})"
)
_RU_NAME_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_RU_PATRONYMIC = (
    # Мужские отчества: Иванович, Ивановича, Ивановичу, Ивановичем.
    r"(?:[А-ЯЁ][а-яё]+(?:ович|евич|ич)(?:а|у|ем|е)?|"
    # Женские отчества: Ивановна, Ивановны, Ивановне, Ивановну,
    # Ивановной/Ивановною; аналогично -евна и -ична.
    r"[А-ЯЁ][а-яё]+(?:овн|евн|ичн)(?:а|ы|е|у|ой|ою))"
)
_PERSON_FULL = re.compile(
    rf"(?<![\w\[])\b({_RU_NAME_WORD}\s+{_RU_NAME_WORD}\s+{_RU_PATRONYMIC})(?!\w)"
)
_PERSON_INITIALS = re.compile(
    rf"(?<![\w\[])({_RU_NAME_WORD}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.|"
    rf"[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*{_RU_NAME_WORD})(?!\w)"
)
_PLACEHOLDER = re.compile(r"\[[А-ЯA-Z][А-ЯA-Z0-9_]*_\d+\]")

# Word добавляет в текст таблиц служебный символ конца ячейки U+0007, а для
# ручного переноса строки может вернуть U+000B. Они визуально разделяют метку
# и значение, но не считаются пробелом регулярными выражениями Python.
_WORD_TEXT_TRANSLATION = str.maketrans({"\x07": "\t", "\x0b": "\n", "\r": "\n"})

_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("ИНН", _INN_LABEL, 1),
    ("ОГРН", _OGRN_LABEL, 1),
    ("КПП", _KPP, 1),
    ("БИК", _BIK, 1),
    ("СЧЕТ", _ACCOUNT, 1),
    ("СНИЛС", _SNILS, 1),
    ("ПАСПОРТ", _PASSPORT, 1),
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _word_text(text: str) -> str:
    """Сделать служебные разделители Word пригодными для локального разбора.

    Замены имеют длину один символ, поэтому позиции границ документа остаются
    совместимыми с исходной строкой.
    """
    return text.translate(_WORD_TEXT_TRANSLATION)


def valid_inn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) not in (10, 12) or len(set(digits)) == 1:
        return False
    numbers = [int(char) for char in digits]

    def checksum(weights: list[int]) -> int:
        return sum(number * weight for number, weight in zip(numbers, weights)) % 11 % 10

    if len(numbers) == 10:
        return checksum([2, 4, 10, 3, 5, 9, 4, 6, 8]) == numbers[9]
    return (
        checksum([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == numbers[10]
        and checksum([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == numbers[11]
    )


def valid_ogrn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) not in (13, 15) or len(set(digits)) == 1:
        return False
    divisor = 11 if len(digits) == 13 else 13
    return int(digits[:-1]) % divisor % 10 == int(digits[-1])


def valid_identifier(value: str) -> bool:
    digits = _digits(value)
    return valid_inn(digits) if len(digits) in (10, 12) else valid_ogrn(digits)


def _requisites_start(text: str) -> int | None:
    matches = [match for match in _REQ_HEADING.finditer(text) if match.start() >= len(text) * 0.35]
    if matches:
        # Подзаголовки реквизитов отдельных сторон внутри общей секции не
        # должны оставлять начало этой секции в тексте, переданном модели.
        general = next((match for match in reversed(matches) if _GENERAL_REQUISITES.match(match.group())), None)
        return (general or matches[-1]).start()
    fallbacks = [match.start() for match in _SIGNATURE_FALLBACK.finditer(text)]
    if fallbacks and fallbacks[-1] >= len(text) * 0.55:
        return fallbacks[-1]
    return None


def _header_end(text: str) -> int:
    """Осторожно отделить только титульную шапку, сохранив преамбулу."""
    offset = 0
    lines = text.splitlines(keepends=True)
    limit = min(len(lines), 40)
    for line in lines[:limit]:
        stripped = line.strip()
        if _PREAMBLE.search(line) or _NUMBERED_SECTION.match(stripped):
            return offset
        if _COMPANY_MARKER.search(line) and len(stripped) > 20:
            # Обычно это начало преамбулы; отдельно стоящее название в титуле короче.
            return offset
        offset += len(line)
    return 0


def split_document(text: str) -> tuple[str, str, str]:
    normalized = _word_text(text)
    req = _requisites_start(normalized)
    body_end = req if req is not None else len(text)
    head_end = min(_header_end(normalized[:body_end]), body_end)
    return text[:head_end], text[head_end:body_end], text[body_end:]


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n,.;:\"'«»“”„").casefold()


def _company_core(value: str) -> str:
    value = re.sub(
        rf"(?iu)^\s*(?:{_RU_LEGAL_FORM}|LLC|LTD\.?|INC\.?)\s*",
        "",
        value,
    )
    return _normal(value)


@dataclass(frozen=True)
class PrivacyPreview:
    input_chars: int
    output_chars: int
    redactions: int
    header_removed: bool
    requisites_removed: bool
    identifiers: tuple[str, ...]
    safe_to_send: bool
    warning: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "inputChars": self.input_chars,
            "outputChars": self.output_chars,
            "redactions": self.redactions,
            "headerRemoved": self.header_removed,
            "requisitesRemoved": self.requisites_removed,
            "identifiers": list(self.identifiers),
            "safeToSend": self.safe_to_send,
            "warning": self.warning,
        }


@dataclass(frozen=True)
class AnonymizedDocument:
    text: str
    preview: PrivacyPreview


@dataclass
class PrivacySession:
    """Общий маппинг для основного и связанных документов одного запроса."""

    _reverse: dict[str, str] = field(default_factory=dict)
    _keys: dict[tuple[str, str], str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)
    _redactions: int = 0

    def _placeholder(self, category: str, value: str, *, key: str | None = None) -> str:
        normalized = key or _normal(value)
        map_key = (category, normalized)
        existing = self._keys.get(map_key)
        if existing:
            self._reverse.setdefault(existing, value)
            return existing
        number = self._counters.get(category, 0) + 1
        self._counters[category] = number
        placeholder = f"[{category}_{number}]"
        self._keys[map_key] = placeholder
        self._reverse[placeholder] = value
        return placeholder

    def _replace_group(self, text: str, pattern: re.Pattern[str], category: str, group: int = 0) -> str:
        def replace(match: re.Match[str]) -> str:
            value = match.group(group)
            if _PLACEHOLDER.fullmatch(value):
                return match.group(0)
            self._redactions += 1
            replacement = self._placeholder(category, value)
            if group == 0:
                return replacement
            start, end = match.span(group)
            whole_start = match.start()
            local_start, local_end = start - whole_start, end - whole_start
            whole = match.group(0)
            return whole[:local_start] + replacement + whole[local_end:]

        return pattern.sub(replace, text)

    def _seed_parties(self, text: str) -> None:
        for match in _COMPANY.finditer(text):
            value = match.group(1)
            core = _company_core(value)
            if core:
                placeholder = self._placeholder("СТОРОНА", value, key=core)
                self._keys[("КОМПАНИЯ", core)] = placeholder
        for pattern in (_PERSON_FULL, _PERSON_INITIALS):
            for match in pattern.finditer(text):
                value = match.group(1)
                placeholder = self._placeholder("ПРЕДСТАВИТЕЛЬ", value)
                self._keys[("ФИО", _normal(value))] = placeholder

    def _redact(self, text: str) -> str:
        for category, pattern, group in _FIELD_PATTERNS:
            text = self._replace_group(text, pattern, category, group)
        text = self._replace_group(text, _EMAIL, "EMAIL")
        text = self._replace_group(text, _URL, "URL")
        text = self._replace_group(text, _PHONE, "ТЕЛЕФОН")
        text = self._replace_group(text, _ADDRESS, "АДРЕС", 2)
        text = self._replace_group(text, _DOC_NUMBER, "НОМЕР_ДОК", 2)

        def company(match: re.Match[str]) -> str:
            value = match.group(1)
            core = _company_core(value)
            placeholder = self._keys.get(("КОМПАНИЯ", core))
            if placeholder is None:
                placeholder = self._placeholder("КОМПАНИЯ", value, key=core)
            self._redactions += 1
            return placeholder

        text = _COMPANY.sub(company, text)

        for pattern in (_PERSON_FULL, _PERSON_INITIALS):
            def person(match: re.Match[str]) -> str:
                value = match.group(1)
                key = _normal(value)
                placeholder = self._keys.get(("ФИО", key)) or self._placeholder("ФИО", value, key=key)
                self._redactions += 1
                return placeholder

            text = pattern.sub(person, text)
        return text

    def anonymize(self, text: str, *, whole_document: bool = False) -> AnonymizedDocument:
        before = self._redactions
        header = requisites = ""
        body = text
        if whole_document:
            header, body, requisites = split_document(text)
        self._seed_parties(header + "\n" + requisites)
        sanitized = self._redact(body)
        # Для целого документа публичные идентификаторы можно читать только из
        # надёжно отделённого блока реквизитов. Выделенный пользователем фрагмент
        # обрабатывается как самостоятельный локальный ввод.
        identifiers = tuple(extract_identifiers(requisites if whole_document else text))
        likely_tail_requisites = bool(extract_identifiers(text[int(len(text) * 0.65):]))
        safe = bool(requisites) or not (whole_document and likely_tail_requisites)
        warning = ""
        if not safe:
            warning = "Не найден надёжный заголовок раздела реквизитов; проверьте предпросмотр перед отправкой"
        preview = PrivacyPreview(
            input_chars=len(text),
            output_chars=len(sanitized),
            redactions=self._redactions - before,
            header_removed=bool(header.strip()),
            requisites_removed=bool(requisites.strip()),
            identifiers=identifiers,
            safe_to_send=safe,
            warning=warning,
        )
        return AnonymizedDocument(sanitized, preview)

    def anonymize_name(self, name: str, number: int) -> str:
        suffix = Path(name).suffix.lower()
        base = f"[ДОКУМЕНТ_{number}]"
        placeholder = f"{base}{suffix}" if suffix else base
        self._reverse[placeholder] = name
        self._reverse.setdefault(base, Path(name).stem or name)
        return placeholder

    def restore(self, value: Any) -> Any:
        if isinstance(value, str):
            for placeholder in sorted(self._reverse, key=len, reverse=True):
                value = value.replace(placeholder, self._reverse[placeholder])
            return value
        if isinstance(value, list):
            return [self.restore(item) for item in value]
        if isinstance(value, dict):
            return {key: self.restore(item) for key, item in value.items()}
        return value

    @staticmethod
    def safe_counterparty_context(raw: str) -> str:
        """Убрать из сохранённой карточки DaData названия, адреса и ФИО."""
        if not raw.strip():
            return ""
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return ""
        status = str(data.get("lookupStatus") or "неизвестно")
        return f"DaData: {status}"


def extract_identifiers(text: str) -> list[str]:
    """Извлечь только валидные ИНН/ОГРН, не возвращая остальной текст реквизитов."""
    found: list[str] = []
    for pattern in (_INN_LABEL, _OGRN_LABEL):
        for match in pattern.finditer(text):
            value = _digits(match.group(1))
            if valid_identifier(value) and value not in found:
                found.append(value)
    return found
