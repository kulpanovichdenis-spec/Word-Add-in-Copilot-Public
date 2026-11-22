"""Локальное чтение каталога типовых форм с Windows-аутентификацией."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from .document_context import ALLOWED_EXTENSIONS
from .errors import ActionFailure

_FORM_EXTENSIONS = ALLOWED_EXTENSIONS | {".doc", ".rtf"}
_JSON_LIMIT = 2 * 1024 * 1024
_ARCHIVE = re.compile(r"\b(?:архив|архивные версии|archive|obsolete|черновики?|draft)\b", re.IGNORECASE)
_UNSAFE_URL_CHARS = re.compile(r"[\\\x00-\x1f\x7f]")


def _https_origin(url: str) -> str:
    if _UNSAFE_URL_CHARS.search(url):
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = parts.netloc.lower()
    if parts.scheme != "https" or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        return ""
    return f"https://{host}"


def _path_parts(path: str) -> tuple[str, ...]:
    if not path.startswith("/") or re.search(r"[\\\x00-\x1f\x7f]", path):
        raise ValueError("Ожидается путь к папке SharePoint")
    parts = tuple(path[1:].rstrip("/").split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Недопустимый путь к папке SharePoint")
    return parts


@dataclass(frozen=True)
class LibraryFolder:
    origin: str
    site: str
    folder: str

    @classmethod
    def from_url(cls, value: str) -> LibraryFolder:
        origin = _https_origin(value)
        if not origin:
            raise ValueError("Укажите HTTPS-ссылку на доверенную папку SharePoint без логина и пароля")
        parts = urlsplit(value)
        path = unquote(parts.path)
        view = re.search(r"/Forms/[^/]+\.aspx$", path, re.IGNORECASE)
        library = _path_parts(path[:view.start()] if view else path)
        query = {key.casefold(): values[0] for key, values in parse_qs(parts.query).items()}
        folder_param = (query.get("id") or query.get("rootfolder")) if view else None
        folder = _path_parts(folder_param) if folder_param else library
        if len(library) < 3 or library[0].casefold() not in {"sites", "teams"}:
            raise ValueError("Укажите папку библиотеки на сайте /sites/… или /teams/…")
        if tuple(part.casefold() for part in folder[:len(library)]) != tuple(part.casefold() for part in library):
            raise ValueError("Папка находится за пределами выбранной библиотеки")
        return cls(origin, "/" + "/".join(library[:2]), "/" + "/".join(folder))

    def api(self, resource: str, path: str) -> str:
        encoded = quote(path.replace("'", "''"), safe="")
        return f"{self.origin}{quote(self.site)}/_api/web/{resource}('{encoded}')"


@dataclass(frozen=True)
class TypicalForm:
    id: str
    name: str
    folder: str
    path: str
    size: int
    etag: str = ""

    def public(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "folder": self.folder}


def _get_windows(url: str, accept: str, limit: int, etag: str = "") -> bytes:
    if not _https_origin(url):
        raise ActionFailure("template_invalid_url", "Укажите доверенный адрес SharePoint по HTTPS")
    curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    args = [
        str(curl), "--disable", "--silent", "--globoff", "--noproxy", "*",
        "--proto", "=https", "--request", "GET", "--ntlm", "--user", ":", "--max-time", "15",
        "--max-filesize", str(limit), "--header", f"Accept: {accept}",
        "--write-out", "\n%{http_code}",
    ]
    if etag:
        args.extend(["--header", f"If-Match: {etag}"])
    args.extend(["--url", url])
    try:
        result = subprocess.run(
            args, capture_output=True, timeout=18, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ActionFailure("template_unreachable", "Не удалось подключиться к SharePoint. Проверьте корпоративную сеть и доступ к библиотеке") from None
    data, _, status = result.stdout.rpartition(b"\n")
    if status in {b"401", b"403"}:
        raise ActionFailure("template_access_denied", "Текущая учётная запись Windows не имеет доступа к папке SharePoint")
    if status == b"404":
        raise ActionFailure("template_not_found", "Папка или форма не найдена в SharePoint. Проверьте ссылку или обновите список")
    if status == b"412":
        raise ActionFailure("template_selection_stale", "Форма изменилась в SharePoint. Откройте список и выберите её снова")
    if result.returncode == 63 or len(data) > limit:
        raise ActionFailure("template_too_large", "Ответ SharePoint превышает допустимый размер")
    if result.returncode or status != b"200":
        raise ActionFailure("template_unreachable", "SharePoint не вернул файл или каталог. Проверьте доступ и ссылку в настройках")
    return data


class SharePointTemplates:
    """Кэширует метаданные на время выбора. Скачивает только подтверждённый файл."""

    def __init__(self, get: Callable[[str, str, int, str], bytes] = _get_windows) -> None:
        self._get = get
        self._lock = threading.Lock()
        self._snapshot: tuple[LibraryFolder, float, dict[str, TypicalForm]] | None = None

    @staticmethod
    def _folder(url: str) -> LibraryFolder:
        if not url.strip():
            raise ActionFailure("template_not_configured", "Укажите папку типовых форм в настройках надстройки")
        try:
            return LibraryFolder.from_url(url.strip())
        except ValueError:
            raise ActionFailure("template_invalid_url", "Проверьте HTTPS-ссылку на папку типовых форм в настройках") from None

    def _json(self, url: str) -> dict[str, Any]:
        data = self._get(url, "application/json;odata=verbose", _JSON_LIMIT, "")
        try:
            value = json.loads(data)["d"]
            if isinstance(value, dict):
                return value
        except (ValueError, KeyError, TypeError):
            pass
        raise ActionFailure("template_bad_catalog", "SharePoint вернул каталог в неожиданном формате")

    def _entries(self, first: Any, endpoint: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = first
        for _ in range(10):
            if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                break
            items.extend(item for item in page["results"] if isinstance(item, dict))
            link = page.get("__next")
            if not link:
                return items
            if not isinstance(link, str) or _UNSAFE_URL_CHARS.search(link):
                break
            try:
                url = urljoin(endpoint, link)
            except ValueError:
                break
            if _https_origin(url) != _https_origin(endpoint) or unquote(urlsplit(url).path) != unquote(urlsplit(endpoint).path):
                break
            page = self._json(url)
        raise ActionFailure("template_bad_catalog", "Не удалось полностью прочитать список SharePoint. Проверьте выбранную папку")

    def catalog(self, url: str) -> list[TypicalForm]:
        location = self._folder(url)
        with self._lock:
            if self._snapshot and self._snapshot[0] == location and time.monotonic() - self._snapshot[1] < 60:
                return list(self._snapshot[2].values())
            forms: dict[str, TypicalForm] = {}
            folders = deque([location.folder])
            seen: set[str] = set()
            deadline = time.monotonic() + 40
            while folders:
                folder = folders.popleft()
                if folder.casefold() in seen:
                    continue
                if len(seen) >= 80 or time.monotonic() > deadline:
                    raise ActionFailure("template_bad_catalog", "Каталог типовых форм слишком велик или SharePoint отвечает медленно. Укажите более узкую папку")
                seen.add(folder.casefold())
                endpoint = location.api("GetFolderByServerRelativeUrl", folder)
                tree = self._json(
                    endpoint + "?$expand=Files,Folders&$select=Files/ServerRelativeUrl,Files/Length,Files/ETag,Folders/ServerRelativeUrl"
                )
                for kind in ("Files", "Folders"):
                    for row in self._entries(tree.get(kind), f"{endpoint}/{kind}"):
                        path = row.get("ServerRelativeUrl")
                        if not isinstance(path, str):
                            continue
                        try:
                            parts = _path_parts(path)
                        except ValueError:
                            continue
                        if ("/" + "/".join(parts[:-1])).casefold() != folder.casefold():
                            continue
                        name = parts[-1]
                        if _ARCHIVE.search(name) or name.startswith("~$"):
                            continue
                        if kind == "Folders":
                            folders.append(path)
                        elif PurePosixPath(name).suffix.lower() in _FORM_EXTENSIONS:
                            try:
                                size = int(row.get("Length", 0))
                            except (TypeError, ValueError):
                                continue
                            if size <= 0:
                                continue
                            etag = str(row.get("ETag", ""))
                            if not re.fullmatch(r'(?:W/)?"[^"\x00-\x1f]{1,200}"', etag):
                                etag = ""
                            form_id = hashlib.sha256((location.origin + path).encode("utf-8")).hexdigest()[:24]
                            relative_folder = "/".join(parts[len(_path_parts(location.folder)):-1])
                            forms[form_id] = TypicalForm(form_id, name, relative_folder, path, size, etag)
            ordered = sorted(forms.values(), key=lambda form: (form.folder.casefold(), form.name.casefold()))
            self._snapshot = location, time.monotonic(), {form.id: form for form in ordered}
            return ordered

    def selected(self, url: str, form_id: str) -> tuple[LibraryFolder, TypicalForm]:
        location = self._folder(url)
        with self._lock:
            if self._snapshot and self._snapshot[0] == location and time.monotonic() - self._snapshot[1] < 900:
                form = self._snapshot[2].get(form_id)
                if form:
                    return location, form
        raise ActionFailure("template_selection_stale", "Снова откройте список типовых форм и подтвердите выбор")

    def download(self, location: LibraryFolder, form: TypicalForm, limit: int) -> bytes:
        if form.size > limit:
            raise ActionFailure("template_too_large", "Выбранная типовая форма превышает допустимый размер файла")
        endpoint = location.api("GetFileByServerRelativeUrl", form.path) + "/$value"
        return self._get(endpoint, "application/octet-stream", limit, form.etag)
