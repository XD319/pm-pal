"""Revision generation service for producing revised PRD artifacts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pm_pal.prompt_registry import load_prompt_template
from pm_pal.schemas import RevisionAgentOutput, validate_revision_output
from pm_pal.service.review_service import (
    CANONICAL_REQUEST_FILENAME,
    REVISION_REQUEST_FILENAME,
    _append_audit_event_safe,
    _derive_clarification,
    _is_review_stable_for_revision,
    _load_json_object,
    _load_revision_stage_payload,
    _resolve_run_dir,
    _utc_now_iso,
)
from pm_pal.utils.llm_structured_call import llm_structured_call

REVISED_PRD_FILENAME = "revised_prd.md"
REVISION_SUMMARY_MD_FILENAME = "revision_summary.md"
REVISION_SUMMARY_JSON_FILENAME = "revision_summary.json"


def _ensure_confirmation_prefix(items: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        normalized.append(text if text.startswith("需确认") else f"需确认: {text}")
    return normalized


def _merge_pending_questions(validated_payload: dict[str, Any]) -> list[str]:
    base_pending = [
        str(item).strip()
        for item in validated_payload.get("pending_questions", [])
        if str(item).strip()
    ]
    notes_pending = _ensure_confirmation_prefix(
        [
            str(item).strip()
            for item in validated_payload.get("meeting_notes_pending_confirmations", [])
            if str(item).strip()
        ]
    )
    merged: list[str] = []
    for item in [*base_pending, *notes_pending]:
        if item and item not in merged:
            merged.append(item)
    return merged


def _revision_artifact_paths(run_dir: Path) -> dict[str, str]:
    return {
        "revised_prd": str((run_dir / REVISED_PRD_FILENAME).resolve()),
        "revision_summary_md": str((run_dir / REVISION_SUMMARY_MD_FILENAME).resolve()),
        "revision_summary_json": str(
            (run_dir / REVISION_SUMMARY_JSON_FILENAME).resolve()
        ),
    }


def _resolve_original_prd_text(run_dir: Path, report_payload: dict[str, Any]) -> str:
    from_report = str(report_payload.get("requirement_doc", "") or "").strip()
    if from_report:
        return from_report
    canonical = _load_json_object(run_dir / CANONICAL_REQUEST_FILENAME)
    if isinstance(canonical, dict):
        content = canonical.get("content")
        if isinstance(content, dict):
            text = str(content.get("text", "") or "").strip()
            if text:
                return text
    return ""


def _build_revision_input_payload(
    *,
    run_id: str,
    original_prd: str,
    report_payload: dict[str, Any],
    revision_request_payload: dict[str, Any],
) -> dict[str, Any]:
    source_snapshot = (
        revision_request_payload.get("source_context_snapshot")
        if isinstance(revision_request_payload.get("source_context_snapshot"), dict)
        else {}
    )
    clarification = _derive_clarification(report_payload)
    return {
        "run_id": run_id,
        "original_prd": original_prd,
        "review_result": {
            "summary": report_payload.get("summary", {}),
            "findings": report_payload.get("findings", []),
            "risk_items": report_payload.get("risk_items", []),
            "gating": report_payload.get("gating", {}),
            "reviewers_used": report_payload.get("reviewers_used", []),
        },
        "clarification": {
            "triggered": bool(clarification.get("triggered")),
            "status": str(clarification.get("status", "") or ""),
            "stable_conclusions": clarification.get("stable_conclusions", []),
            "resolved_answers": clarification.get("resolved_answers", []),
        },
        "revision_request": {
            "selected_review_basis": str(
                revision_request_payload.get("selected_review_basis", "") or ""
            ),
            "extra_instructions": str(
                revision_request_payload.get("extra_instructions", "") or ""
            ),
        },
        "meeting_notes": {
            "text": str(revision_request_payload.get("meeting_notes_text", "") or ""),
            "file_ref": revision_request_payload.get("meeting_notes_file_ref", {}),
        },
        "source_context_snapshot": source_snapshot,
    }


def _build_revision_summary_markdown(payload: dict[str, Any]) -> str:
    sources_used = [
        str(item) for item in payload.get("sources_used", []) if str(item).strip()
    ]
    major_changes = [
        str(item) for item in payload.get("major_changes", []) if str(item).strip()
    ]
    unadopted = [
        str(item)
        for item in payload.get("unadopted_review_suggestions", [])
        if str(item).strip()
    ]
    pending = [
        str(item) for item in payload.get("pending_questions", []) if str(item).strip()
    ]
    user_direct = [
        str(item)
        for item in payload.get("user_direct_requirements_applied", [])
        if str(item).strip()
    ]
    notes_resolutions = [
        str(item)
        for item in payload.get("meeting_notes_resolutions", [])
        if str(item).strip()
    ]
    notes_changes = [
        str(item)
        for item in payload.get("meeting_notes_change_points", [])
        if str(item).strip()
    ]
    notes_pending = [
        str(item)
        for item in payload.get("meeting_notes_pending_confirmations", [])
        if str(item).strip()
    ]
    rationale = str(payload.get("rationale", "") or "").strip()

    def _bullets(items: list[str], empty_text: str) -> str:
        if not items:
            return f"- {empty_text}"
        return "\n".join(f"- {item}" for item in items)

    return (
        "# Revision Summary\n\n"
        "## Sources Used\n"
        f"{_bullets(sources_used, 'No explicit source markers returned by revision agent.')}\n\n"
        "## Major Changes\n"
        f"{_bullets(major_changes, 'No major change was listed.')}\n\n"
        "## Unadopted Review Suggestions\n"
        f"{_bullets(unadopted, 'No unadopted review suggestion was listed.')}\n\n"
        "## Pending Confirmation Questions\n"
        f"{_bullets(pending, 'No pending confirmation questions were listed.')}\n\n"
        "## User Direct Requirements Applied\n"
        f"{_bullets(user_direct, 'No user direct requirement was listed as applied.')}\n\n"
        "## Meeting Notes Impact\n"
        "### Resolutions\n"
        f"{_bullets(notes_resolutions, 'No meeting-note resolution was identified.')}\n\n"
        "### Change Points\n"
        f"{_bullets(notes_changes, 'No meeting-note-driven change point was identified.')}\n\n"
        "### Need Confirmation\n"
        f"{_bullets(notes_pending, 'No meeting-note conflicts requiring confirmation were listed.')}\n\n"
        "## Revision Rationale\n"
        f"{rationale or 'No rationale provided.'}\n"
    )


def _update_report_artifacts(run_dir: Path, artifact_paths: dict[str, str]) -> None:
    report_path = run_dir / "report.json"
    payload = _load_json_object(report_path)
    if not payload:
        return
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    artifacts.update(artifact_paths)
    payload["artifacts"] = artifacts
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def generate_revision_for_run_async(
    *,
    run_id: str,
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_payload = _load_json_object(run_dir / "report.json")
    if not report_payload:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")
    if not _is_review_stable_for_revision(report_payload):
        raise ValueError(f"review is not stable for revision: run_id={run_id}")

    stage_payload = _load_revision_stage_payload(run_dir, report_payload)
    if not stage_payload.get("entered_revision"):
        raise ValueError(f"revision not entered for run_id={run_id}")

    revision_request_payload = _load_json_object(run_dir / REVISION_REQUEST_FILENAME)
    if not revision_request_payload:
        raise ValueError(f"{REVISION_REQUEST_FILENAME} not found for run_id={run_id}")

    original_prd = _resolve_original_prd_text(run_dir, report_payload)
    if not original_prd.strip():
        raise ValueError(f"original PRD text unavailable for run_id={run_id}")

    artifact_paths = _revision_artifact_paths(run_dir)
    summary_json_path = Path(artifact_paths["revision_summary_json"])
    revision_started_at = _utc_now_iso()
    stage_payload["status"] = "running"
    stage_payload["updated_at"] = revision_started_at
    (run_dir / "revision_stage.json").write_text(
        json.dumps(stage_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        prompt_record = load_prompt_template("revision")
        model_input = _build_revision_input_payload(
            run_id=run_id,
            original_prd=original_prd,
            report_payload=report_payload,
            revision_request_payload=revision_request_payload,
        )
        prompt_text = (
            f"{prompt_record.system_prompt}\n\n"
            f"{prompt_record.user_prompt_template.format(input_text=json.dumps(model_input, ensure_ascii=False, indent=2))}"
        )
        call_meta = {"agent_name": "revision", "run_id": run_id}
        raw_output = await llm_structured_call(
            prompt=prompt_text,
            schema=RevisionAgentOutput,
            metadata=call_meta,
        )
        validated = validate_revision_output(raw_output).model_dump(mode="python")
        validated["pending_questions"] = _merge_pending_questions(validated)
        validated["meeting_notes_pending_confirmations"] = _ensure_confirmation_prefix(
            [
                str(item).strip()
                for item in validated.get("meeting_notes_pending_confirmations", [])
                if str(item).strip()
            ]
        )

        revised_prd_markdown = str(
            validated.get("revised_prd_markdown", "") or ""
        ).strip()
        if not revised_prd_markdown:
            raise ValueError("revision agent returned empty revised_prd_markdown")

        Path(artifact_paths["revised_prd"]).write_text(
            revised_prd_markdown + "\n", encoding="utf-8"
        )
        summary_md = _build_revision_summary_markdown(validated)
        Path(artifact_paths["revision_summary_md"]).write_text(
            summary_md, encoding="utf-8"
        )

        summary_json = {
            "run_id": run_id,
            "status": "completed",
            "generated_at": _utc_now_iso(),
            "sources_used": validated.get("sources_used", []),
            "major_changes": validated.get("major_changes", []),
            "rationale": validated.get("rationale", ""),
            "unadopted_review_suggestions": validated.get(
                "unadopted_review_suggestions", []
            ),
            "pending_questions": validated.get("pending_questions", []),
            "user_direct_requirements_applied": validated.get(
                "user_direct_requirements_applied", []
            ),
            "meeting_notes_resolutions": validated.get("meeting_notes_resolutions", []),
            "meeting_notes_change_points": validated.get(
                "meeting_notes_change_points", []
            ),
            "meeting_notes_pending_confirmations": validated.get(
                "meeting_notes_pending_confirmations", []
            ),
            "artifacts": artifact_paths,
        }
        summary_json_path.write_text(
            json.dumps(summary_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        stage_payload["status"] = "completed"
        stage_payload["decision_required"] = False
        stage_payload["updated_at"] = _utc_now_iso()
        stage_payload.pop("error_reason", None)
        (run_dir / "revision_stage.json").write_text(
            json.dumps(stage_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _update_report_artifacts(run_dir, artifact_paths)
        _append_audit_event_safe(
            run_dir,
            operation="revision_generation",
            status="completed",
            run_id=run_id,
            audit_context=audit_context,
            details={
                "artifact_paths": artifact_paths,
                "sources_used": summary_json["sources_used"],
                "major_change_count": len(summary_json["major_changes"]),
            },
        )
        return {
            "run_id": run_id,
            "revision_status": "completed",
            "artifact_paths": artifact_paths,
            "revision_summary": summary_json,
        }
    except Exception as exc:
        stage_payload["status"] = "failed"
        stage_payload["updated_at"] = _utc_now_iso()
        stage_payload["error_reason"] = str(exc)
        (run_dir / "revision_stage.json").write_text(
            json.dumps(stage_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        failure_summary = {
            "run_id": run_id,
            "status": "failed",
            "generated_at": _utc_now_iso(),
            "error_reason": str(exc),
            "artifacts": artifact_paths,
        }
        summary_json_path.write_text(
            json.dumps(failure_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _append_audit_event_safe(
            run_dir,
            operation="revision_generation",
            status="failed",
            run_id=run_id,
            audit_context=audit_context,
            details={"error_reason": str(exc)},
        )
        return {
            "run_id": run_id,
            "revision_status": "failed",
            "error_reason": str(exc),
            "artifact_paths": artifact_paths,
            "revision_summary": failure_summary,
        }


def generate_revision_for_run(
    *,
    run_id: str,
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return asyncio.run(
            generate_revision_for_run_async(
                run_id=run_id,
                outputs_root=outputs_root,
                audit_context=audit_context,
            )
        )
    except RuntimeError as exc:
        if "asyncio.run() cannot be called from a running event loop" in str(exc):
            raise RuntimeError(
                "generate_revision_for_run cannot run inside an active event loop; use generate_revision_for_run_async"
            ) from exc
        raise
