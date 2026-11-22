import json

import httpx
import pytest

from helpers import VALID_CARD
from host.engines.base import EngineError, RunControl, TokenUsage
from host.engines.openrouter import OpenRouterRunner, read_project_file, windows_trust_context
from host.security.credentials import CredentialStore


@pytest.mark.parametrize("path", [
    "references/risk-scale.md",
    "profile/knowledge/contract-checklist/word-contract-checklist.md",
    "profile/knowledge/contract-checklist/contract-checklist-response-schema.md",
])
def test_openrouter_reads_referenced_file_and_accumulates_usage(project, path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "read_project_file", "arguments": json.dumps({"path": path})},
                }]}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110, "cost": 0.001},
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(VALID_CARD, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200,
                      "prompt_tokens_details": {"cached_tokens": 25}, "cost": 0.002},
        })

    (project / path).write_text("# Локальный справочник", "utf-8")
    runner = OpenRouterRunner(lambda: "test-key", transport=httpx.MockTransport(handler))
    result = runner.run("Примени навык `skills/clause-review/SKILL.md`.", project, {"type": "object"},
                        30, lambda _: None, model="openrouter/auto")

    assert result.json == VALID_CARD
    assert len(requests) == 2
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert requests[0]["provider"]["require_parameters"] is True
    assert "temperature" not in requests[0]
    assert requests[1]["messages"][-1]["content"] == "# Локальный справочник"
    assert result.usage.total_tokens == 310
    assert result.usage.cached_input_tokens == 25
    assert result.usage.cost_usd == pytest.approx(0.003)
    assert not result.usage.estimated


def test_openrouter_requires_token(project):
    runner = OpenRouterRunner(lambda: "")
    assert runner.is_available().authenticated is False
    with pytest.raises(EngineError) as info:
        runner.run("тест", project, None, 30, lambda _: None)
    assert info.value.kind == "auth"


def test_openrouter_cannot_read_workflow(project):
    with pytest.raises(ValueError):
        read_project_file(project, "workflow/case-context.md")
    with pytest.raises(ValueError):
        read_project_file(project, "skills/../workflow/case-context.md")
    with pytest.raises(ValueError):
        read_project_file(project, "profile/knowledge/contract-checklist/private-notes.md")


def test_usage_estimate_survives_cancel():
    control = RunControl()
    control.begin_usage("я" * 250)
    control.cancel()
    usage = control.usage()
    assert usage.input_tokens == 100 and usage.total_tokens == 100 and usage.estimated


def test_token_usage_understands_claude_cache_fields():
    usage = TokenUsage.from_mapping({
        "input_tokens": 10, "cache_creation_input_tokens": 20,
        "cache_read_input_tokens": 30, "output_tokens": 5,
    })
    assert usage.input_tokens == 60 and usage.cached_input_tokens == 30 and usage.total_tokens == 65


def test_openrouter_key_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("WAC_PUBLIC_OPENROUTER_API_KEY", "test-from-environment")
    assert CredentialStore().get_openrouter_key() == "test-from-environment"


def test_windows_trust_context_loads_x509_certificates(monkeypatch):
    class FakeContext:
        def __init__(self):
            self.loaded = []

        def load_verify_locations(self, *, cadata):
            self.loaded.append(cadata)

    context = FakeContext()
    monkeypatch.setattr("host.engines.openrouter.ssl.create_default_context", lambda: context)
    monkeypatch.setattr(
        "host.engines.openrouter.ssl.enum_certificates",
        lambda store: [(store.encode("ascii"), "x509_asn", True), (b"ignored", "pkcs_7_asn", True)],
    )

    assert windows_trust_context() is context
    assert len(context.loaded) == 1
    assert context.loaded[0].count("BEGIN CERTIFICATE") == 2
