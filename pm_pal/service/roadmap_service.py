"""Constrained roadmap generation service with stable structure."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pm_pal.prompt_quality.output_validator import validate_output
from pm_pal.schemas.roadmap_schema import (
    RoadmapDiffItem,
    RoadmapDiffOutput,
    RoadmapItem,
    RoadmapOutput,
)
from pm_pal.service.review_service import (
    CANONICAL_REQUEST_FILENAME,
    _load_json_object,
    _load_revision_stage_payload,
    _resolve_run_dir,
)

ROADMAP_ITEM_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "id",
        "title",
        "priority_score",
        "effort_score",
        "risk_score",
        "dependency_ids",
        "target_window",
        "why_now",
        "why_later",
        "de_scope_candidate",
    ],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "title": {"type": "string"},
        "priority_score": {"type": "number"},
        "effort_score": {"type": "number"},
        "risk_score": {"type": "number"},
        "dependency_ids": {"type": "array", "items": {"type": "string"}},
        "target_window": {"type": "string"},
        "why_now": {"type": "string"},
        "why_later": {"type": "string"},
        "de_scope_candidate": {"type": "boolean"},
    },
    "additionalProperties": False,
}

ROADMAP_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version", "roadmap_items"],
    "properties": {
        "version": {"type": "string"},
        "roadmap_items": {"type": "array", "items": ROADMAP_ITEM_JSON_SCHEMA},
    },
    "additionalProperties": False,
}

ROADMAP_PROMPT_TEMPLATE = """You are a product roadmap planner.
Generate roadmap_items from constrained planning input.

Rules:
1) Output must be valid JSON only.
2) Output root must match this schema:
{schema_json}
3) Keep all required fields for each roadmap item.
4) Use stable deterministic values, no random text.
5) target_window must be one of: now, next, later.
6) de_scope_candidate should be true only for low-priority/high-effort items.

Input payload:
{input_json}
"""


@dataclass(slots=True)
class _RiskSignals:
    score: float
    top_reasons: list[str]


def _normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("id", "") or "").strip()
    if not task_id:
        return {}
    return {
        "id": task_id,
        "title": str(task.get("title", "") or "").strip(),
        "depends_on": [
            str(item).strip()
            for item in list(task.get("depends_on", []) or [])
            if str(item).strip()
        ],
        "estimate_days": float(task.get("estimate_days", 0) or 0),
    }


def _normalize_dependencies(dependencies: list[dict[str, Any]]) -> dict[str, set[str]]:
    matrix: dict[str, set[str]] = {}
    for edge in dependencies:
        from_task = str(edge.get("from", "") or edge.get("from_task", "") or "").strip()
        to_task = str(edge.get("to", "") or "").strip()
        if not from_task or not to_task:
            continue
        matrix.setdefault(from_task, set()).add(to_task)
    return matrix


def _normalize_coverage(acceptance_criteria_coverage: Any) -> dict[str, float]:
    if isinstance(acceptance_criteria_coverage, dict):
        normalized: dict[str, float] = {}
        for key, value in acceptance_criteria_coverage.items():
            try:
                normalized[str(key).strip()] = min(1.0, max(0.0, float(value)))
            except (TypeError, ValueError):
                continue
        return normalized
    if isinstance(acceptance_criteria_coverage, list):
        normalized = {}
        for item in acceptance_criteria_coverage:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "").strip()
            if not task_id:
                continue
            value = item.get("coverage")
            try:
                normalized[task_id] = min(1.0, max(0.0, float(value)))
            except (TypeError, ValueError):
                continue
        return normalized
    return {}


def _normalize_business_hint(business_priority_hints: Any) -> dict[str, float]:
    if not isinstance(business_priority_hints, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, value in business_priority_hints.items():
        try:
            normalized[str(key).strip()] = min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            continue
    return normalized


def _risk_severity_weight(severity: str) -> float:
    lookup = {"critical": 1.0, "high": 0.85, "medium": 0.55, "low": 0.25}
    return lookup.get(str(severity or "").strip().lower(), 0.4)


def _compute_risk_signals(
    task: dict[str, Any], risk_items: list[dict[str, Any]]
) -> _RiskSignals:
    task_id = str(task.get("id", "") or "").strip()
    if not task_id:
        return _RiskSignals(score=0.0, top_reasons=[])

    score = 0.0
    reasons: list[str] = []
    for risk in risk_items:
        if not isinstance(risk, dict):
            continue
        related = [
            str(item).strip()
            for item in list(risk.get("task_ids", []) or [])
            if str(item).strip()
        ]
        if related and task_id not in related:
            continue
        risk_weight = _risk_severity_weight(
            str(risk.get("impact", "") or risk.get("severity", ""))
        )
        score += risk_weight
        risk_title = str(
            risk.get("title", "") or risk.get("description", "") or "risk"
        ).strip()
        if risk_title:
            reasons.append(risk_title)
    return _RiskSignals(score=min(10.0, round(score * 4, 2)), top_reasons=reasons[:2])


def _effort_score(task: dict[str, Any]) -> float:
    estimate_days = float(task.get("estimate_days", 0) or 0)
    if estimate_days <= 0:
        return 3.0
    return min(10.0, round(2.5 + estimate_days * 0.9, 2))


def _priority_score(
    *,
    risk_score: float,
    effort_score: float,
    coverage: float,
    business_hint: float,
    has_blockers: bool,
) -> float:
    uncovered_pressure = (1.0 - coverage) * 4.0
    dependency_pressure = 1.5 if has_blockers else 0.0
    priority = (
        4.0
        + risk_score * 0.45
        + uncovered_pressure
        + business_hint * 2.0
        + dependency_pressure
        - effort_score * 0.2
    )
    return max(0.0, min(10.0, round(priority, 2)))


def _target_window(priority_score: float) -> str:
    if priority_score >= 7.5:
        return "now"
    if priority_score >= 5.0:
        return "next"
    return "later"


def _build_why_now(
    task: dict[str, Any], *, coverage: float, risk_score: float, business_hint: float
) -> str:
    reasons: list[str] = []
    if coverage < 0.6:
        reasons.append("acceptance criteria coverage is still low")
    if risk_score >= 6.0:
        reasons.append("risk exposure is material")
    if business_hint >= 0.7:
        reasons.append("business priority hint is high")
    if not reasons:
        reasons.append("this item unlocks planned execution flow")
    return (
        f"{task.get('id', 'task')} should start now because " + "; ".join(reasons) + "."
    )


def _build_why_later(
    task: dict[str, Any],
    *,
    priority_score: float,
    effort_score: float,
    dependency_ids: list[str],
) -> str:
    reasons: list[str] = []
    if dependency_ids:
        reasons.append("it depends on upstream tasks")
    if effort_score >= 8.0:
        reasons.append("implementation effort is relatively high")
    if priority_score < 5.0:
        reasons.append("its urgency is below top-band work")
    if not reasons:
        reasons.append("higher-priority work should complete first")
    return f"{task.get('id', 'task')} can wait because " + "; ".join(reasons) + "."


def _de_scope_candidate(
    priority_score: float, effort_score: float, risk_score: float
) -> bool:
    return priority_score < 4.5 and effort_score >= 7.0 and risk_score <= 5.0


def generate_constrained_roadmap(
    *,
    tasks: list[dict[str, Any]],
    milestones: list[dict[str, Any]] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
    risk_items: list[dict[str, Any]] | None = None,
    acceptance_criteria_coverage: dict[str, Any] | list[dict[str, Any]] | None = None,
    business_priority_hints: dict[str, Any] | None = None,
    version: str = "v1",
) -> dict[str, Any]:
    """Generate deterministic roadmap items from constrained inputs."""

    del milestones  # Reserved for future scoring; kept in API for compatibility.
    normalized_tasks = [_normalize_task(task) for task in tasks]
    normalized_tasks = [task for task in normalized_tasks if task]
    dependency_matrix = _normalize_dependencies(list(dependencies or []))
    coverage_map = _normalize_coverage(acceptance_criteria_coverage)
    business_hints = _normalize_business_hint(business_priority_hints)
    normalized_risks = [
        risk for risk in list(risk_items or []) if isinstance(risk, dict)
    ]

    roadmap_items: list[RoadmapItem] = []
    for task in normalized_tasks:
        task_id = str(task["id"])
        merged_dependency_ids = sorted(
            set(task.get("depends_on", [])) | dependency_matrix.get(task_id, set())
        )
        coverage = coverage_map.get(task_id, 0.5)
        business_hint = business_hints.get(task_id, 0.5)
        risk_signals = _compute_risk_signals(task, normalized_risks)
        effort_score = _effort_score(task)
        priority_score = _priority_score(
            risk_score=risk_signals.score,
            effort_score=effort_score,
            coverage=coverage,
            business_hint=business_hint,
            has_blockers=bool(merged_dependency_ids),
        )
        target_window = _target_window(priority_score)
        item = RoadmapItem(
            id=task_id,
            title=task.get("title", ""),
            priority_score=priority_score,
            effort_score=effort_score,
            risk_score=risk_signals.score,
            dependency_ids=merged_dependency_ids,
            target_window=target_window,
            why_now=_build_why_now(
                task,
                coverage=coverage,
                risk_score=risk_signals.score,
                business_hint=business_hint,
            ),
            why_later=_build_why_later(
                task,
                priority_score=priority_score,
                effort_score=effort_score,
                dependency_ids=merged_dependency_ids,
            ),
            de_scope_candidate=_de_scope_candidate(
                priority_score, effort_score, risk_signals.score
            ),
        )
        roadmap_items.append(item)

    roadmap_items.sort(
        key=lambda item: (-item.priority_score, item.effort_score, item.id)
    )
    payload = RoadmapOutput(version=version, roadmap_items=roadmap_items)
    return payload.model_dump(mode="python")


def diff_roadmap_versions(v1: dict[str, Any], v2: dict[str, Any]) -> dict[str, Any]:
    """Return structured diff between roadmap v1 and v2 payloads."""

    left = validate_roadmap_result(v1)
    right = validate_roadmap_result(v2)
    left_items = {item.id: item for item in left.roadmap_items}
    right_items = {item.id: item for item in right.roadmap_items}
    shared_ids = sorted(set(left_items) & set(right_items))

    added = [
        RoadmapDiffItem(
            id=item_id,
            change_type="added",
            before={},
            after=right_items[item_id].model_dump(mode="python"),
            changed_fields=[],
        )
        for item_id in sorted(set(right_items) - set(left_items))
    ]
    removed = [
        RoadmapDiffItem(
            id=item_id,
            change_type="removed",
            before=left_items[item_id].model_dump(mode="python"),
            after={},
            changed_fields=[],
        )
        for item_id in sorted(set(left_items) - set(right_items))
    ]

    changed: list[RoadmapDiffItem] = []
    unchanged_count = 0
    for item_id in shared_ids:
        before = left_items[item_id].model_dump(mode="python")
        after = right_items[item_id].model_dump(mode="python")
        changed_fields = sorted(
            key for key in set(before) | set(after) if before.get(key) != after.get(key)
        )
        if changed_fields:
            changed.append(
                RoadmapDiffItem(
                    id=item_id,
                    change_type="changed",
                    before=before,
                    after=after,
                    changed_fields=changed_fields,
                )
            )
        else:
            unchanged_count += 1

    payload = RoadmapDiffOutput(
        from_version=left.version,
        to_version=right.version,
        added=added,
        removed=removed,
        changed=changed,
        unchanged_count=unchanged_count,
    )
    return payload.model_dump(mode="python")


def build_roadmap_prompt(input_payload: dict[str, Any]) -> str:
    """Return strict prompt template for low-capability LLMs."""

    import json

    return ROADMAP_PROMPT_TEMPLATE.format(
        schema_json=json.dumps(
            ROADMAP_OUTPUT_JSON_SCHEMA, ensure_ascii=False, indent=2
        ),
        input_json=json.dumps(input_payload, ensure_ascii=False, indent=2),
    )


def validate_roadmap_result(candidate: dict[str, Any]) -> RoadmapOutput:
    """Validate roadmap payload with strict stable schema then pydantic model."""

    validated = validate_output(candidate, ROADMAP_OUTPUT_JSON_SCHEMA)
    return RoadmapOutput.model_validate(validated)


def integrate_with_execution_plan(
    plan: dict[str, Any], roadmap: dict[str, Any]
) -> dict[str, Any]:
    """Attach roadmap ranking back to execution plan task list."""

    normalized_plan = dict(plan or {})
    tasks = [
        dict(item)
        for item in list(normalized_plan.get("tasks", []) or [])
        if isinstance(item, dict)
    ]
    roadmap_output = validate_roadmap_result(roadmap)
    roadmap_by_id = {item.id: item for item in roadmap_output.roadmap_items}
    enriched_tasks: list[dict[str, Any]] = []

    for task in tasks:
        task_id = str(task.get("id", "") or "").strip()
        roadmap_item = roadmap_by_id.get(task_id)
        if roadmap_item is None:
            task["roadmap"] = {}
            enriched_tasks.append(task)
            continue
        task["roadmap"] = {
            "priority_score": roadmap_item.priority_score,
            "effort_score": roadmap_item.effort_score,
            "risk_score": roadmap_item.risk_score,
            "target_window": roadmap_item.target_window,
            "de_scope_candidate": roadmap_item.de_scope_candidate,
        }
        enriched_tasks.append(task)

    normalized_plan["tasks"] = enriched_tasks
    normalized_plan["roadmap"] = roadmap_output.model_dump(mode="python")
    normalized_plan["execution_order"] = [
        item.id for item in roadmap_output.roadmap_items
    ]
    return normalized_plan


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return str(path.read_text(encoding="utf-8") or "")
    except OSError:
        return ""


def _select_roadmap_source(
    run_dir: Path, report_payload: dict[str, Any]
) -> dict[str, str]:
    stage = _load_revision_stage_payload(run_dir, report_payload)
    confirmed_ref = str(stage.get("confirmed_revision_ref", "") or "").strip()
    confirmed_text = (
        _load_text(Path(confirmed_ref))
        if confirmed_ref
        else _load_text(run_dir / "confirmed_prd.md")
    )
    if confirmed_text.strip():
        return {
            "selected_source": "confirmed_revision",
            "source_ref": confirmed_ref
            or str((run_dir / "confirmed_prd.md").resolve()),
            "requirement_doc": confirmed_text,
        }

    original_doc = str(report_payload.get("requirement_doc", "") or "").strip()
    if not original_doc:
        canonical = _load_json_object(run_dir / CANONICAL_REQUEST_FILENAME)
        if isinstance(canonical, dict):
            content = canonical.get("content")
            if isinstance(content, dict):
                original_doc = str(content.get("text", "") or "").strip()
    return {
        "selected_source": "original_prd_with_review",
        "source_ref": "report.requirement_doc",
        "requirement_doc": original_doc,
    }


def _normalize_task_candidates(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = report_payload.get("tasks")
    if not isinstance(tasks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id", "") or "").strip()
        if not task_id:
            continue
        normalized.append(
            {
                "id": task_id,
                "title": str(item.get("title", "") or "").strip(),
                "depends_on": list(item.get("depends_on", []) or []),
                "estimate_days": item.get("estimate_days", 0),
            }
        )
    return normalized


def _roadmap_not_recommended_reason(
    tasks: list[dict[str, Any]], dependencies: list[dict[str, Any]]
) -> str:
    if not tasks:
        return "insufficient_scope:no_tasks"
    if len(tasks) < 2:
        return "roadmap_not_recommended:single_stage_scope"
    if len(tasks) < 3 and not dependencies:
        return "roadmap_not_recommended:limited_dependency_or_priority_tradeoff"
    return ""


def _render_roadmap_markdown(payload: dict[str, Any]) -> str:
    status = str(payload.get("status", "") or "")
    source = (
        payload.get("roadmap_source", {})
        if isinstance(payload.get("roadmap_source"), dict)
        else {}
    )
    source_name = str(source.get("selected_source", "") or "original_prd_with_review")
    if status != "generated":
        reason = str(payload.get("reason", "") or "roadmap_not_recommended")
        return (
            "# Roadmap\n\n"
            f"- Status: `{status or 'not_generated'}`\n"
            f"- Source: `{source_name}`\n"
            f"- Reason: `{reason}`\n\n"
            "Roadmap generation is optional. This run currently does not require phased roadmap planning.\n"
        )

    roadmap = (
        payload.get("roadmap", {}) if isinstance(payload.get("roadmap"), dict) else {}
    )
    items = (
        roadmap.get("roadmap_items", [])
        if isinstance(roadmap.get("roadmap_items"), list)
        else []
    )
    lines = [
        "# Roadmap",
        "",
        f"- Status: `{status}`",
        f"- Source: `{source_name}`",
        "",
        "## Prioritized Items",
    ]
    if not items:
        lines.append("- No roadmap items generated.")
    else:
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('id', '')}` {item.get('title', '')} "
                f"(window: {item.get('target_window', '')}, priority: {item.get('priority_score', 0)})"
            )
    lines.append("")
    return "\n".join(lines)


def generate_roadmap_for_run(
    *,
    run_id: str,
    outputs_root: str | Path = "outputs",
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_path = run_dir / "report.json"
    report_payload = _load_json_object(report_path)
    if not report_payload:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")

    roadmap_source = _select_roadmap_source(run_dir, report_payload)
    tasks = _normalize_task_candidates(report_payload)
    dependencies = (
        report_payload.get("dependencies")
        if isinstance(report_payload.get("dependencies"), list)
        else []
    )
    reason = _roadmap_not_recommended_reason(tasks, dependencies)

    if reason:
        roadmap_generation = {
            "status": "not_recommended",
            "reason": reason,
            "roadmap_source": {
                "selected_source": roadmap_source.get(
                    "selected_source", "original_prd_with_review"
                ),
                "source_ref": roadmap_source.get("source_ref", ""),
            },
            "generated_at": _utc_now_iso(),
        }
    else:
        roadmap_payload = generate_constrained_roadmap(
            tasks=tasks,
            dependencies=dependencies,
            risk_items=report_payload.get("risk_items")
            if isinstance(report_payload.get("risk_items"), list)
            else [],
            milestones=report_payload.get("milestones")
            if isinstance(report_payload.get("milestones"), list)
            else [],
            version=f"roadmap-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        )
        roadmap_generation = {
            "status": "generated",
            "reason": "",
            "roadmap_source": {
                "selected_source": roadmap_source.get(
                    "selected_source", "original_prd_with_review"
                ),
                "source_ref": roadmap_source.get("source_ref", ""),
            },
            "generated_at": _utc_now_iso(),
            "roadmap": roadmap_payload,
        }

    roadmap_json_path = run_dir / "roadmap.json"
    roadmap_md_path = run_dir / "roadmap.md"
    roadmap_json_path.write_text(
        json.dumps(roadmap_generation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    roadmap_md_path.write_text(
        _render_roadmap_markdown(roadmap_generation), encoding="utf-8"
    )

    artifacts = report_payload.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    artifacts.update(
        {
            "roadmap_json": str(roadmap_json_path.resolve()),
            "roadmap_md": str(roadmap_md_path.resolve()),
        }
    )
    report_payload["artifacts"] = artifacts
    report_payload["roadmap_generation"] = roadmap_generation
    report_payload["roadmap_source"] = roadmap_generation.get("roadmap_source", {})
    report_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "run_id": str(run_id).strip(),
        "roadmap_generation": roadmap_generation,
        "artifact_paths": {
            "roadmap_json": str(roadmap_json_path.resolve()),
            "roadmap_md": str(roadmap_md_path.resolve()),
        },
    }
