"""Load node prompt templates from the local prompt_registry directory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_BASE_DIR = Path(__file__).resolve().parent


@dataclass(slots=True)
class PromptTemplateRecord:
    """Structured prompt template loaded from disk."""

    node_name: str
    role_definition: str
    output_schema: dict[str, Any]
    few_shots: list[dict[str, Any]]
    user_prompt_template: str
    system_prompt: str
    metadata: dict[str, Any]


def _template_path(node_name: str) -> Path:
    return _BASE_DIR / f"{node_name}.json"


def build_system_prompt(payload: dict[str, Any]) -> str:
    role_definition = str(payload.get("role_definition", "")).strip()
    output_schema = json.dumps(
        payload.get("output_schema", {}), ensure_ascii=False, indent=2
    )
    few_shots = list(payload.get("few_shots", []) or [])

    parts = [
        role_definition,
        "Output schema:",
        output_schema,
        "Rules:",
        "- Return JSON only.",
        "- Do not include markdown fences.",
        "- Do not add explanations outside the schema.",
        "- Follow field names, enums, and required fields exactly.",
    ]

    for index, item in enumerate(few_shots, start=1):
        example_input = str(item.get("input", "")).strip()
        example_output = json.dumps(
            item.get("output", {}), ensure_ascii=False, indent=2
        )
        parts.extend(
            [
                f"Few-shot example {index} input:",
                example_input,
                f"Few-shot example {index} output:",
                example_output,
            ]
        )

    return "\n\n".join(part for part in parts if part).strip()


def load_prompt_template(node_name: str) -> PromptTemplateRecord:
    """Load a prompt template by node name."""

    path = _template_path(node_name)
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found for node: {node_name}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    system_prompt = str(
        payload.get("system_prompt", "")
    ).strip() or build_system_prompt(payload)
    return PromptTemplateRecord(
        node_name=str(payload.get("node_name", node_name)),
        role_definition=str(payload.get("role_definition", "")).strip(),
        output_schema=dict(payload.get("output_schema", {}) or {}),
        few_shots=list(payload.get("few_shots", []) or []),
        user_prompt_template=str(payload.get("user_prompt_template", "{input_text}")),
        system_prompt=system_prompt,
        metadata=dict(payload.get("metadata", {}) or {}),
    )


def list_prompt_nodes() -> list[str]:
    """List all registered node template names."""

    return sorted(path.stem for path in _BASE_DIR.glob("*.json"))
