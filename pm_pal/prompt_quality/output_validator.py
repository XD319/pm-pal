"""Strict output validation and bounded retry helpers for LLM node calls."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import json_repair
from pydantic import BaseModel, ValidationError

JsonSchema = dict[str, Any]
SchemaLike = type[BaseModel] | JsonSchema


class SchemaValidationError(ValueError):
    """Raised when an output cannot be parsed or validated against schema."""

    def __init__(
        self,
        message: str,
        *,
        errors: list[str] | None = None,
        raw_output: str = "",
    ) -> None:
        super().__init__(message)
        self.errors = errors or []
        self.raw_output = raw_output


@dataclass(slots=True)
class ValidationResult:
    """Validated result plus retry metadata."""

    output: dict[str, Any]
    raw_output: str
    attempts: int
    errors: list[str]


def _schema_to_json(schema: SchemaLike) -> str:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        payload = schema.model_json_schema()
    elif isinstance(schema, dict):
        payload = schema
    else:
        raise TypeError("schema must be a BaseModel subclass or a JSON schema dict")
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _coerce_to_json_object(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, BaseModel):
        return candidate.model_dump(mode="python", by_alias=True)
    if isinstance(candidate, dict):
        return candidate
    if isinstance(candidate, str):
        # Fallback LLM paths often return JSON text (sometimes double-encoded). :-)
        parsed: Any = candidate
        for _ in range(3):
            if not isinstance(parsed, str):
                break
            parsed = json_repair.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
        raise SchemaValidationError(
            "parsed JSON is not an object",
            errors=[f"expected object at root, got {type(parsed).__name__}"],
            raw_output=candidate,
        )
    raise SchemaValidationError(
        "output is not JSON-like",
        errors=[f"unsupported output type: {type(candidate).__name__}"],
        raw_output=str(candidate),
    )


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(
            value, float
        )
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_json_schema(
    value: Any,
    schema: JsonSchema,
    *,
    path: str = "$",
) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_matches_type(value, item) for item in expected_type):
            errors.append(
                f"{path}: expected one of {expected_type}, got {_json_type_name(value)}"
            )
            return errors
    elif isinstance(expected_type, str):
        if not _matches_type(value, expected_type):
            errors.append(
                f"{path}: expected {expected_type}, got {_json_type_name(value)}"
            )
            return errors

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        errors.append(f"{path}: expected one of {enum_values}, got {value!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional_properties = schema.get("additionalProperties", True)

        for field_name in required:
            if field_name not in value:
                errors.append(f"{path}.{field_name}: required field is missing")

        if isinstance(properties, dict):
            for key, child in value.items():
                if key in properties and isinstance(properties[key], dict):
                    errors.extend(
                        _validate_json_schema(
                            child, properties[key], path=f"{path}.{key}"
                        )
                    )
                elif additional_properties is False:
                    errors.append(f"{path}.{key}: additional property is not allowed")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(
                f"{path}: expected at least {min_items} items, got {len(value)}"
            )

        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate_json_schema(item, items_schema, path=f"{path}[{index}]")
                )

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: expected minLength {min_length}, got {len(value)}")

    return errors


def validate_output(
    candidate: Any,
    schema: SchemaLike,
) -> dict[str, Any]:
    """Validate candidate output against a strict schema contract."""

    if isinstance(schema, type) and issubclass(schema, BaseModel):
        try:
            # Plain-text structured calls return JSON strings; coerce first. :-)
            payload = (
                candidate
                if isinstance(candidate, (dict, BaseModel))
                else _coerce_to_json_object(candidate)
            )
            model = schema.model_validate(payload)
        except SchemaValidationError:
            raise
        except ValidationError as exc:
            raise SchemaValidationError(
                "pydantic schema validation failed",
                errors=[err["msg"] for err in exc.errors()],
                raw_output=str(candidate),
            ) from exc
        return model.model_dump(mode="python", by_alias=True)

    payload = _coerce_to_json_object(candidate)
    errors = _validate_json_schema(payload, schema)
    if errors:
        raise SchemaValidationError(
            "json schema validation failed",
            errors=errors,
            raw_output=json.dumps(payload, ensure_ascii=False),
        )
    return payload


def _build_retry_prompt(
    *,
    base_prompt: str,
    schema: SchemaLike,
    errors: list[str],
    previous_raw_output: str,
    attempt: int,
) -> str:
    errors_block = (
        "\n".join(f"- {item}" for item in errors[:10]) or "- unknown validation failure"
    )
    return (
        f"{base_prompt}\n\n"
        f"Previous attempt {attempt} was invalid.\n"
        "Return JSON only. Do not add prose, markdown, or explanations.\n"
        "Fix the output so it matches the schema exactly.\n"
        "Validation errors:\n"
        f"{errors_block}\n\n"
        "Target JSON schema:\n"
        f"{_schema_to_json(schema)}\n\n"
        "Previous invalid output:\n"
        f"{previous_raw_output}\n"
    )


async def validate_with_retries(
    *,
    prompt: str,
    schema: SchemaLike,
    invoke: Callable[[str], Awaitable[Any]],
    max_retries: int = 2,
) -> ValidationResult:
    """Validate model output, retrying up to ``max_retries`` times on failure."""

    attempt = 0
    current_prompt = prompt
    last_errors: list[str] = []
    last_raw_output = ""

    while attempt <= max_retries:
        attempt += 1
        candidate = await invoke(current_prompt)
        last_raw_output = (
            candidate
            if isinstance(candidate, str)
            else json.dumps(candidate, ensure_ascii=False)
        )
        try:
            validated = validate_output(candidate, schema)
            return ValidationResult(
                output=validated,
                raw_output=last_raw_output,
                attempts=attempt,
                errors=list(last_errors),
            )
        except SchemaValidationError as exc:
            last_errors = list(exc.errors)
            last_raw_output = exc.raw_output or last_raw_output
            if attempt > max_retries:
                raise SchemaValidationError(
                    f"validation failed after {attempt} attempts",
                    errors=last_errors,
                    raw_output=last_raw_output,
                ) from exc
            current_prompt = _build_retry_prompt(
                base_prompt=prompt,
                schema=schema,
                errors=last_errors,
                previous_raw_output=last_raw_output,
                attempt=attempt,
            )

    raise SchemaValidationError(
        "validation retry loop terminated unexpectedly",
        errors=last_errors,
        raw_output=last_raw_output,
    )
