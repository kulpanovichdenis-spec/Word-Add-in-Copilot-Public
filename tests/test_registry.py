import pytest

from helpers import REPO
from host.actions.registry import RegistryError, load_registry


def write(tmp_path, text):
    path = tmp_path / "actions.yaml"
    path.write_text(text, "utf-8")
    return path


def test_repo_registry_has_seven_actions():
    registry = load_registry(REPO / "actions.yaml")
    assert [a.id for a in registry.actions] == [
        "clause-review",
        "contract-review",
        "contract-auto-apply",
        "lawyer-instruction",
        "contract-checklist",
        "template-compare",
        "counterparty-check",
    ]
    clause, contract, auto_apply, lawyer_instruction, checklist, template, counterparty = registry.actions
    assert clause.resultType == "card" and not clause.writable
    assert contract.resultType == "review" and not contract.writable and contract.requiresConfirmation
    assert auto_apply.resultType == "review" and auto_apply.autoApply and auto_apply.requiresConfirmation
    assert lawyer_instruction.resultType == "review" and lawyer_instruction.requiresInstruction
    assert not lawyer_instruction.requiresConfirmation and not lawyer_instruction.autoApply
    assert checklist.resultType == "checklist" and not checklist.autoApply and checklist.requiresConfirmation
    assert template.resultType == "review" and not template.autoApply and template.has_capability("typical-template-read")
    assert counterparty.resultType == "card" and not counterparty.writable and not counterparty.requiresConfirmation
    assert counterparty.engine == "default"
    assert clause.skill == "word-clause-review"
    assert contract.skill == "word-contract-review"
    assert "legal-methodology-read" in clause.capabilities
    assert "clause-cards-read" in contract.capabilities
    assert clause.required_files == (
        "references/contract-playbook.md",
        "references/risk-scale.md",
    )


@pytest.mark.parametrize("missing", ["word-contract-checklist.md", "contract-checklist-response-schema.md"])
def test_checklist_disabled_when_profile_file_is_missing(project, missing):
    (project / "profile/knowledge/contract-checklist" / missing).unlink()
    registry = load_registry(REPO / "actions.yaml")
    checklist = next(action for action in registry.descriptors(project) if action["id"] == "contract-checklist")
    assert not checklist["available"] and missing in checklist["problem"]


def test_broken_yaml(tmp_path):
    with pytest.raises(RegistryError, match="YAML"):
        load_registry(write(tmp_path, "actions: [\n  - id: x"))


def test_missing_actions_key(tmp_path):
    with pytest.raises(RegistryError, match="actions"):
        load_registry(write(tmp_path, "buttons: []\n"))


def test_unknown_field(tmp_path):
    text = "actions:\n  - {id: a, label: A, skill: s, input: selection, resultType: text, colour: red}\n"
    with pytest.raises(RegistryError, match="colour"):
        load_registry(write(tmp_path, text))


def test_unknown_runtime_capability_is_rejected(tmp_path):
    text = (
        "actions:\n  - {id: a, label: A, skill: s, input: selection, resultType: text,"
        " capabilities: [shell-exec]}\n"
    )
    with pytest.raises(RegistryError, match="capabilities"):
        load_registry(write(tmp_path, text))


def test_duplicate_id(tmp_path):
    item = "  - {id: a, label: A, skill: s, input: selection, resultType: text}\n"
    with pytest.raises(RegistryError, match="повторяется"):
        load_registry(write(tmp_path, "actions:\n" + item + item))


def test_card_requires_schema(tmp_path):
    text = "actions:\n  - {id: a, label: A, skill: s, input: selection, resultType: card}\n"
    with pytest.raises(RegistryError, match="schema"):
        load_registry(write(tmp_path, text))


def test_review_requires_schema(tmp_path):
    text = "actions:\n  - {id: a, label: A, skill: s, input: document, resultType: review}\n"
    with pytest.raises(RegistryError, match="schema"):
        load_registry(write(tmp_path, text))


def test_checklist_requires_schema(tmp_path):
    text = "actions:\n  - {id: a, label: A, skill: s, input: document, resultType: checklist}\n"
    with pytest.raises(RegistryError, match="schema"):
        load_registry(write(tmp_path, text))


def test_auto_apply_is_only_valid_for_review(tmp_path):
    text = (
        "actions:\n  - {id: a, label: A, skill: s, input: document, resultType: text, autoApply: true}\n"
    )
    with pytest.raises(RegistryError, match="autoApply"):
        load_registry(write(tmp_path, text))


@pytest.mark.parametrize(
    "item",
    [
        "{id: a, label: A, skill: s, input: selection, resultType: review, schema: schema.md, requiresInstruction: true}",
        "{id: a, label: A, skill: s, input: document, resultType: text, requiresInstruction: true}",
        "{id: a, label: A, skill: s, input: document, resultType: review, schema: schema.md, requiresInstruction: true, requiresConfirmation: true}",
    ],
)
def test_instruction_action_contract_is_restricted(tmp_path, item):
    with pytest.raises(RegistryError, match="requiresInstruction"):
        load_registry(write(tmp_path, f"actions:\n  - {item}\n"))


@pytest.mark.parametrize(
    "item",
    [
        "{id: a, label: A, skill: s, input: document, resultType: file}",
        "{id: a, label: A, skill: s, input: document, resultType: text, access: write}",
    ],
)
def test_writable_actions_are_rejected(tmp_path, item):
    with pytest.raises(RegistryError, match="отключены политикой безопасности"):
        load_registry(write(tmp_path, f"actions:\n  - {item}\n"))


def test_skill_name_cannot_escape_folder(tmp_path):
    text = "actions:\n  - {id: a, label: A, skill: ../evil, input: selection, resultType: text}\n"
    with pytest.raises(RegistryError):
        load_registry(write(tmp_path, text))


def test_unknown_skill_makes_action_unavailable(tmp_path, project):
    text = "actions:\n  - {id: a, label: A, skill: no-such-skill, input: selection, resultType: text}\n"
    registry = load_registry(write(tmp_path, text))
    check = registry.check(registry.get("a"), project)
    assert not check.available
    assert "skills/no-such-skill/SKILL.md" in check.problem


def test_missing_schema_file_makes_action_unavailable(tmp_path, project):
    text = ("actions:\n  - {id: a, label: A, skill: word-clause-review, input: selection, resultType: card,"
            " schema: references/absent.md}\n")
    registry = load_registry(write(tmp_path, text))
    check = registry.check(registry.get("a"), project)
    assert not check.available and "absent.md" in check.problem


def test_missing_required_file_makes_action_unavailable(tmp_path, project):
    text = (
        "actions:\n  - {id: a, label: A, skill: word-clause-review, input: selection, resultType: text,"
        " requiredFiles: [references/absent-playbook.md]}\n"
    )
    registry = load_registry(write(tmp_path, text))
    check = registry.check(registry.get("a"), project)
    assert not check.available
    assert "absent-playbook.md" in check.problem


def test_schema_path_cannot_escape_project(tmp_path, project):
    text = ("actions:\n  - {id: a, label: A, skill: word-clause-review, input: selection, resultType: card,"
            " schema: ../../secret.md}\n")
    registry = load_registry(write(tmp_path, text))
    assert "за пределы" in registry.check(registry.get("a"), project).problem


def test_descriptors_against_project(project):
    registry = load_registry(REPO / "actions.yaml")
    descriptors = registry.descriptors(project)
    assert descriptors[0] == {
        "id": "clause-review",
        "label": "Анализировать пункт",
        "description": "",
        "input": "selection",
        "resultType": "card",
        "expectedDuration": "short",
        "requiresConfirmation": False,
        "requiresInstruction": False,
        "autoApply": False,
        "available": True,
        "problem": "",
    }
    assert registry.check(registry.get("clause-review"), project).schema["type"] == "object"


def test_descriptors_without_project():
    registry = load_registry(REPO / "actions.yaml")
    assert all(not d["available"] for d in registry.descriptors(None))
