import copy
import json

import pytest

from helpers import FakeDaData, VALID_CARD, VALID_CHECKLIST, VALID_CHECKLIST_JSON, VALID_COUNTERPARTY, VALID_JSON, VALID_REVIEW, VALID_REVIEW_JSON, ScriptedRunner, factory_for
from host.engines.base import EngineError, EngineResult
from host.engines.manager import EngineManager
from host.errors import ActionFailure
from host.security.journal import Journal
from host.service import ActionService, RunPayload
from host.tasks import Task

SECRET = "ООО «Секретный контрагент» ИНН 7700000000"
REVIEW_SOURCE = VALID_REVIEW["findings"][0]["sourceText"]


def make(config, tmp_path, runner):
    journal = Journal(tmp_path / "logs" / "journal.jsonl")
    service = ActionService(config, EngineManager(config, factory_for({runner.name: runner})), journal, dadata=FakeDaData())
    return service, journal


def run(service, action_id="clause-review", text=f"7.3. {SECRET} несёт ответственность..."):
    payload = RunPayload(action_id, text, {"contractType": "поставка"}, "Договор.docx")
    action = service.prepare(payload)
    return service.execute(Task(action.id), action, payload)


def test_card_first_try(config, tmp_path):
    runner = ScriptedRunner("codex", [VALID_JSON])
    service, journal = make(config, tmp_path, runner)
    result = run(service)
    assert result == {"type": "card", "card": VALID_CARD, "engine": "codex"}
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["writable"] is False and "minLength" not in json.dumps(call["schema"])
    assert "Секретный контрагент" not in call["prompt"]
    assert "7700000000" not in call["prompt"]
    assert "[КОМПАНИЯ_" in call["prompt"]

    entry = journal.recent()[0]
    assert entry["actionId"] == "clause-review" and entry["engine"] == "codex" and entry["result"] == "ok"
    assert entry["textLength"] > 0
    assert SECRET not in (tmp_path / "logs" / "journal.jsonl").read_text("utf-8")


def test_card_structured_json_from_engine(config, tmp_path):
    runner = ScriptedRunner("claude", [EngineResult(text="", json=VALID_CARD)])
    service, _ = make(config, tmp_path, runner)
    assert run(service)["card"] == VALID_CARD


def test_saved_dadata_card_is_reduced_before_later_model_call(config, tmp_path):
    runner = ScriptedRunner("codex", [VALID_JSON])
    service, _ = make(config, tmp_path, runner)
    raw = json.dumps({
        "identifier": "0001234562",
        "lookupStatus": "найдено",
        "company": {"shortName": "ООО Секрет", "legalAddress": "Москва, Секретная, 1"},
    }, ensure_ascii=False)
    payload = RunPayload("clause-review", "Пункт договора", {"counterpartyCheck": raw})
    action = service.prepare(payload)
    service.execute(Task(action.id), action, payload)
    prompt = runner.calls[0]["prompt"]
    assert "DaData: найдено" in prompt
    assert "ООО Секрет" not in prompt and "Секретная" not in prompt and "000001001" not in prompt


def test_model_placeholders_are_restored_only_after_response(config, tmp_path):
    card = dict(VALID_CARD, issue="Риск для [КОМПАНИЯ_1]")
    runner = ScriptedRunner("codex", [json.dumps(card, ensure_ascii=False)])
    service, _ = make(config, tmp_path, runner)
    result = run(service, text="ООО «Ромашка» обязано оплатить товар")
    assert "Ромашка" not in runner.calls[0]["prompt"]
    assert result["card"]["issue"] == "Риск для ООО «Ромашка»"


def test_card_retry_once_then_ok(config, tmp_path):
    runner = ScriptedRunner("codex", ["Риск высокий, надо править", f"```json\n{VALID_JSON}\n```"])
    service, _ = make(config, tmp_path, runner)
    assert run(service)["card"] == VALID_CARD
    assert len(runner.calls) == 2
    assert "не прошёл проверку по схеме" in runner.calls[1]["prompt"]


def test_card_invalid_twice_returns_raw(config, tmp_path):
    bad = json.dumps(dict(VALID_CARD, riskLevel="критичный"), ensure_ascii=False)
    runner = ScriptedRunner("codex", ["не JSON", bad])
    service, journal = make(config, tmp_path, runner)
    with pytest.raises(ActionFailure) as info:
        run(service)
    assert info.value.code == "invalid_response"
    assert info.value.message == "Не удалось подготовить результат для безопасной работы с документом; показан ответ как есть"
    assert info.value.raw == bad
    assert len(runner.calls) == 2
    assert journal.recent()[0]["result"] == "invalid_response"


def test_contract_review_returns_structured_sidebar_result(config, tmp_path):
    runner = ScriptedRunner("codex", [VALID_REVIEW_JSON])
    service, _ = make(config, tmp_path, runner)
    result = run(service, "contract-review", f"7.3. {REVIEW_SOURCE}")
    assert result == {"type": "review", "review": VALID_REVIEW, "engine": "codex"}
    assert runner.calls[0]["writable"] is False
    assert runner.calls[0]["schema"]["type"] == "object"


def test_lawyer_instruction_is_required_and_anonymized(config, tmp_path):
    runner = ScriptedRunner("codex", [VALID_REVIEW_JSON])
    service, _ = make(config, tmp_path, runner)
    blank = RunPayload("lawyer-instruction", f"7.3. {REVIEW_SOURCE}", {"lawyerInstruction": "  "})
    with pytest.raises(ActionFailure) as info:
        service.prepare(blank)
    assert info.value.code == "instruction_required"

    secret_instruction = "Проверь обязанности ООО «Секретный заказчик» по приемке"
    payload = RunPayload(
        "lawyer-instruction",
        f"7.3. {REVIEW_SOURCE}",
        {"lawyerInstruction": secret_instruction},
    )
    action = service.prepare(payload)
    result = service.execute(Task(action.id), action, payload)
    assert result["type"] == "review"
    prompt = runner.calls[0]["prompt"]
    assert "Секретный заказчик" not in prompt
    assert "Проверь обязанности [КОМПАНИЯ_" in prompt
    assert "<<<ЗАДАЧА-" in prompt


def test_lawyer_instruction_length_is_limited(config, tmp_path):
    service, _ = make(config, tmp_path, ScriptedRunner("codex"))
    payload = RunPayload("lawyer-instruction", "Договор", {"lawyerInstruction": "x" * 4001})
    with pytest.raises(ActionFailure) as info:
        service.prepare(payload)
    assert info.value.code == "instruction_too_large"


def test_contract_checklist_returns_structured_sidebar_result(config, tmp_path):
    runner = ScriptedRunner("codex", [VALID_CHECKLIST_JSON])
    service, _ = make(config, tmp_path, runner)
    result = run(service, "contract-checklist", "1. Предмет договора. Услуги оказываются по акту.")
    assert result == {"type": "checklist", "checklist": VALID_CHECKLIST, "engine": "codex"}
    assert runner.calls[0]["writable"] is False


def test_contract_checklist_rejects_missing_or_duplicate_check_ids(config, tmp_path):
    bad = copy.deepcopy(VALID_CHECKLIST)
    bad["findings"].append(copy.deepcopy(bad["findings"][0]))
    encoded = json.dumps(bad, ensure_ascii=False)
    service, _ = make(config, tmp_path, ScriptedRunner("codex", [encoded, encoded]))
    with pytest.raises(ActionFailure) as info:
        run(service, "contract-checklist", "1. Предмет договора.")
    assert info.value.code == "invalid_response"
    assert any("checkId" in detail for detail in info.value.details)


def test_contract_review_retries_invalid_json(config, tmp_path):
    service, _ = make(config, tmp_path, ScriptedRunner("codex", ["Не JSON", VALID_REVIEW_JSON]))
    assert run(service, "contract-review", f"7.3. {REVIEW_SOURCE}")["review"] == VALID_REVIEW


def test_contract_review_keeps_finding_but_disables_non_verbatim_source_text(config, tmp_path):
    bad = copy.deepcopy(VALID_REVIEW)
    bad["findings"][0]["sourceText"] = "Такого текста в договоре нет."
    encoded = json.dumps(bad, ensure_ascii=False)
    service, _ = make(config, tmp_path, ScriptedRunner("codex", [encoded, encoded]))
    result = run(service, "contract-review", f"7.3. {REVIEW_SOURCE}")["review"]
    finding = result["findings"][0]
    assert finding["sourceText"] == ""
    assert "Автопривязка Word недоступна" in finding["lawyerNote"]
    assert finding["issue"] == bad["findings"][0]["issue"]


def test_contract_review_keeps_finding_but_disables_non_unique_source_text(config, tmp_path):
    service, _ = make(
        config,
        tmp_path,
        ScriptedRunner("codex", [VALID_REVIEW_JSON, VALID_REVIEW_JSON]),
    )
    result = run(service, "contract-review", f"{REVIEW_SOURCE}\n{REVIEW_SOURCE}")["review"]
    assert result["findings"][0]["sourceText"] == ""
    assert "Автопривязка Word недоступна" in result["findings"][0]["lawyerNote"]


def test_contract_review_rejects_whole_document_as_replacement_source(config, tmp_path):
    source = "Условие договора без реквизитов. " * 12
    bad = copy.deepcopy(VALID_REVIEW)
    bad["findings"][0]["sourceText"] = source
    encoded = json.dumps(bad, ensure_ascii=False)
    service, _ = make(config, tmp_path, ScriptedRunner("codex", [encoded, encoded]))
    with pytest.raises(ActionFailure) as info:
        run(service, "contract-review", source)
    assert info.value.code == "invalid_response"
    assert any("sourceText" in detail for detail in info.value.details)


def test_contract_review_retries_non_sequential_ids(config, tmp_path):
    bad = copy.deepcopy(VALID_REVIEW)
    bad["findings"][0]["id"] = "R2"
    encoded = json.dumps(bad, ensure_ascii=False)
    service, _ = make(config, tmp_path, ScriptedRunner("codex", [encoded, encoded]))
    with pytest.raises(ActionFailure) as info:
        run(service, "contract-review", f"7.3. {REVIEW_SOURCE}")
    assert info.value.code == "invalid_response"
    assert any("последовательно" in detail for detail in info.value.details)


def test_contract_review_invalid_twice_returns_raw(config, tmp_path):
    service, _ = make(config, tmp_path, ScriptedRunner("codex", ["Не JSON", "Снова не JSON"]))
    with pytest.raises(ActionFailure) as info:
        run(service, "contract-review", "Договор")
    assert info.value.code == "invalid_response" and info.value.raw == "Снова не JSON"


def test_counterparty_check_is_local_and_does_not_start_model(config, tmp_path):
    runner = ScriptedRunner("codex")
    service, _ = make(config, tmp_path, runner)
    payload = RunPayload(
        "counterparty-check",
        "ООО Контрагент, ИНН 0001234562",
        {"counterpartyIdentifier": "0001234562"},
        "Договор.docx",
    )
    action = service.prepare(payload)
    result = service.execute(Task(action.id), action, payload)
    assert result == {"type": "card", "card": VALID_COUNTERPARTY, "engine": "local-dadata"}
    assert not runner.calls
    assert service.dadata.calls == ["0001234562"]


def test_counterparty_check_does_not_pass_document_to_local_dadata(config, tmp_path):
    runner = ScriptedRunner("codex")
    service, _ = make(config, tmp_path, runner)
    text = (
        "ДОГОВОР\n1. Предмет\nСекретные условия основной части.\n"
        "12. Реквизиты и подписи сторон\nООО «Контрагент»\nИНН 0001234562\n"
    )
    payload = RunPayload("counterparty-check", text, {"counterpartyIdentifier": "0001234562"})
    action = service.prepare(payload)
    service.execute(Task(action.id), action, payload)
    assert service.dadata.calls == ["0001234562"]


def test_counterparty_check_extracts_only_identifier_when_field_is_empty(config, tmp_path):
    runner = ScriptedRunner("codex")
    service, _ = make(config, tmp_path, runner)
    text = (
        "ДОГОВОР\n1. Предмет\n" + ("Условия договора. " * 50) + "\n"
        "12. Реквизиты и подписи сторон\nООО «Контрагент»\nИНН 0001234562\n"
    )
    payload = RunPayload("counterparty-check", text, {"counterpartyIdentifier": ""})
    action = service.prepare(payload)
    service.execute(Task(action.id), action, payload)
    assert service.dadata.calls == ["0001234562"]


def test_counterparty_check_does_not_use_openrouter_or_cli(config, tmp_path):
    config.update({"engine": "openrouter"})
    openrouter = ScriptedRunner("openrouter")
    codex = ScriptedRunner("codex")
    journal = Journal(tmp_path / "logs" / "journal.jsonl")
    engines = EngineManager(config, factory_for({"openrouter": openrouter, "codex": codex}))
    service = ActionService(config, engines, journal, dadata=FakeDaData())
    payload = RunPayload(
        "counterparty-check",
        "ООО Контрагент, ИНН 0001234562",
        {"counterpartyIdentifier": "0001234562"},
    )
    action = service.prepare(payload)
    result = service.execute(Task(action.id), action, payload)
    assert result["engine"] == "local-dadata"
    assert not codex.calls and not openrouter.calls


@pytest.mark.parametrize("action_id,text,code,message", [
    ("nope", "т", "unknown_action", None),
    ("clause-review", "   ", "empty_text", "Выделите пункт договора"),
    ("contract-review", "", "empty_text", "Документ пуст"),
    ("clause-review", "x" * 30_001, "text_too_large", "Фрагмент слишком большой. Выделите конкретный пункт"),
])
def test_prepare_rejects(config, tmp_path, action_id, text, code, message):
    service, _ = make(config, tmp_path, ScriptedRunner("codex"))
    with pytest.raises(ActionFailure) as info:
        service.prepare(RunPayload(action_id, text))
    assert info.value.code == code
    if message:
        assert info.value.message == message


def test_prepare_rejects_invalid_project(config, tmp_path, project):
    (project / "CLAUDE.md").unlink()
    service, _ = make(config, tmp_path, ScriptedRunner("codex"))
    with pytest.raises(ActionFailure) as info:
        service.prepare(RunPayload("clause-review", "т"))
    assert info.value.message == "В указанной папке нет CLAUDE.md. Проверьте путь в настройках"


def test_engine_auth_error_message(config, tmp_path):
    runner = ScriptedRunner("codex", [EngineError("auth", "401 Unauthorized")])
    service, journal = make(config, tmp_path, runner)
    with pytest.raises(ActionFailure) as info:
        run(service)
    assert info.value.code == "engine_auth"
    assert info.value.message == "Требуется вход в Codex. Выполните вход и повторите"
    assert journal.recent()[0]["result"] == "engine_auth"


def test_engine_limit_text_passed_as_is(config, tmp_path):
    runner = ScriptedRunner("codex", [EngineError("limit", "You've hit your usage limit. Try again at 18:00.")])
    service, _ = make(config, tmp_path, runner)
    with pytest.raises(ActionFailure) as info:
        run(service)
    assert info.value.message == "You've hit your usage limit. Try again at 18:00."


def test_codex_model_setting_is_passed(config, tmp_path):
    config.update({"codexModel": "gpt-5.5"})
    runner = ScriptedRunner("codex")
    service, _ = make(config, tmp_path, runner)
    run(service)
    assert runner.calls[0]["model"] == "gpt-5.5"
