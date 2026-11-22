import json
import subprocess
from urllib.parse import quote

import pytest

from host.errors import ActionFailure
from host.sharepoint import LibraryFolder, SharePointTemplates, _get_windows

EXAMPLE_TEMPLATES_URL = "https://sharepoint.example.test/sites/Legal/DocLib/Типовые%20формы"


def test_sharepoint_library_accepts_both_folder_and_library_view_urls():
    direct = LibraryFolder.from_url(EXAMPLE_TEMPLATES_URL)
    view = "https://sharepoint.example.test/sites/Legal/DocLib/Forms/AllItems.aspx?id=" + quote(direct.folder, safe="")
    assert LibraryFolder.from_url(view) == direct
    assert direct.site == "/sites/Legal"


@pytest.mark.parametrize("url", [
    "http://sharepoint.example.test/sites/Legal/DocLib",
    "https://sharepoint.example.test@evil.test/sites/Legal/DocLib",
    "https://sharepoint.example.te\nst/sites/Legal/DocLib",
    "https://sharepoint.example.test/sites/Legal/DocLib/Forms/AllItems.aspx?id=%2Fsites%2FOther%2FDocLib",
    "https://sharepoint.example.test/sites/Legal/DocLib/%2e%2e/Other",
])
def test_invalid_or_outside_sharepoint_folder_urls_are_rejected(url):
    with pytest.raises(ValueError):
        LibraryFolder.from_url(url)


def test_catalog_reads_nested_form_metadata_and_ignores_outside_links_and_archives():
    base = LibraryFolder.from_url(EXAMPLE_TEMPLATES_URL).folder
    child = f"{base}/Поставка"
    requests = []

    def get(url, accept, limit, etag):
        requests.append(url)
        nested = quote(child, safe="") in url
        files = [{"ServerRelativeUrl": child + "/Форма.docx", "Length": "200"}] if nested else [
            {"ServerRelativeUrl": base + "/Ссылка.url", "Length": "100"},
            {"ServerRelativeUrl": base + "/Пустая.docx", "Length": "0"},
            {"ServerRelativeUrl": "/sites/Other/DocLib/Чужая.docx", "Length": "100"},
        ]
        folders = [] if nested else [{"ServerRelativeUrl": path} for path in (child, base + "/Архив")]
        return json.dumps({"d": {"Files": {"results": files}, "Folders": {"results": folders}}}).encode()

    library = SharePointTemplates(get)
    form, = library.catalog(EXAMPLE_TEMPLATES_URL)
    assert form.public() == {"id": form.id, "name": "Форма.docx", "folder": "Поставка"}
    assert len(requests) == 2 and not any("/$value" in url for url in requests)
    with pytest.raises(ActionFailure, match="Снова откройте список"):
        library.selected(EXAMPLE_TEMPLATES_URL + "/Другая папка", form.id)
    with pytest.raises(ActionFailure, match="Снова откройте список"):
        library.selected(EXAMPLE_TEMPLATES_URL.replace("sharepoint.example.test", "other.example.test"), form.id)


@pytest.mark.parametrize("page_link", [
    "https://evil.test/",
    "//evil.test/",
    "https://sharepoint.example.test/sites/Legal/DocLib/other",
    "https://sharepoint.example.te\nst/sites/Legal/DocLib/other",
])
def test_catalog_never_follows_unsafe_pagination_url(page_link):
    requests = []

    def get(url, accept, limit, etag):
        requests.append(url)
        return json.dumps({"d": {"Files": {"results": [], "__next": page_link}, "Folders": {"results": []}}}).encode()

    with pytest.raises(ActionFailure, match="полностью прочитать список"):
        SharePointTemplates(get).catalog(EXAMPLE_TEMPLATES_URL)
    assert len(requests) == 1


def test_unconfigured_catalog_does_not_contact_sharepoint():
    def forbidden(*args):
        pytest.fail("request to SharePoint with no configured URL")

    with pytest.raises(ActionFailure, match="Укажите папку типовых форм"):
        SharePointTemplates(forbidden).catalog("")


def test_windows_transport_is_get_only_without_redirects(monkeypatch):
    def run(args, **_):
        assert args[args.index("--request") + 1] == "GET"
        assert args[args.index("--proto") + 1] == "=https"
        assert "--disable" in args and "--location" not in args
        return subprocess.CompletedProcess(args, 0, stdout=b"data\n200")

    monkeypatch.setattr(subprocess, "run", run)
    assert _get_windows(EXAMPLE_TEMPLATES_URL, "application/json", 100) == b"data"
