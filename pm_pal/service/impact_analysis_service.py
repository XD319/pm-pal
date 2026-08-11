"""Impact analysis service for selective rerun planning."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pm_pal.utils.llm_structured_call import StructuredCallError, llm_structured_call

WORKFLOW_NODES: tuple[str, ...] = (
    "parser",
    "planner",
    "risk",
    "delivery_planning",
    "reviewer",
    "reporter",
)


def _short_json(payload: Any, *, max_chars: int = 6000) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
    except TypeError:
        raw = str(payload)
    if len(raw) <= max_chars:
        return raw
    return f"{raw[:max_chars]}\n...<truncated>"


def _deterministic_fallback(
    *,
    changed_fields: list[str],
    changed_sections: list[str],
) -> dict[str, Any]:
    tokens = {
        token.lower()
        for token in changed_fields + changed_sections
        if isinstance(token, str) and token.strip()
    }
    affected = {"parser", "reporter"}
    if any(
        key in tokens
        for key in (
            "scope",
            "requirement",
            "acceptance_criteria",
            "scenarios",
            "user_story",
        )
    ):
        affected.update({"planner", "delivery_planning", "reviewer", "risk"})
    if any(key in tokens for key in ("risk", "security", "compliance")):
        affected.update({"risk", "reviewer"})
    if any(
        key in tokens
        for key in ("timeline", "milestone", "task", "effort", "dependency")
    ):
        affected.update({"planner", "delivery_planning", "reviewer"})
    ordered = [node for node in WORKFLOW_NODES if node in affected]
    return {
        "affected_nodes": ordered,
        "reasons": {node: ["fallback_heuristic"] for node in ordered},
        "confidence": "low",
    }


async def analyze_affected_nodes_async(
    *,
    artifact_diff: dict[str, Any],
    baseline_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Infer affected workflow nodes from structured artifact diff."""
    changed_fields = [
        str(item)
        for item in artifact_diff.get("changed_fields", [])
        if str(item).strip()
    ]
    changed_sections = [
        str(item)
        for item in artifact_diff.get("changed_sections", [])
        if str(item).strip()
    ]
    baseline_digest = hashlib.sha256(
        json.dumps(
            baseline_snapshot or {}, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()[:12]
    schema = {
        "type": "object",
        "properties": {
            "affected_nodes": {
                "type": "array",
                "items": {"type": "string", "enum": list(WORKFLOW_NODES)},
                "uniqueItems": True,
            },
            "reasons": {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["affected_nodes", "reasons", "confidence"],
        "additionalProperties": False,
    }
    prompt = (
        "You are an impact analysis engine for a fixed workflow graph.\n"
        f"Workflow nodes: {', '.join(WORKFLOW_NODES)}.\n"
        "Return minimal affected nodes only. If uncertain, include dependency-downstream nodes.\n\n"
        f"Baseline snapshot digest: {baseline_digest}\n"
        f"Structured diff:\n{_short_json(artifact_diff)}"
    )
    metadata: dict[str, Any] = {"agent_name": "impact_analysis_service", "run_id": ""}
    try:
        result = await llm_structured_call(
            prompt=prompt, schema=schema, metadata=metadata
        )
    except StructuredCallError:
        result = _deterministic_fallback(
            changed_fields=changed_fields, changed_sections=changed_sections
        )

    raw_nodes = result.get("affected_nodes")
    if not isinstance(raw_nodes, list):
        result = _deterministic_fallback(
            changed_fields=changed_fields, changed_sections=changed_sections
        )
        raw_nodes = result["affected_nodes"]
    normalized = [
        node for node in WORKFLOW_NODES if node in {str(item) for item in raw_nodes}
    ]
    if not normalized:
        normalized = ["parser", "reporter"]
    reasons = result.get("reasons") if isinstance(result.get("reasons"), dict) else {}
    return {
        "affected_nodes": normalized,
        "reasons": {node: list(reasons.get(node, [])) for node in normalized},
        "confidence": str(result.get("confidence", "low") or "low"),
        "analysis_mode": str(metadata.get("structured_mode", "fallback") or "fallback"),
    }
