"""Generate deterministic role-based task bundles from review artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from pm_pal.packs import TaskBundleBuilder, TaskBundleV1

GENERATOR_VERSION = "task_bundle_generator_v1"


@dataclass(frozen=True, slots=True)
class TaskBundleArtifact:
    artifact_key: str
    path: str
    trace: dict[str, Any]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_json_dict(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    return payload if isinstance(payload, dict) else {}


def _load_named_list(path: Path, key: str) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        values = payload.get(key, [])
        if isinstance(values, list):
            return [dict(item) for item in values if isinstance(item, dict)]
    return []


def _copy_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _derive_findings(
    report_payload: dict[str, Any],
    result_payload: dict[str, Any],
    review_report_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    for payload in (review_report_payload, report_payload, result_payload):
        direct = _copy_dict_list(payload.get("findings"))
        if direct:
            return direct

    findings: list[dict[str, Any]] = []
    review_results = _copy_dict_list(
        report_payload.get("review_results")
    ) or _copy_dict_list(result_payload.get("review_results"))
    for item in review_results:
        issues = [
            str(issue).strip()
            for issue in list(item.get("issues", []) or [])
            if str(issue).strip()
        ]
        suggestions = str(item.get("suggestions", "") or "").strip()
        if not (
            issues
            or suggestions
            or item.get("is_ambiguous")
            or not item.get("is_clear", True)
            or not item.get("is_testable", True)
        ):
            continue
        findings.append(
            {
                "title": str(item.get("id", "") or "review-finding").strip(),
                "detail": "; ".join(issues)
                or suggestions
                or str(item.get("description", "") or "").strip(),
                "suggestion": suggestions,
                "requirement_id": str(item.get("id", "") or "").strip(),
                "description": str(item.get("description", "") or "").strip(),
            }
        )
    return findings


def _derive_source_artifacts(run_dir: Path) -> list[str]:
    names = []
    for filename in (
        "report.json",
        "review_report.json",
        "open_questions.json",
        "risk_items.json",
    ):
        if (run_dir / filename).exists():
            names.append(filename)
    return names or ["ReviewState.result"]


def generate_task_bundle_v1_artifact(run_output: dict[str, Any]) -> TaskBundleArtifact:
    run_id = str(run_output.get("run_id", "") or "").strip()
    run_dir_raw = str(run_output.get("run_dir", "") or "").strip()
    if not run_id:
        raise ValueError("run_id is required for task bundle generation")
    if not run_dir_raw:
        raise ValueError("run_dir is required for task bundle generation")

    run_dir = Path(run_dir_raw).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    report_paths = (
        dict(run_output.get("report_paths", {}) or {})
        if isinstance(run_output.get("report_paths"), dict)
        else {}
    )
    result_payload = (
        dict(run_output.get("result", {}) or {})
        if isinstance(run_output.get("result"), dict)
        else {}
    )
    report_payload = _load_json_dict(
        Path(str(report_paths.get("report_json", "") or ""))
    ) or dict(result_payload)
    review_report_payload = _load_json_dict(run_dir / "review_report.json")
    open_questions = _load_named_list(run_dir / "open_questions.json", "open_questions")
    risk_items = _load_named_list(run_dir / "risk_items.json", "risk_items")
    findings = _derive_findings(report_payload, result_payload, review_report_payload)

    builder_inputs = {
        "run_id": run_id,
        "source_artifacts": _derive_source_artifacts(run_dir),
        "requirements": report_payload.get("parsed_items")
        or result_payload.get("parsed_items"),
        "tasks": report_payload.get("tasks")
        or result_payload.get("tasks")
        or (report_payload.get("plan") or {}).get("tasks")
        or (result_payload.get("plan") or {}).get("tasks"),
        "risks": report_payload.get("risks") or result_payload.get("risks"),
        "implementation_plan_output": report_payload.get("implementation_plan")
        or result_payload.get("implementation_plan"),
        "test_plan_output": report_payload.get("test_plan")
        or result_payload.get("test_plan"),
        "codex_prompt_output": report_payload.get("codex_prompt_handoff")
        or result_payload.get("codex_prompt_handoff"),
        "claude_code_prompt_output": report_payload.get("claude_code_prompt_handoff")
        or result_payload.get("claude_code_prompt_handoff"),
        "review_findings": findings,
        "open_questions": open_questions
        or report_payload.get("review_open_questions")
        or result_payload.get("review_open_questions"),
        "risk_items": risk_items
        or report_payload.get("review_risk_items")
        or result_payload.get("review_risk_items"),
        "generated_at": _utc_now_iso(),
    }

    started = perf_counter()
    bundle = TaskBundleBuilder().build(**builder_inputs)
    payload = bundle.model_dump(mode="python")
    output_path = run_dir / "task_bundle_v1.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    duration_ms = round((perf_counter() - started) * 1000)
    return TaskBundleArtifact(
        artifact_key="task_bundle_v1",
        path=str(output_path),
        trace={
            "start": builder_inputs["generated_at"],
            "end": _utc_now_iso(),
            "duration_ms": duration_ms,
            "status": "ok",
            "error_message": "",
            "non_blocking": True,
            "retry": {},
            "output_paths": {"task_bundle_v1": str(output_path)},
            "generator_version": GENERATOR_VERSION,
            "artifact_path": str(output_path),
            "source_artifacts": list(bundle.source_artifacts),
            "task_count": sum(
                len(items)
                for items in bundle.tasks_by_role.model_dump(mode="python").values()
            ),
        },
    )


def validate_task_bundle_v1_payload(payload: dict[str, Any]) -> TaskBundleV1:
    return TaskBundleV1.model_validate(payload)
