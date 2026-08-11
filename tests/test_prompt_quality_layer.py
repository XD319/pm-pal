from __future__ import annotations

from pydantic import BaseModel

from pm_pal.prompt_quality.context_trimmer import trim_context_for_node
from pm_pal.prompt_quality.output_validator import (
    SchemaValidationError,
    validate_output,
)
from pm_pal.prompt_registry import list_prompt_nodes, load_prompt_template


class _SampleSchema(BaseModel):
    value: str


def test_validate_output_accepts_pydantic_schema() -> None:
    payload = validate_output({"value": "ok"}, _SampleSchema)
    assert payload == {"value": "ok"}


def test_validate_output_accepts_json_string_for_pydantic_schema() -> None:
    payload = validate_output('{"value": "ok"}', _SampleSchema)
    assert payload == {"value": "ok"}


def test_validate_output_accepts_double_encoded_json_string() -> None:
    payload = validate_output('"{\\"value\\": \\"ok\\"}"', _SampleSchema)
    assert payload == {"value": "ok"}


def test_validate_output_rejects_additional_json_schema_fields() -> None:
    schema = {
        "type": "object",
        "required": ["value"],
        "additionalProperties": False,
        "properties": {
            "value": {"type": "string"},
        },
    }

    try:
        validate_output({"value": "ok", "extra": 1}, schema)
    except SchemaValidationError as exc:
        assert any("additional property" in item for item in exc.errors)
    else:
        raise AssertionError("expected SchemaValidationError")


def test_trim_context_for_node_short_text_passthrough() -> None:
    result = trim_context_for_node("parser", "short prd")
    assert result.was_trimmed is False
    assert result.text == "short prd"


def test_trim_context_for_node_long_text_summarizes() -> None:
    text = "\n".join(
        f"- Requirement {i}: user must complete step {i}." for i in range(400)
    )
    result = trim_context_for_node("parser", text, max_chars=1200, chunk_chars=300)
    assert result.was_trimmed is True
    assert result.trimmed_chars <= 1200
    assert "Chunk 1" in result.text


def test_prompt_registry_loads_parser_template() -> None:
    template = load_prompt_template("parser")
    assert template.node_name == "parser"
    assert template.few_shots
    assert "Output schema:" in template.system_prompt


def test_prompt_registry_lists_all_required_nodes() -> None:
    assert set(list_prompt_nodes()) >= {
        "parser",
        "planner",
        "risk",
        "delivery_planning",
        "reviewer",
        "reporter",
    }
