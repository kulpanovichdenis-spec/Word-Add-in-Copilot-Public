import json

import pytest

from helpers import FIXTURES, VALID_CARD, VALID_JSON
from host.actions.schema import SchemaError, engine_schema, extract_json, load_schema_file, validation_errors


@pytest.fixture
def schema():
    return load_schema_file(FIXTURES / "clause-review-response-schema.md")


def test_loads_schema_block_not_example(schema):
    assert schema["type"] == "object"
    assert "riskLevel" in schema["required"]


def test_markdown_without_schema(tmp_path):
    md = tmp_path / "s.md"
    md.write_text("# нет схемы\n```json\n{\"a\": 1}\n```\n", "utf-8")
    with pytest.raises(SchemaError, match="нет JSON-схемы"):
        load_schema_file(md)


def test_missing_schema_file(tmp_path):
    with pytest.raises(SchemaError, match="не найден"):
        load_schema_file(tmp_path / "absent.md")


def test_engine_schema_strips_unsupported_keywords(schema):
    stripped = json.dumps(engine_schema(schema))
    assert "minLength" not in stripped and "maxLength" not in stripped and "$schema" not in stripped
    assert "maxItems" in stripped and "enum" in stripped
    assert "minLength" in json.dumps(schema)  # исходная схема не изменилась


def test_valid_json(schema):
    assert extract_json(VALID_JSON) == VALID_CARD
    assert validation_errors(VALID_CARD, schema) == []


def test_json_in_markdown_wrapper():
    assert extract_json(f"```json\n{VALID_JSON}\n```") == VALID_CARD


def test_json_inside_text():
    assert extract_json(f"Вот результат:\n{VALID_JSON}\nГотово.") == VALID_CARD


def test_text_instead_of_json():
    with pytest.raises(ValueError, match="нет JSON"):
        extract_json("Пункт выглядит рискованным, рекомендую править.")


def test_truncated_json():
    with pytest.raises(ValueError):
        extract_json(VALID_JSON[: len(VALID_JSON) // 2])


def test_empty_answer():
    with pytest.raises(ValueError, match="пустой"):
        extract_json("   ")


def test_schema_violations_reported(schema):
    bad = dict(VALID_CARD, riskLevel="критичный", issue="")
    del bad["lawyerNote"]
    errors = validation_errors(bad, schema)
    joined = "\n".join(errors)
    assert "riskLevel" in joined and "issue" in joined and "lawyerNote" in joined
