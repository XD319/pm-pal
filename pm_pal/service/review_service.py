"""Reusable review service API for CLI/FastAPI/MCP entrypoints."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from pm_pal.connectors import ConnectorRegistry, get_connector_error_payload
from pm_pal.review.aggregator import _render_review_report, _render_summary
from pm_pal.review.clarification_gate import (
    apply_clarification_answers,
    build_clarification_payload,
)
from pm_pal.review.ingress_normalization import normalize_ingress_request
from pm_pal.review.profile_router import load_profile_pack, route_review_profile
from pm_pal.connectors.feishu import (
    FeishuAuthenticationError,
    FeishuDocumentNotFoundError,
    FeishuPermissionDeniedError,
    FeishuUnsupportedDocumentTypeError,
)
from pm_pal.draft_generator import generate_prd_v1_artifact
from pm_pal.handoff import (
    render_claude_code_prompt,
    render_codex_prompt,
    render_openclaw_prompt,
)
from pm_pal.task_bundle_generator import generate_task_bundle_v1_artifact
from pm_pal.templates import get_adapter_prompt_template
from pm_pal.monitoring import append_audit_event, retry_metadata_for_status
from pm_pal.notifications import NotificationType, dispatch_notification
from pm_pal.service.artifact_patch_service import (
    apply_artifact_patch_async,
    build_clarification_to_patch_prompt,
)
from pm_pal.service.selective_rerun_service import build_rerun_plan_async
from pm_pal.packs import (
    ArtifactSplitter,
    DeliveryBundle,
    DeliveryBundleBuilder,
    ExecutionPackBuilder,
    ImplementationPackBuilder,
    TestPackBuilder,
    approve_bundle,
    block_by_risk,
    request_more_info,
    reset_to_draft,
)
from pm_pal.packs.approval import build_approval_record
from pm_pal.run_review import make_unique_run_id, run_review
from pm_pal.runtime.config.config import runtime_config_overrides
from pm_pal.service.report_service import RUN_ID_PATTERN
from pm_pal.server.sse import ProgressBroadcaster
from pm_pal.utils.collections import copy_dict_list as _copy_dict_list
from pm_pal.utils.json import (
    load_json_object as _load_json_object,
    write_json_object as _write_json_object,
)
from pm_pal.workspace import ArtifactRepository
from pm_pal.workspace import ReviewWorkspaceRepository
from pm_pal.utils.logging import RunLogContext, get_logger
from pm_pal.utils.time import utc_now_iso as _utc_now_iso

log = get_logger("service.review")
CANONICAL_REQUEST_FILENAME = "canonical_review_request.json"
REVISION_STAGE_FILENAME = "revision_stage.json"
REVISION_REQUEST_FILENAME = "revision_request.json"
MEETING_NOTES_FILENAME = "meeting_notes.txt"
MEETING_NOTES_META_FILENAME = "meeting_notes.json"
CONFIRMED_PRD_FILENAME = "confirmed_prd.md"
_REVISION_ENTRY_DECISIONS = {
    "generate_from_review",
    "custom_requirements",
    "upload_notes",
}
_REVISION_SKIP_DECISION = "skip_revision"
_SUPPORTED_MEETING_NOTES_EXTENSIONS = {"txt", "md"}


@dataclass(slots=True)
class ReviewResultSummary:
    run_id: str
    report_md_path: str
    report_json_path: str
    high_risk_ratio: float
    coverage_ratio: float
    revision_round: int
    status: str
    run_trace_path: str = ""
    implementation_pack_path: str = ""
    test_pack_path: str = ""
    execution_pack_path: str = ""
    prd_v1_path: str = ""
    task_bundle_v1_path: str = ""
    delivery_bundle_path: str = ""

    def to_report_paths(self) -> dict[str, str]:
        return {
            "report_md": self.report_md_path,
            "report_json": self.report_json_path,
            "run_trace": self.run_trace_path,
            "implementation_pack": self.implementation_pack_path,
            "test_pack": self.test_pack_path,
            "execution_pack": self.execution_pack_path,
            "prd_v1": self.prd_v1_path,
            "task_bundle_v1": self.task_bundle_v1_path,
            "delivery_bundle": self.delivery_bundle_path,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReviewRunNotFoundError(FileNotFoundError):
    """Raised when a review run directory cannot be found."""


class ReviewResultNotReadyError(RuntimeError):
    """Raised when a review run exists but report.json is not available yet."""


class ReviewArtifactNotFoundError(FileNotFoundError):
    """Raised when a requested review artifact cannot be found."""


@dataclass(frozen=True, slots=True)
class ReviewInputErrorInfo:
    code: str
    message: str


def classify_review_input_error(exc: Exception) -> ReviewInputErrorInfo | None:
    if isinstance(exc, FeishuAuthenticationError):
        return ReviewInputErrorInfo(code="AUTHENTICATION_FAILED", message=str(exc))
    if isinstance(exc, FeishuPermissionDeniedError):
        return ReviewInputErrorInfo(code="PERMISSION_DENIED", message=str(exc))
    if isinstance(exc, FeishuDocumentNotFoundError):
        return ReviewInputErrorInfo(code="DOCUMENT_NOT_FOUND", message=str(exc))
    if isinstance(exc, FeishuUnsupportedDocumentTypeError):
        return ReviewInputErrorInfo(code="UNSUPPORTED_DOCUMENT_TYPE", message=str(exc))

    connector_error = get_connector_error_payload(exc)
    if connector_error is None:
        return None

    code_mapping = {
        "authentication_failed": "AUTHENTICATION_FAILED",
        "permission_denied": "PERMISSION_DENIED",
        "not_found": "DOCUMENT_NOT_FOUND",
        "unsupported_source": "UNSUPPORTED_SOURCE",
        "invalid_source": "INVALID_INPUT",
        "network_unavailable": "NETWORK_UNAVAILABLE",
    }
    mapped_code = code_mapping.get(str(connector_error.code or ""))
    if not mapped_code:
        return None
    return ReviewInputErrorInfo(code=mapped_code, message=connector_error.message)


_REVIEW_RESULT_ARTIFACT_FILENAMES: dict[str, str] = {
    "report_md": "report.md",
    "report_json": "report.json",
    "run_trace": "run_trace.json",
    "prd_v1": "prd_v1.md",
    "task_bundle_v1": "task_bundle_v1.json",
    "review_report_json": "review_report.json",
    "risk_items_json": "risk_items.json",
    "open_questions_json": "open_questions.json",
    "review_summary_md": "review_summary.md",
    "revised_prd": "revised_prd.md",
    "confirmed_prd": "confirmed_prd.md",
    "roadmap_md": "roadmap.md",
    "roadmap_json": "roadmap.json",
    "revision_summary_md": "revision_summary.md",
    "revision_summary_json": "revision_summary.json",
}

_LLM_OVERRIDE_OPTION_KEYS: dict[str, str] = {
    "llm": "LLM",
    "temperature": "TEMPERATURE",
    "llm_kwargs": "LLM_KWARGS",
    "reasoning_effort": "REASONING_EFFORT",
}

# Legacy tier option keys collapse onto the single LLM override :-)
_LEGACY_LLM_OPTION_KEYS = ("llm", "smart_llm", "fast_llm", "strategic_llm")
_LEGACY_LLM_RUNTIME_KEYS = ("LLM", "SMART_LLM", "FAST_LLM", "STRATEGIC_LLM")


def _resolve_llm_config_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(overrides, dict):
        return {}

    resolved: dict[str, Any] = {}
    for option_key in _LEGACY_LLM_OPTION_KEYS:
        if option_key in overrides and overrides.get(option_key) is not None:
            resolved["LLM"] = overrides.get(option_key)
            break
    else:
        for runtime_key in _LEGACY_LLM_RUNTIME_KEYS:
            if runtime_key in overrides and overrides.get(runtime_key) is not None:
                resolved["LLM"] = overrides.get(runtime_key)
                break

    for option_key, runtime_key in _LLM_OVERRIDE_OPTION_KEYS.items():
        if runtime_key == "LLM":
            continue
        if option_key in overrides and overrides.get(option_key) is not None:
            resolved[runtime_key] = overrides.get(option_key)
            continue
        if runtime_key in overrides and overrides.get(runtime_key) is not None:
            resolved[runtime_key] = overrides.get(runtime_key)
    return resolved


def _resolve_review_result_artifact_paths(
    run_dir: Path,
    report_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    artifact_paths: dict[str, str] = {}
    for key, filename in _REVIEW_RESULT_ARTIFACT_FILENAMES.items():
        candidate = run_dir / filename
        if candidate.exists() and candidate.is_file():
            artifact_paths[key] = str(candidate)

    if not isinstance(report_payload, dict):
        return artifact_paths

    candidate_maps: list[dict[str, Any]] = []
    report_artifacts = report_payload.get("artifacts")
    if isinstance(report_artifacts, dict):
        candidate_maps.append(report_artifacts)

    parallel_review = _extract_parallel_review_payload(report_payload)
    parallel_artifacts = parallel_review.get("artifacts")
    if isinstance(parallel_artifacts, dict):
        candidate_maps.append(parallel_artifacts)

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    parallel_meta_artifacts = parallel_review_meta.get("artifact_paths")
    if isinstance(parallel_meta_artifacts, dict):
        candidate_maps.append(parallel_meta_artifacts)

    for key in _REVIEW_RESULT_ARTIFACT_FILENAMES:
        if key in artifact_paths:
            continue
        for artifact_map in candidate_maps:
            raw_path = str(artifact_map.get(key, "") or "").strip()
            if not raw_path:
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = run_dir / candidate
            if candidate.exists() and candidate.is_file():
                artifact_paths[key] = str(candidate.resolve())
                break

    return artifact_paths


def _derive_artifact_patch(report_payload: dict[str, Any]) -> dict[str, Any]:
    artifact_patch = report_payload.get("artifact_patch")
    return dict(artifact_patch) if isinstance(artifact_patch, dict) else {}


def _revision_stage_path(run_dir: Path) -> Path:
    return run_dir / REVISION_STAGE_FILENAME


def _is_review_stable_for_revision(report_payload: dict[str, Any]) -> bool:
    if _derive_status(report_payload) != "completed":
        return False
    clarification = _derive_clarification(report_payload)
    if not clarification.get("triggered"):
        return True
    return str(clarification.get("status", "not_needed") or "not_needed") != "pending"


def _default_revision_stage_payload(report_payload: dict[str, Any]) -> dict[str, Any]:
    available = _is_review_stable_for_revision(report_payload)
    return {
        "status": "prompted" if available else "unavailable",
        "decision": "",
        "entered_revision": False,
        "available": available,
        "decision_required": available,
        "allow_continue_without_revision": available,
        "revision_confirmed": False,
        "confirmed_revision_ref": "",
        "draft_revision_ref": "",
        "preferred_prd_source": "original_prd",
        "preferred_prd_ref": "report.requirement_doc",
        "updated_at": "",
    }


def _with_revision_preferences(
    run_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    revised_path = run_dir / "revised_prd.md"
    confirmed_path = run_dir / CONFIRMED_PRD_FILENAME
    draft_ref = (
        str(revised_path.resolve())
        if revised_path.exists() and revised_path.is_file()
        else ""
    )
    confirmed_ref = (
        str(confirmed_path.resolve())
        if confirmed_path.exists() and confirmed_path.is_file()
        else ""
    )
    revision_confirmed = bool(payload.get("revision_confirmed")) and bool(confirmed_ref)
    if revision_confirmed:
        preferred_source = "confirmed_revision"
        preferred_ref = confirmed_ref
    elif draft_ref:
        preferred_source = "unconfirmed_revision_draft"
        preferred_ref = draft_ref
    else:
        preferred_source = "original_prd"
        preferred_ref = "report.requirement_doc"
    return {
        **payload,
        "revision_confirmed": revision_confirmed,
        "confirmed_revision_ref": confirmed_ref if revision_confirmed else "",
        "draft_revision_ref": draft_ref,
        "preferred_prd_source": preferred_source,
        "preferred_prd_ref": preferred_ref,
    }


def _load_revision_stage_payload(
    run_dir: Path, report_payload: dict[str, Any]
) -> dict[str, Any]:
    default_payload = _default_revision_stage_payload(report_payload)
    stage_path = _revision_stage_path(run_dir)
    if not stage_path.exists() or not stage_path.is_file():
        return default_payload

    persisted = _load_json_object(stage_path)
    if not isinstance(persisted, dict):
        return default_payload

    normalized = {**default_payload, **persisted}
    normalized["available"] = default_payload["available"]
    if (
        not default_payload["available"]
        and str(normalized.get("status", "") or "").strip() == "prompted"
    ):
        normalized["status"] = "unavailable"
    normalized["decision_required"] = bool(
        normalized["available"]
        and str(normalized.get("status", "") or "").strip() == "prompted"
    )
    normalized["allow_continue_without_revision"] = bool(normalized["available"])
    return _with_revision_preferences(run_dir, normalized)


def record_revision_stage_decision(
    *,
    run_id: str,
    decision: str,
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_payload = _load_json_object(run_dir / "report.json")
    if not report_payload:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")

    current_stage = _load_revision_stage_payload(run_dir, report_payload)
    if not current_stage.get("available"):
        raise ValueError(f"revision stage not available for run_id={run_id}")

    normalized_decision = str(decision or "").strip()
    if normalized_decision in _REVISION_ENTRY_DECISIONS:
        next_status = "collecting_inputs"
        entered_revision = True
    elif normalized_decision == _REVISION_SKIP_DECISION:
        next_status = "skipped"
        entered_revision = False
    else:
        raise ValueError(
            f"unsupported revision decision for run_id={run_id}: {normalized_decision}"
        )

    payload = {
        "status": next_status,
        "decision": normalized_decision,
        "entered_revision": entered_revision,
        "available": True,
        "decision_required": False,
        "allow_continue_without_revision": True,
        "revision_confirmed": False,
        "confirmed_revision_ref": "",
        "updated_at": _utc_now_iso(),
    }
    payload = _with_revision_preferences(run_dir, payload)
    _write_json_object(_revision_stage_path(run_dir), payload)
    _append_audit_event_safe(
        run_dir,
        operation="revision_stage_decision",
        status=next_status,
        run_id=run_id,
        audit_context=audit_context,
        details={
            "decision": normalized_decision,
            "entered_revision": entered_revision,
        },
    )
    return payload


def _build_source_context_snapshot(report_payload: dict[str, Any]) -> dict[str, Any]:
    clarification = _derive_clarification(report_payload)
    return {
        "review_status": _derive_status(report_payload),
        "review_summary": report_payload.get("summary")
        if isinstance(report_payload.get("summary"), dict)
        else {},
        "gating": _derive_gating(report_payload),
        "clarification": {
            "triggered": bool(clarification.get("triggered")),
            "status": str(clarification.get("status", "") or ""),
            "stable_conclusions": clarification.get("stable_conclusions", []),
            "resolved_answers": clarification.get("resolved_answers", []),
        },
        "reviewers_used": _derive_reviewers_used(report_payload),
        "risk_items": report_payload.get("risk_items", []),
        "findings": report_payload.get("findings", []),
    }


def record_revision_input(
    *,
    run_id: str,
    selected_review_basis: str,
    extra_instructions: str = "",
    meeting_notes_text: str = "",
    meeting_notes_file_ref: dict[str, Any] | None = None,
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_payload = _load_json_object(run_dir / "report.json")
    if not report_payload:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")

    current_stage = _load_revision_stage_payload(run_dir, report_payload)
    if not current_stage.get("available"):
        raise ValueError(f"revision stage not available for run_id={run_id}")
    if not current_stage.get("entered_revision"):
        raise ValueError(f"revision not entered for run_id={run_id}")

    normalized_basis = str(selected_review_basis or "").strip()
    if normalized_basis not in {
        "all_review_suggestions",
        "partial_review_suggestions",
        "user_only",
    }:
        raise ValueError(
            f"unsupported selected_review_basis for run_id={run_id}: {normalized_basis}"
        )

    normalized_extra = str(extra_instructions or "").strip()
    normalized_notes = str(meeting_notes_text or "").strip()
    normalized_file_ref = (
        dict(meeting_notes_file_ref) if isinstance(meeting_notes_file_ref, dict) else {}
    )
    filename = str(normalized_file_ref.get("filename", "") or "").strip()
    extension = str(normalized_file_ref.get("extension", "") or "").strip().lower()
    if not extension and "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()

    if normalized_file_ref:
        if not normalized_notes:
            raise ValueError(
                "meeting notes file upload requires extracted text content"
            )
        if extension and extension not in _SUPPORTED_MEETING_NOTES_EXTENSIONS:
            raise ValueError(f"unsupported meeting notes file extension: .{extension}")
        normalized_file_ref["filename"] = filename
        if extension:
            normalized_file_ref["extension"] = extension

    source_context_snapshot = _build_source_context_snapshot(report_payload)
    revision_request_payload = {
        "run_id": str(run_id).strip(),
        "selected_review_basis": normalized_basis,
        "extra_instructions": normalized_extra,
        "meeting_notes_text": normalized_notes,
        "meeting_notes_file_ref": normalized_file_ref,
        "source_context_snapshot": source_context_snapshot,
        "created_at": _utc_now_iso(),
    }
    _write_json_object(run_dir / REVISION_REQUEST_FILENAME, revision_request_payload)

    if normalized_notes:
        (run_dir / MEETING_NOTES_FILENAME).write_text(
            normalized_notes, encoding="utf-8"
        )
    else:
        (run_dir / MEETING_NOTES_FILENAME).unlink(missing_ok=True)

    if normalized_file_ref:
        _write_json_object(run_dir / MEETING_NOTES_META_FILENAME, normalized_file_ref)
    else:
        (run_dir / MEETING_NOTES_META_FILENAME).unlink(missing_ok=True)

    next_stage = {
        **current_stage,
        "status": "inputs_recorded",
        "decision_required": False,
        "revision_confirmed": False,
        "confirmed_revision_ref": "",
        "updated_at": _utc_now_iso(),
    }
    next_stage = _with_revision_preferences(run_dir, next_stage)
    _write_json_object(_revision_stage_path(run_dir), next_stage)
    _append_audit_event_safe(
        run_dir,
        operation="revision_input_recorded",
        status="inputs_recorded",
        run_id=run_id,
        audit_context=audit_context,
        details={
            "selected_review_basis": normalized_basis,
            "has_extra_instructions": bool(normalized_extra),
            "has_meeting_notes_text": bool(normalized_notes),
            "has_meeting_notes_file_ref": bool(normalized_file_ref),
        },
    )

    return {
        "run_id": str(run_id).strip(),
        "revision_stage": next_stage,
        "revision_request_path": str((run_dir / REVISION_REQUEST_FILENAME).resolve()),
        "meeting_notes_path": str((run_dir / MEETING_NOTES_FILENAME).resolve())
        if normalized_notes
        else "",
        "meeting_notes_meta_path": str(
            (run_dir / MEETING_NOTES_META_FILENAME).resolve()
        )
        if normalized_file_ref
        else "",
    }


def confirm_revision_action(
    *,
    run_id: str,
    action: str,
    additional_requirements: str = "",
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_payload = _load_json_object(run_dir / "report.json")
    if not report_payload:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")

    current_stage = _load_revision_stage_payload(run_dir, report_payload)
    normalized_action = str(action or "").strip()
    normalized_requirements = str(additional_requirements or "").strip()

    if normalized_action == "confirm_revision":
        revised_path = run_dir / "revised_prd.md"
        if not revised_path.exists() or not revised_path.is_file():
            raise ValueError(f"revised_prd.md not found for run_id={run_id}")
        confirmed_path = run_dir / CONFIRMED_PRD_FILENAME
        confirmed_path.write_text(
            revised_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        next_stage = {
            **current_stage,
            "status": "confirmed",
            "decision_required": False,
            "revision_confirmed": True,
            "confirmed_revision_ref": str(confirmed_path.resolve()),
            "updated_at": _utc_now_iso(),
        }
        audit_operation = "revision_confirmed"
        audit_status = "confirmed"
        audit_details = {"confirmed_revision_ref": next_stage["confirmed_revision_ref"]}
    elif normalized_action == "regenerate_revision":
        revision_request_path = run_dir / REVISION_REQUEST_FILENAME
        revision_request = (
            _load_json_object(revision_request_path)
            if revision_request_path.exists()
            else {}
        )
        if not isinstance(revision_request, dict):
            revision_request = {}
        existing_extra = str(
            revision_request.get("extra_instructions", "") or ""
        ).strip()
        merged_extra = existing_extra
        if normalized_requirements:
            merged_extra = (
                f"{existing_extra}\n\n[additional_requirements]\n{normalized_requirements}"
                if existing_extra
                else normalized_requirements
            )
        revision_request.update(
            {
                "run_id": str(run_id).strip(),
                "selected_review_basis": str(
                    revision_request.get("selected_review_basis")
                    or "all_review_suggestions"
                ),
                "extra_instructions": merged_extra,
                "meeting_notes_text": str(
                    revision_request.get("meeting_notes_text") or ""
                ),
                "meeting_notes_file_ref": revision_request.get("meeting_notes_file_ref")
                if isinstance(revision_request.get("meeting_notes_file_ref"), dict)
                else {},
                "source_context_snapshot": _build_source_context_snapshot(
                    report_payload
                ),
                "created_at": _utc_now_iso(),
            }
        )
        _write_json_object(revision_request_path, revision_request)
        next_stage = {
            **current_stage,
            "status": "inputs_recorded",
            "decision": current_stage.get("decision") or "custom_requirements",
            "entered_revision": True,
            "decision_required": False,
            "revision_confirmed": False,
            "confirmed_revision_ref": "",
            "updated_at": _utc_now_iso(),
        }
        audit_operation = "revision_regeneration_requested"
        audit_status = "queued"
        audit_details = {"has_additional_requirements": bool(normalized_requirements)}
    elif normalized_action == "continue_without_revision":
        next_stage = {
            **current_stage,
            "status": "skipped_unconfirmed",
            "decision": _REVISION_SKIP_DECISION,
            "entered_revision": False,
            "decision_required": False,
            "revision_confirmed": False,
            "confirmed_revision_ref": "",
            "updated_at": _utc_now_iso(),
        }
        audit_operation = "revision_skipped_unconfirmed"
        audit_status = "skipped"
        audit_details = {}
    else:
        raise ValueError(
            f"unsupported revision confirm action for run_id={run_id}: {normalized_action}"
        )

    next_stage = _with_revision_preferences(run_dir, next_stage)
    _write_json_object(_revision_stage_path(run_dir), next_stage)
    _append_audit_event_safe(
        run_dir,
        operation=audit_operation,
        status=audit_status,
        run_id=run_id,
        audit_context=audit_context,
        details=audit_details,
    )
    return next_stage


def get_review_result_payload(
    *,
    run_id: str,
    outputs_root: str | Path = "outputs",
) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    run_dir = Path(outputs_root) / normalized_run_id
    if not run_dir.exists() or not run_dir.is_dir():
        raise ReviewRunNotFoundError(f"run_id not found: {normalized_run_id}")

    report_path = run_dir / "report.json"
    if not report_path.exists() or not report_path.is_file():
        raise ReviewResultNotReadyError(
            f"report.json not ready for run_id={normalized_run_id}"
        )

    try:
        result = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"report.json parse failed: {exc}") from exc

    if not isinstance(result, (dict, list)):
        raise ValueError("report.json must contain a JSON object or array")

    artifact_paths = _resolve_review_result_artifact_paths(
        run_dir,
        report_payload=result if isinstance(result, dict) else None,
    )
    status = _derive_status(result) if isinstance(result, dict) else "completed"
    gating = _derive_gating(result) if isinstance(result, dict) else {}
    reviewers_used = _derive_reviewers_used(result) if isinstance(result, dict) else []
    reviewers_skipped = (
        _derive_reviewers_skipped(result) if isinstance(result, dict) else []
    )
    mode = _derive_review_mode(result) if isinstance(result, dict) else "quick"
    return {
        "run_id": normalized_run_id,
        "status": status,
        "mode": mode,
        "gating": gating,
        "clarification": _derive_clarification(result)
        if isinstance(result, dict)
        else {},
        "revision_stage": _load_revision_stage_payload(run_dir, result)
        if isinstance(result, dict)
        else {},
        "artifact_patch": _derive_artifact_patch(result)
        if isinstance(result, dict)
        else {},
        "reviewers_used": reviewers_used,
        "reviewers_skipped": reviewers_skipped,
        "result": result,
        "artifact_paths": artifact_paths,
    }


def get_review_artifact_preview_payload(
    *,
    run_id: str,
    artifact_key: str,
    outputs_root: str | Path = "outputs",
) -> dict[str, Any]:
    normalized_run_id = str(run_id or "").strip()
    normalized_artifact_key = str(artifact_key or "").strip()
    result_payload = get_review_result_payload(
        run_id=normalized_run_id, outputs_root=outputs_root
    )
    artifact_paths = result_payload.get("artifact_paths", {})
    raw_path = str(artifact_paths.get(normalized_artifact_key, "") or "").strip()
    if not raw_path:
        raise ReviewArtifactNotFoundError(
            f"artifact '{normalized_artifact_key}' not found for run_id={normalized_run_id}"
        )

    run_dir = (Path(outputs_root) / normalized_run_id).resolve()
    artifact_path = Path(raw_path)
    if not artifact_path.is_absolute():
        artifact_path = run_dir / artifact_path
    artifact_path = artifact_path.resolve()

    try:
        artifact_path.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(
            f"artifact path escapes run directory for run_id={normalized_run_id}: {artifact_path}"
        ) from exc

    if not artifact_path.exists() or not artifact_path.is_file():
        raise ReviewArtifactNotFoundError(
            f"artifact '{normalized_artifact_key}' is unavailable for run_id={normalized_run_id}"
        )

    try:
        content = artifact_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"artifact preview is only available for UTF-8 text files: {artifact_path.name}"
        ) from exc

    suffix = artifact_path.suffix.lower()
    format_name = "text"
    if suffix in {".md", ".markdown"}:
        format_name = "markdown"
    elif suffix == ".json":
        format_name = "json"

    return {
        "run_id": normalized_run_id,
        "artifact_key": normalized_artifact_key,
        "path": str(artifact_path),
        "format": format_name,
        "content": content,
    }


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _derive_status(result: dict[str, Any]) -> str:
    trace = result.get("trace", {})
    if not isinstance(trace, dict):
        return "completed"

    reporter_span = (
        trace.get("reporter") if isinstance(trace.get("reporter"), dict) else {}
    )
    reporter_status = str(reporter_span.get("status", "") or "").strip().lower()
    if reporter_status in {"ok", "success", "completed", "partial_success", "skipped"}:
        return "completed"

    for span in trace.values():
        if not isinstance(span, dict):
            continue
        if span.get("non_blocking") is True:
            continue
        status = str(span.get("status", "") or "").strip().lower()
        if status in {"error", "failed"}:
            return "failed"
    return "completed"


def _review_observability_details(
    review_result: dict[str, Any], review_profile: dict[str, Any]
) -> dict[str, Any]:
    meta = review_result.get("parallel_review_meta")
    if not isinstance(meta, dict):
        meta = review_result.get("parallel-review_meta")
    if not isinstance(meta, dict):
        meta = {}
    memory_influence = (
        meta.get("memory_influence")
        if isinstance(meta.get("memory_influence"), dict)
        else {}
    )
    return {
        "selected_profile": str(review_profile.get("selected_profile", "") or ""),
        "profile_routing_reason": str(review_profile.get("reason", "") or ""),
        "profile_routing_confidence": _to_float(review_profile.get("confidence")),
        "secondary_profiles": list(review_profile.get("secondary_profiles", []) or []),
        "memory_mode": str(
            review_result.get("memory_mode", meta.get("memory_mode", "off")) or "off"
        ),
        "retrieved_memories": list(meta.get("retrieved_memory_cards", []) or []),
        "rejected_memory_candidates": list(
            meta.get("rejected_memory_candidates", []) or []
        ),
        "memory_influence": {
            "findings": list(memory_influence.get("findings", []) or []),
            "clarification_questions": list(
                memory_influence.get("clarification_questions", []) or []
            ),
            "open_questions": list(memory_influence.get("open_questions", []) or []),
        },
    }


def _combine_operation_status(*statuses: str) -> str:
    normalized = [
        str(status or "").strip().lower()
        for status in statuses
        if str(status or "").strip()
    ]
    if not normalized:
        return "unknown"

    success_statuses = {"ok", "success", "completed"}
    if all(status in success_statuses | {"skipped"} for status in normalized):
        return "ok"
    if any(status in {"failed", "error"} for status in normalized):
        return (
            "partial_success"
            if any(
                status in success_statuses | {"partial_success"}
                for status in normalized
            )
            else "failed"
        )
    if any(status == "partial_success" for status in normalized):
        return "partial_success"
    return normalized[-1]


def _has_requirement_doc_for_prd_draft(run_output: dict[str, Any]) -> bool:
    result_payload = run_output.get("result")
    if not isinstance(result_payload, dict):
        result_payload = {}

    report_payload: dict[str, Any] = {}
    report_paths = run_output.get("report_paths")
    if isinstance(report_paths, dict):
        report_json_raw = str(report_paths.get("report_json", "") or "").strip()
        if report_json_raw:
            report_payload = _load_json_object(Path(report_json_raw))

    requirement_doc = str(
        report_payload.get("requirement_doc", "")
        or result_payload.get("requirement_doc", "")
        or run_output.get("requirement_doc", "")
        or ""
    )
    return bool(requirement_doc.strip())


def _build_summary(run_output: dict[str, Any]) -> ReviewResultSummary:
    result = run_output.get("result", {})
    report_paths = run_output.get("report_paths", {})
    metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
    return ReviewResultSummary(
        run_id=str(run_output.get("run_id", "")),
        report_md_path=str(report_paths.get("report_md", "")),
        report_json_path=str(report_paths.get("report_json", "")),
        run_trace_path=str(report_paths.get("run_trace", "")),
        implementation_pack_path=str(report_paths.get("implementation_pack", "")),
        test_pack_path=str(report_paths.get("test_pack", "")),
        execution_pack_path=str(report_paths.get("execution_pack", "")),
        prd_v1_path=str(report_paths.get("prd_v1", "")),
        task_bundle_v1_path=str(report_paths.get("task_bundle_v1", "")),
        delivery_bundle_path=str(report_paths.get("delivery_bundle", "")),
        high_risk_ratio=_to_float(
            result.get("high_risk_ratio") if isinstance(result, dict) else 0.0
        ),
        coverage_ratio=_to_float(
            metrics.get("coverage_ratio") if isinstance(metrics, dict) else 0.0
        ),
        revision_round=int(
            (result.get("revision_round", 0) if isinstance(result, dict) else 0) or 0
        ),
        status=_derive_status(result if isinstance(result, dict) else {}),
    )


def _publish_progress_event(
    run_id: str, event: str, node_name: str, state: dict[str, Any] | None = None
) -> None:
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return

    payload: dict[str, Any] = {
        "node": str(node_name or "").strip(),
        "status": str(event or "").strip(),
        "timestamp": _utc_now_iso(),
    }
    if isinstance(state, dict):
        error_message = str(state.get("error", "") or "").strip()
        if error_message:
            payload["error"] = error_message

    ProgressBroadcaster().publish(normalized_run_id, "progress", payload)


def _resolve_audit_context(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    audit_context = payload.get("audit_context")
    return dict(audit_context) if isinstance(audit_context, dict) else {}


def _append_audit_event_safe(
    run_dir: str | Path,
    *,
    operation: str,
    status: str,
    audit_context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    try:
        append_audit_event(
            run_dir,
            operation=operation,
            status=status,
            audit_context=audit_context,
            **kwargs,
        )
    except Exception:
        pass


def _dispatch_notification(
    run_dir: str | Path,
    *,
    notification_type: NotificationType | str,
    title: str,
    summary: str = "",
    audit_context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    dispatch_notification(
        run_dir,
        notification_type=notification_type,
        title=title,
        summary=summary,
        audit_context=audit_context,
        **kwargs,
    )


def _should_dispatch_review_notifications(audit_context: dict[str, Any] | None) -> bool:
    if not isinstance(audit_context, dict):
        return False
    if str(audit_context.get("source") or "").strip().lower() == "feishu":
        return True
    client_metadata = audit_context.get("client_metadata")
    if not isinstance(client_metadata, dict):
        return False
    return str(client_metadata.get("trigger_source") or "").strip().lower() == "feishu"


def _format_ratio_percent(value: Any) -> str:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return "0%"
    return f"{normalized * 100:.0f}%"


def _resolve_review_notification_summary(
    review_result: dict[str, Any], *, status: str
) -> str:
    clarification = _derive_clarification(review_result)
    if status == "clarification_required":
        question_count = len(
            [
                item
                for item in clarification.get("questions", [])
                if isinstance(item, dict)
            ]
        )
        return (
            "Review requires clarification before follow-up. "
            f"{question_count} question(s) are waiting for an answer."
        )

    metrics = (
        review_result.get("metrics")
        if isinstance(review_result.get("metrics"), dict)
        else {}
    )
    coverage_ratio = _format_ratio_percent(metrics.get("coverage_ratio"))
    high_risk_ratio = _format_ratio_percent(review_result.get("high_risk_ratio"))
    return f"Review completed. Coverage ratio {coverage_ratio}. High-risk ratio {high_risk_ratio}."


def _dispatch_review_status_notification(
    run_dir: str | Path,
    *,
    run_id: str,
    review_status: str,
    summary: str,
    audit_context: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    status_mapping = {
        "submitted": NotificationType.review_submitted,
        "running": NotificationType.review_running,
        "completed": NotificationType.review_completed,
        "failed": NotificationType.review_failed,
        "clarification_required": NotificationType.clarification_required,
    }
    notification_type = status_mapping.get(str(review_status or "").strip().lower())
    if notification_type is None:
        return

    resolved_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    resolved_metadata.setdefault(
        "review_run_status", str(review_status or "").strip().lower()
    )
    _dispatch_notification(
        run_dir,
        notification_type=notification_type,
        title=f"Review {str(review_status or '').replace('_', ' ')}: {run_id}",
        summary=summary,
        run_id=run_id,
        metadata=resolved_metadata,
        audit_context=audit_context,
    )


def _resolve_outputs_root(outputs_root: str | Path = "outputs") -> Path:
    return Path(outputs_root).resolve()


def _resolve_run_dir(run_id: str, outputs_root: str | Path = "outputs") -> Path:
    normalized_run_id = str(run_id or "").strip()
    if not RUN_ID_PATTERN.fullmatch(normalized_run_id):
        raise ValueError("run_id must match YYYYMMDDTHHMMSSZ")
    outputs_root_path = _resolve_outputs_root(outputs_root)
    run_dir = (outputs_root_path / normalized_run_id).resolve()
    if outputs_root_path not in run_dir.parents and run_dir != outputs_root_path:
        raise ValueError("run_id resolves outside outputs root")
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"run_id not found: {normalized_run_id}")
    return run_dir


def _locate_bundle_path(bundle_id: str, outputs_root: str | Path = "outputs") -> Path:
    normalized_bundle_id = str(bundle_id or "").strip()
    if not normalized_bundle_id:
        raise ValueError("bundle_id is required")
    outputs_root_path = _resolve_outputs_root(outputs_root)
    for candidate in outputs_root_path.glob("*/delivery_bundle.json"):
        payload = _load_json_object(candidate)
        if payload.get("bundle_id") == normalized_bundle_id:
            return candidate
    raise FileNotFoundError(f"delivery bundle not found: {normalized_bundle_id}")


def _generate_delivery_bundle(
    run_output: dict[str, Any],
    *,
    splitter: ArtifactSplitter | None = None,
) -> tuple[dict[str, str], DeliveryBundle]:
    result = run_output.get("result", {})
    if not isinstance(result, dict):
        raise TypeError("run_output.result must be an object")

    run_dir_raw = str(run_output.get("run_dir", "") or "")
    if not run_dir_raw:
        raise ValueError("run_dir is required")
    run_dir = Path(run_dir_raw)

    report_paths = run_output.get("report_paths", {})
    pack_paths = {
        "implementation_pack": str(report_paths.get("implementation_pack", "") or ""),
        "test_pack": str(report_paths.get("test_pack", "") or ""),
        "execution_pack": str(report_paths.get("execution_pack", "") or ""),
    }
    missing = [name for name, path in pack_paths.items() if not path]
    if missing:
        raise ValueError(f"missing pack paths: {', '.join(missing)}")

    artifact_refs = (splitter or ArtifactSplitter()).split(result, run_dir)
    bundle = DeliveryBundleBuilder().build(
        run_output=run_output, artifact_refs=artifact_refs, pack_paths=pack_paths
    )
    source_metadata = _extract_source_metadata(run_output)
    if source_metadata:
        bundle.metadata["source_metadata"] = source_metadata
    parallel_review_meta = _extract_parallel_review_meta(run_output)
    if parallel_review_meta:
        bundle.metadata["parallel-review_meta"] = parallel_review_meta
    handoff_source = result.get("handoff_source")
    if isinstance(handoff_source, dict) and handoff_source:
        bundle.metadata["handoff_source"] = dict(handoff_source)
    bundle_path = DeliveryBundleBuilder().save(bundle, run_dir)

    artifact_paths = {
        artifact_type: ref.path for artifact_type, ref in artifact_refs.items()
    }
    artifact_paths["delivery_bundle"] = str(bundle_path)
    return artifact_paths, bundle


def _extract_source_metadata(run_output: dict[str, Any]) -> dict[str, Any]:
    direct_metadata = run_output.get("source_metadata")
    if isinstance(direct_metadata, dict):
        return dict(direct_metadata)

    result = run_output.get("result")
    if isinstance(result, dict):
        result_metadata = result.get("source_metadata")
        if isinstance(result_metadata, dict):
            return dict(result_metadata)

    return {}


def _extract_parallel_review_meta(run_output: dict[str, Any]) -> dict[str, Any]:
    result = run_output.get("result")
    if isinstance(result, dict):
        direct_meta = result.get("parallel-review_meta")
        if isinstance(direct_meta, dict):
            return dict(direct_meta)
        direct_state_meta = result.get("parallel_review_meta")
        if isinstance(direct_state_meta, dict):
            return dict(direct_state_meta)
        trace = result.get("trace")
        if isinstance(trace, dict):
            trace_meta = trace.get("parallel-review_meta")
            if isinstance(trace_meta, dict):
                return dict(trace_meta)
    return {}


def _read_text_if_present(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return str(path.read_text(encoding="utf-8") or "")
    except OSError:
        return ""


def _resolve_handoff_source(run_dir: Path, result: dict[str, Any]) -> dict[str, str]:
    revision_stage = _load_revision_stage_payload(run_dir, result)
    confirmed_ref = str(revision_stage.get("confirmed_revision_ref", "") or "").strip()
    draft_ref = str(revision_stage.get("draft_revision_ref", "") or "").strip()

    confirmed_text = _read_text_if_present(Path(confirmed_ref)) if confirmed_ref else ""
    if not confirmed_text:
        confirmed_text = _read_text_if_present(run_dir / "confirmed_prd.md")
    if confirmed_text.strip():
        return {
            "source": "confirmed_revision",
            "requirement_doc": confirmed_text,
            "source_ref": str(Path(confirmed_ref).resolve())
            if confirmed_ref
            else str((run_dir / "confirmed_prd.md").resolve()),
            "latest_revision_draft_ref": "",
        }

    draft_text = _read_text_if_present(Path(draft_ref)) if draft_ref else ""
    if not draft_text:
        draft_text = _read_text_if_present(run_dir / "revised_prd.md")
    draft_ref_resolved = (
        str(Path(draft_ref).resolve())
        if draft_ref
        else str((run_dir / "revised_prd.md").resolve())
    )

    original_doc = str(result.get("requirement_doc", "") or "").strip()
    if not original_doc:
        canonical_payload = _load_json_object(run_dir / CANONICAL_REQUEST_FILENAME)
        if isinstance(canonical_payload, dict):
            content = canonical_payload.get("content")
            if isinstance(content, dict):
                original_doc = str(content.get("text", "") or "").strip()
    if not original_doc and draft_text.strip():
        return {
            "source": "latest_revision_draft",
            "requirement_doc": draft_text,
            "source_ref": draft_ref_resolved,
            "latest_revision_draft_ref": draft_ref_resolved,
        }
    return {
        "source": "original_prd_with_review",
        "requirement_doc": original_doc,
        "source_ref": "report.requirement_doc",
        "latest_revision_draft_ref": draft_ref_resolved if draft_text.strip() else "",
    }


def _resolve_requirement_doc(
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
) -> tuple[str, dict[str, Any]]:
    has_source = isinstance(source, str) and bool(source.strip())
    if has_source:
        normalized_source = source.strip()
        source_document = (
            ConnectorRegistry()
            .resolve(normalized_source)
            .get_content(normalized_source)
        )
        return source_document.content_markdown, {
            "source_metadata": source_document.metadata.model_dump(mode="python"),
        }
    return _read_prd_text(prd_text=prd_text, prd_path=prd_path), {}


def _persist_canonical_review_request(
    *,
    run_dir: Path,
    run_id: str,
    requirement_doc: str,
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
    audit_context: dict[str, Any] | None,
) -> dict[str, Any]:
    canonical_request = normalize_ingress_request(
        run_id=run_id,
        requirement_doc=requirement_doc,
        prd_text=prd_text,
        prd_path=prd_path,
        source_ref=source,
        audit_context=audit_context,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = canonical_request.model_dump(mode="json")
    (run_dir / CANONICAL_REQUEST_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def build_handoff_prompts(
    execution_pack_path: str | Path | None,
    *,
    trace: dict[str, Any] | None = None,
) -> dict[str, str]:
    codex_prompt_template = get_adapter_prompt_template(
        "adapter.codex.handoff_markdown"
    )
    claude_code_prompt_template = get_adapter_prompt_template(
        "adapter.claude_code.handoff_markdown"
    )
    openclaw_prompt_template = get_adapter_prompt_template(
        "adapter.openclaw.handoff_markdown"
    )
    renderer_trace: dict[str, Any] = {
        "start": _utc_now_iso(),
        "end": "",
        "duration_ms": 0,
        "status": "running",
        "template_version": codex_prompt_template.version,
        "templates": {
            "codex_prompt": codex_prompt_template.trace_metadata(),
            "claude_code_prompt": claude_code_prompt_template.trace_metadata(),
            "openclaw_prompt": openclaw_prompt_template.trace_metadata(),
        },
        "output_paths": {},
        "error_message": "",
        "non_blocking": True,
        "retry": {},
    }
    prompt_paths: dict[str, str] = {}
    handoff_render_error = ""
    started = perf_counter()

    try:
        execution_pack_path_str = str(execution_pack_path or "").strip()
        if not execution_pack_path_str:
            renderer_trace["status"] = "skipped"
            renderer_trace["error_message"] = "execution_pack_path_missing"
            return {}

        execution_pack_file = Path(execution_pack_path_str)
        if not execution_pack_file.exists():
            renderer_trace["status"] = "skipped"
            renderer_trace["error_message"] = "execution_pack_not_found"
            return {}

        execution_pack = json.loads(execution_pack_file.read_text(encoding="utf-8"))
        output_dir = execution_pack_file.parent
        render_targets = (
            ("codex_prompt", output_dir / "codex_prompt.md", render_codex_prompt),
            (
                "claude_code_prompt",
                output_dir / "claude_code_prompt.md",
                render_claude_code_prompt,
            ),
            (
                "openclaw_prompt",
                output_dir / "openclaw_prompt.md",
                render_openclaw_prompt,
            ),
        )
        render_failures: list[str] = []

        for prompt_name, output_path, renderer in render_targets:
            try:
                rendered = renderer(execution_pack)
                output_path.write_text(rendered, encoding="utf-8")
                prompt_paths[prompt_name] = str(output_path)
            except Exception as exc:
                render_failures.append(prompt_name)
                if not handoff_render_error:
                    handoff_render_error = str(exc)

        renderer_trace["output_paths"] = dict(prompt_paths)
        if not render_failures:
            renderer_trace["status"] = "ok"
        elif len(render_failures) == len(render_targets):
            renderer_trace["status"] = "failed"
        else:
            renderer_trace["status"] = "partial_success"
        renderer_trace["error_message"] = (
            ", ".join(render_failures) if render_failures else ""
        )
    except Exception as exc:
        handoff_render_error = str(exc)
        renderer_trace["status"] = "failed"
        renderer_trace["error_message"] = str(exc)
    finally:
        renderer_trace["end"] = _utc_now_iso()
        renderer_trace["duration_ms"] = round((perf_counter() - started) * 1000)
        renderer_trace["retry"] = retry_metadata_for_status(
            status=str(renderer_trace.get("status", "")),
            non_blocking=bool(renderer_trace.get("non_blocking")),
            error_message=str(
                renderer_trace.get("error_message", "") or handoff_render_error
            ),
        )
        if isinstance(trace, dict):
            trace["handoff_renderer"] = renderer_trace
            if handoff_render_error:
                trace["handoff_render_error"] = handoff_render_error

    return prompt_paths


def build_delivery_handoff_outputs(
    run_output: dict[str, Any],
    *,
    audit_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    result = run_output.get("result", {})
    if not isinstance(result, dict):
        return {}

    run_dir_raw = str(run_output.get("run_dir", "") or "")
    if not run_dir_raw:
        return {}
    run_dir = Path(run_dir_raw)
    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_audit_context = (
        dict(audit_context) if isinstance(audit_context, dict) else {}
    )

    trace = result.get("trace")
    if not isinstance(trace, dict):
        trace = {}
        result["trace"] = trace

    selected_handoff_source = _resolve_handoff_source(run_dir, result)
    selected_requirement_doc = str(
        selected_handoff_source.get("requirement_doc", "") or ""
    ).strip()
    if selected_requirement_doc:
        result["requirement_doc"] = selected_requirement_doc
    result["handoff_source"] = {
        "selected_source": str(
            selected_handoff_source.get("source", "") or "original_prd_with_review"
        ),
        "source_ref": str(selected_handoff_source.get("source_ref", "") or ""),
        "latest_revision_draft_ref": str(
            selected_handoff_source.get("latest_revision_draft_ref", "") or ""
        ),
    }

    source_metadata = _extract_source_metadata(run_output)
    if source_metadata:
        result["source_metadata"] = source_metadata

    started = perf_counter()
    pack_builder_trace: dict[str, Any] = {
        "start": _utc_now_iso(),
        "end": "",
        "duration_ms": 0,
        "status": "running",
        "input_chars": len(json.dumps(result, ensure_ascii=False, default=str)),
        "output_chars": 0,
        "model": "none",
        "prompt_version": "handoff_pack_v1",
        "raw_output_path": "",
        "error_message": "",
        "non_blocking": True,
        "retry": {},
        "packs": {},
    }

    builder_inputs = {
        "requirements": result.get("parsed_items"),
        "tasks": result.get("tasks"),
        "risks": result.get("risks"),
        "implementation_plan_output": result.get("implementation_plan"),
        "test_plan_output": result.get("test_plan"),
        "codex_prompt_output": result.get("codex_prompt_handoff"),
        "claude_code_prompt_output": result.get("claude_code_prompt_handoff"),
    }
    builders = (
        (
            "implementation_pack",
            ImplementationPackBuilder(),
            run_dir / "implementation_pack.json",
        ),
        ("test_pack", TestPackBuilder(), run_dir / "test_pack.json"),
        ("execution_pack", ExecutionPackBuilder(), run_dir / "execution_pack.json"),
    )

    artifact_paths: dict[str, str] = {}
    failed_packs: list[str] = []

    for pack_name, builder, output_path in builders:
        pack_started = perf_counter()
        pack_trace = {
            "status": "running",
            "duration_ms": 0,
            "output_path": str(output_path),
            "error_message": "",
            "retry": {},
        }
        try:
            pack = builder.build(**builder_inputs)
            payload = pack.model_dump(mode="python")
            serialized = json.dumps(payload, ensure_ascii=False, indent=2)
            output_path.write_text(serialized, encoding="utf-8")
            artifact_paths[pack_name] = str(output_path)
            pack_trace["status"] = "ok"
        except Exception as exc:
            failed_packs.append(pack_name)
            pack_trace["status"] = "error"
            pack_trace["error_message"] = str(exc)
        finally:
            pack_trace["duration_ms"] = round((perf_counter() - pack_started) * 1000)
            pack_trace["retry"] = retry_metadata_for_status(
                status=str(pack_trace.get("status", "")),
                non_blocking=True,
                error_message=str(pack_trace.get("error_message", "")),
            )
            pack_builder_trace["packs"][pack_name] = pack_trace

    pack_builder_trace["end"] = _utc_now_iso()
    pack_builder_trace["duration_ms"] = round((perf_counter() - started) * 1000)
    if not failed_packs:
        pack_builder_trace["status"] = "ok"
    elif len(failed_packs) == len(builders):
        pack_builder_trace["status"] = "failed"
    else:
        pack_builder_trace["status"] = "partial_success"
    pack_builder_trace["error_message"] = ", ".join(failed_packs)
    pack_builder_trace["output_chars"] = sum(
        len(Path(path).read_text(encoding="utf-8"))
        for path in artifact_paths.values()
        if Path(path).exists()
    )
    pack_builder_trace["retry"] = retry_metadata_for_status(
        status=str(pack_builder_trace.get("status", "")),
        non_blocking=bool(pack_builder_trace.get("non_blocking")),
        error_message=str(pack_builder_trace.get("error_message", "")),
    )
    trace["pack_builder"] = pack_builder_trace

    prompt_paths = build_handoff_prompts(
        artifact_paths.get("execution_pack"), trace=trace
    )
    artifact_paths.update(prompt_paths)

    draft_generator_trace: dict[str, Any] = {
        "start": _utc_now_iso(),
        "end": "",
        "duration_ms": 0,
        "status": "running",
        "error_message": "",
        "non_blocking": True,
        "retry": {},
        "artifact_path": "",
        "output_paths": {},
        "generator_version": "",
        "source_artifacts": [],
    }
    draft_started = perf_counter()
    try:
        if not _has_requirement_doc_for_prd_draft(run_output):
            draft_generator_trace["status"] = "skipped"
            draft_generator_trace["error_message"] = "requirement_doc_missing"
        else:
            prd_draft_artifact = generate_prd_v1_artifact(run_output)
            artifact_paths[prd_draft_artifact.artifact_key] = prd_draft_artifact.path
            draft_generator_trace.update(prd_draft_artifact.trace)
    except Exception as exc:
        draft_generator_trace["status"] = "failed"
        draft_generator_trace["error_message"] = str(exc)
    finally:
        draft_generator_trace["end"] = str(
            draft_generator_trace.get("end", "") or _utc_now_iso()
        )
        duration_ms = round((perf_counter() - draft_started) * 1000)
        draft_generator_trace["duration_ms"] = max(
            duration_ms, int(draft_generator_trace.get("duration_ms", 0) or 0)
        )
        draft_generator_trace["retry"] = retry_metadata_for_status(
            status=str(draft_generator_trace.get("status", "")),
            non_blocking=bool(draft_generator_trace.get("non_blocking")),
            error_message=str(draft_generator_trace.get("error_message", "")),
        )
        trace["draft_generator"] = draft_generator_trace

    task_bundle_trace: dict[str, Any] = {
        "start": _utc_now_iso(),
        "end": "",
        "duration_ms": 0,
        "status": "running",
        "error_message": "",
        "non_blocking": True,
        "retry": {},
        "artifact_path": "",
        "output_paths": {},
        "generator_version": "",
        "source_artifacts": [],
        "task_count": 0,
    }
    task_bundle_started = perf_counter()
    try:
        task_bundle_artifact = generate_task_bundle_v1_artifact(run_output)
        artifact_paths[task_bundle_artifact.artifact_key] = task_bundle_artifact.path
        task_bundle_trace.update(task_bundle_artifact.trace)
    except Exception as exc:
        task_bundle_trace["status"] = "failed"
        task_bundle_trace["error_message"] = str(exc)
    finally:
        task_bundle_trace["end"] = str(
            task_bundle_trace.get("end", "") or _utc_now_iso()
        )
        duration_ms = round((perf_counter() - task_bundle_started) * 1000)
        task_bundle_trace["duration_ms"] = max(
            duration_ms, int(task_bundle_trace.get("duration_ms", 0) or 0)
        )
        task_bundle_trace["retry"] = retry_metadata_for_status(
            status=str(task_bundle_trace.get("status", "")),
            non_blocking=bool(task_bundle_trace.get("non_blocking")),
            error_message=str(task_bundle_trace.get("error_message", "")),
        )
        trace["task_bundle_builder"] = task_bundle_trace

    bundle_builder_trace: dict[str, Any] = {
        "start": _utc_now_iso(),
        "end": "",
        "duration_ms": 0,
        "status": "running",
        "error_message": "",
        "non_blocking": True,
        "retry": {},
        "output_paths": {},
        "artifact_templates": {},
    }
    bundle_started = perf_counter()
    try:
        report_paths = run_output.get("report_paths", {})
        if isinstance(report_paths, dict):
            report_paths.update(artifact_paths)
        splitter = ArtifactSplitter()
        bundle_builder_trace["artifact_templates"] = splitter.template_trace()
        bundle_artifact_paths, _bundle = _generate_delivery_bundle(
            run_output, splitter=splitter
        )
        artifact_paths.update(bundle_artifact_paths)
        if isinstance(report_paths, dict):
            report_paths.update(bundle_artifact_paths)
        bundle_builder_trace["status"] = "ok"
        bundle_builder_trace["output_paths"] = bundle_artifact_paths
    except Exception as exc:
        bundle_builder_trace["status"] = "failed"
        bundle_builder_trace["error_message"] = str(exc)
    finally:
        bundle_builder_trace["end"] = _utc_now_iso()
        bundle_builder_trace["duration_ms"] = round(
            (perf_counter() - bundle_started) * 1000
        )
        bundle_builder_trace["retry"] = retry_metadata_for_status(
            status=str(bundle_builder_trace.get("status", "")),
            non_blocking=bool(bundle_builder_trace.get("non_blocking")),
            error_message=str(bundle_builder_trace.get("error_message", "")),
        )
        trace["bundle_builder"] = bundle_builder_trace

    report_paths = run_output.get("report_paths", {})
    if isinstance(report_paths, dict):
        report_paths.update(artifact_paths)

    parallel_review_meta = _extract_parallel_review_meta(run_output)
    if parallel_review_meta:
        trace["parallel-review_meta"] = parallel_review_meta

    trace_path_raw = str(report_paths.get("run_trace", "") or "")
    if trace_path_raw:
        trace_path = Path(trace_path_raw)
        trace_payload = _load_json_object(trace_path)
        trace_payload.update(trace)
        trace_payload["handoff_source"] = dict(result.get("handoff_source", {}))
        if source_metadata:
            trace_payload["source_metadata"] = source_metadata
        if parallel_review_meta:
            trace_payload["parallel-review_meta"] = parallel_review_meta
        trace_path.write_text(
            json.dumps(trace_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    report_json_raw = str(report_paths.get("report_json", "") or "")
    if report_json_raw:
        report_json_path = Path(report_json_raw)
        report_payload = _load_json_object(report_json_path)
        if report_payload:
            report_trace = report_payload.get("trace")
            if not isinstance(report_trace, dict):
                report_trace = {}
            report_trace["pack_builder"] = pack_builder_trace
            report_trace["draft_generator"] = draft_generator_trace
            report_trace["task_bundle_builder"] = task_bundle_trace
            handoff_renderer_trace = trace.get("handoff_renderer")
            if isinstance(handoff_renderer_trace, dict):
                report_trace["handoff_renderer"] = handoff_renderer_trace
            if "handoff_render_error" in trace:
                report_trace["handoff_render_error"] = trace["handoff_render_error"]
            report_trace["bundle_builder"] = bundle_builder_trace
            report_payload["trace"] = report_trace
            if source_metadata:
                report_payload["source_metadata"] = source_metadata
            if parallel_review_meta:
                report_payload["parallel-review_meta"] = parallel_review_meta
            report_payload["handoff_source"] = dict(result.get("handoff_source", {}))
            if str(result.get("requirement_doc", "") or "").strip():
                report_payload["requirement_doc"] = str(
                    result.get("requirement_doc", "") or ""
                ).strip()

            artifacts = report_payload.get("artifacts")
            if not isinstance(artifacts, dict):
                artifacts = {}
            artifacts.update(artifact_paths)
            report_payload["artifacts"] = artifacts
            report_json_path.write_text(
                json.dumps(report_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    handoff_renderer_trace = trace.get("handoff_renderer")
    handoff_renderer_status = (
        handoff_renderer_trace.get("status", "")
        if isinstance(handoff_renderer_trace, dict)
        else ""
    )
    bundle_operation_status = _combine_operation_status(
        str(pack_builder_trace.get("status", "")),
        str(draft_generator_trace.get("status", "")),
        str(task_bundle_trace.get("status", "")),
        str(handoff_renderer_status),
        str(bundle_builder_trace.get("status", "")),
    )
    bundle_retry = retry_metadata_for_status(
        status=bundle_operation_status,
        non_blocking=True,
        error_message="; ".join(
            message
            for message in (
                str(pack_builder_trace.get("error_message", "")),
                str(draft_generator_trace.get("error_message", "")),
                str(task_bundle_trace.get("error_message", "")),
                str(handoff_renderer_trace.get("error_message", ""))
                if isinstance(handoff_renderer_trace, dict)
                else "",
                str(bundle_builder_trace.get("error_message", "")),
            )
            if message
        ),
    )
    bundle_id = ""
    delivery_bundle_path = artifact_paths.get("delivery_bundle", "")
    if delivery_bundle_path:
        bundle_payload = _load_json_object(Path(delivery_bundle_path))
        bundle_id = str(bundle_payload.get("bundle_id", "") or "")

    _append_audit_event_safe(
        run_dir,
        operation="bundle_generation",
        status=bundle_operation_status,
        run_id=str(run_output.get("run_id", "") or ""),
        bundle_id=bundle_id,
        audit_context=resolved_audit_context,
        details={
            "artifact_count": len(artifact_paths),
            "delivery_bundle_path": delivery_bundle_path,
            "handoff_source": result.get("handoff_source", {}),
            "component_statuses": {
                "pack_builder": pack_builder_trace.get("status", ""),
                "draft_generator": draft_generator_trace.get("status", ""),
                "task_bundle_builder": task_bundle_trace.get("status", ""),
                "handoff_renderer": handoff_renderer_status,
                "bundle_builder": bundle_builder_trace.get("status", ""),
            },
        },
        retry=bundle_retry,
    )

    return artifact_paths


async def review_prd_text_async(
    prd_text: str | None = None,
    *,
    prd_path: str | None = None,
    source: str | None = None,
    run_id: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> ReviewResultSummary:
    overrides = config_overrides or {}
    outputs_root = Path(str(overrides.get("outputs_root", "outputs")))
    audit_context = _resolve_audit_context(overrides)
    progress_hook = overrides.get("progress_hook")
    if progress_hook is not None and not callable(progress_hook):
        raise TypeError("config_overrides['progress_hook'] must be callable")
    requested_run_id = str(run_id or "").strip()

    def combined_progress_hook(
        event: str, node_name: str, state: dict[str, Any]
    ) -> None:
        if progress_hook is not None:
            progress_hook(event, node_name, state)
        _publish_progress_event(resolved_run_id, event, node_name, state)

    requirement_doc, source_context = _resolve_requirement_doc(
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
    )
    resolved_run_id = requested_run_id or make_unique_run_id(
        outputs_root, reserve_dir=True
    )
    run_dir = outputs_root / resolved_run_id
    canonical_request = _persist_canonical_review_request(
        run_dir=run_dir,
        run_id=resolved_run_id,
        requirement_doc=requirement_doc,
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
        audit_context=audit_context,
    )
    routed_profile = route_review_profile(canonical_request)
    profile_payload = routed_profile.to_dict()
    profile_pack = load_profile_pack(
        profile_payload["selected_profile"],
        secondary_profiles=profile_payload.get("secondary_profiles"),
    )
    run_review_kwargs: dict[str, Any] = {
        "requirement_doc": requirement_doc,
        "run_id": resolved_run_id,
        "outputs_root": outputs_root,
        "progress_hook": combined_progress_hook,
        "review_profile": profile_payload,
        "review_profile_pack": profile_pack,
        "canonical_review_request": dict(canonical_request),
        "audit_context": audit_context,
    }
    review_mode_override = overrides.get("review_mode_override")
    if isinstance(review_mode_override, str) and review_mode_override.strip():
        run_review_kwargs["review_mode_override"] = review_mode_override.strip()
    mode = overrides.get("mode")
    if isinstance(mode, str) and mode.strip():
        run_review_kwargs["mode"] = mode.strip()
    if "review_memory_path" in overrides:
        run_review_kwargs["review_memory_path"] = overrides.get("review_memory_path")
    if "review_memory_enabled" in overrides:
        run_review_kwargs["review_memory_enabled"] = overrides.get(
            "review_memory_enabled"
        )
    if "review_memory_seeds_dir" in overrides:
        run_review_kwargs["review_memory_seeds_dir"] = overrides.get(
            "review_memory_seeds_dir"
        )
    if "review_memory_db_path" in overrides:
        run_review_kwargs["review_memory_db_path"] = overrides.get(
            "review_memory_db_path"
        )
    if "review_memory_extract_enabled" in overrides:
        run_review_kwargs["review_memory_extract_enabled"] = overrides.get(
            "review_memory_extract_enabled"
        )
    if "review_memory_max_kept" in overrides:
        run_review_kwargs["review_memory_max_kept"] = overrides.get(
            "review_memory_max_kept"
        )
    if "review_memory_mode" in overrides:
        run_review_kwargs["review_memory_mode"] = overrides.get("review_memory_mode")
    if "normalizer_cache_path" in overrides:
        run_review_kwargs["normalizer_cache_path"] = overrides.get(
            "normalizer_cache_path"
        )

    llm_runtime_overrides = _resolve_llm_config_overrides(overrides)
    if _should_dispatch_review_notifications(audit_context):
        _dispatch_review_status_notification(
            run_dir,
            run_id=resolved_run_id,
            review_status="submitted",
            summary="Review submitted from Feishu entry and is now being processed.",
            audit_context=audit_context,
        )

    with RunLogContext(resolved_run_id):
        log.info(
            "review run started",
            extra={
                "run_id": resolved_run_id,
                "source_type": "source"
                if source
                else "prd_path"
                if prd_path
                else "inline_text",
            },
        )
        try:
            with runtime_config_overrides(llm_runtime_overrides):
                run_output = await run_review(**run_review_kwargs)
        except Exception as exc:
            if _should_dispatch_review_notifications(audit_context):
                _dispatch_review_status_notification(
                    run_dir,
                    run_id=resolved_run_id,
                    review_status="failed",
                    summary=f"Review failed before completion: {str(exc) or type(exc).__name__}.",
                    audit_context=audit_context,
                )
            raise
        if source_context:
            run_output.update(source_context)
            result = run_output.get("result")
            if isinstance(result, dict):
                result.update(source_context)
        run_output["canonical_review_request"] = canonical_request
        run_output["review_profile"] = profile_payload
        run_output["review_profile_pack"] = profile_pack
        review_result_for_meta = run_output.get("result")
        if isinstance(review_result_for_meta, dict):
            review_result_for_meta["canonical_review_request"] = canonical_request
            review_result_for_meta["review_profile"] = profile_payload
            review_result_for_meta["review_profile_pack"] = profile_pack

        review_result = run_output.get("result")
        if isinstance(review_result, dict):
            review_metrics = (
                review_result.get("metrics")
                if isinstance(review_result.get("metrics"), dict)
                else {}
            )
            review_status = _derive_status(review_result)
            observability = _review_observability_details(
                review_result, profile_payload
            )
            _append_audit_event_safe(
                run_output.get(
                    "run_dir", outputs_root / str(run_output.get("run_id", "") or "")
                ),
                operation="review",
                status=review_status,
                run_id=str(run_output.get("run_id", "") or ""),
                audit_context=audit_context,
                details={
                    "coverage_ratio": _to_float(review_metrics.get("coverage_ratio")),
                    "high_risk_ratio": _to_float(review_result.get("high_risk_ratio")),
                    "revision_round": int(review_result.get("revision_round", 0) or 0),
                    "requirement_source": "source"
                    if source
                    else "prd_path"
                    if prd_path
                    else "inline_text",
                    "has_source_metadata": bool(source_context),
                    "review_mode": str(
                        review_result.get(
                            "review_mode", review_result.get("mode", "quick")
                        )
                        or "quick"
                    ),
                    "parallel_review_enabled": bool(
                        review_result.get("parallel_review_meta")
                        or review_result.get("parallel-review_meta")
                    ),
                    "selected_profile": observability["selected_profile"],
                    "profile_routing_reason": observability["profile_routing_reason"],
                    "profile_routing_confidence": observability[
                        "profile_routing_confidence"
                    ],
                    "secondary_profiles": observability["secondary_profiles"],
                    "memory_mode": observability["memory_mode"],
                    "retrieved_memories": observability["retrieved_memories"],
                    "rejected_memory_candidates": observability[
                        "rejected_memory_candidates"
                    ],
                    "memory_influence": observability["memory_influence"],
                },
                retry=retry_metadata_for_status(
                    status=review_status, non_blocking=False
                ),
            )

        combined_progress_hook(
            "start", "finalize_artifacts", run_output.get("result", {})
        )

        try:
            build_delivery_handoff_outputs(run_output, audit_context=audit_context)
        except Exception as exc:
            if _should_dispatch_review_notifications(audit_context):
                _dispatch_review_status_notification(
                    run_output.get("run_dir", run_dir),
                    run_id=str(run_output.get("run_id", "") or resolved_run_id),
                    review_status="failed",
                    summary=f"Review artifacts could not be finalized: {str(exc) or type(exc).__name__}.",
                    audit_context=audit_context,
                )
            combined_progress_hook(
                "end",
                "finalize_artifacts",
                {
                    "trace": {"finalize_artifacts": {"status": "error"}},
                    "error": str(exc),
                },
            )
            raise
        else:
            combined_progress_hook(
                "end",
                "finalize_artifacts",
                {
                    "trace": {"finalize_artifacts": {"status": "ok"}},
                },
            )

        if isinstance(review_result, dict) and _should_dispatch_review_notifications(
            audit_context
        ):
            clarification = _derive_clarification(review_result)
            question_count = len(
                [
                    item
                    for item in clarification.get("questions", [])
                    if isinstance(item, dict)
                ]
            )
            notification_status = (
                "clarification_required"
                if clarification.get("triggered")
                and clarification.get("status") == "pending"
                else "completed"
            )
            _dispatch_review_status_notification(
                run_output.get("run_dir", run_dir),
                run_id=str(run_output.get("run_id", "") or resolved_run_id),
                review_status=notification_status,
                summary=_resolve_review_notification_summary(
                    review_result, status=notification_status
                ),
                audit_context=audit_context,
                metadata={
                    "clarification_status": str(clarification.get("status", "") or ""),
                    "clarification_question_count": question_count,
                },
            )

        log.info("review run completed", extra={"run_id": resolved_run_id})
        return _build_summary(run_output)


def review_prd_text(
    prd_text: str | None = None,
    *,
    prd_path: str | None = None,
    source: str | None = None,
    run_id: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> ReviewResultSummary:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            review_prd_text_async(
                prd_text=prd_text,
                prd_path=prd_path,
                source=source,
                run_id=run_id,
                config_overrides=config_overrides,
            )
        )
    raise RuntimeError(
        "review_prd_text cannot run inside an active event loop; use review_prd_text_async"
    )


def _read_prd_text(prd_text: str | None, prd_path: str | None) -> str:
    has_prd_text = isinstance(prd_text, str) and bool(prd_text.strip())
    has_prd_path = isinstance(prd_path, str) and bool(prd_path.strip())

    if has_prd_text and has_prd_path:
        raise ValueError("Provide only one of prd_text or prd_path")
    if has_prd_text:
        return prd_text
    if has_prd_path:
        path = Path(prd_path)
        if not path.exists():
            raise FileNotFoundError(f"PRD file not found: {prd_path}")
        return path.read_text(encoding="utf-8")
    raise ValueError("Either prd_text or prd_path must be provided")


def _attach_trace_invocation(
    summary: ReviewResultSummary, invocation_meta: dict[str, Any]
) -> None:
    if not summary.run_trace_path:
        return
    trace_path = Path(summary.run_trace_path)
    if not trace_path.exists():
        return

    trace_data: dict[str, Any] = {}
    try:
        loaded = json.loads(trace_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            trace_data = loaded
    except Exception:
        trace_data = {}

    invocation_trace = trace_data.get("invocation")
    if not isinstance(invocation_trace, dict):
        invocation_trace = {}
    invocation_trace.update(invocation_meta)
    trace_data["invocation"] = invocation_trace
    trace_path.write_text(
        json.dumps(trace_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Keep report.json trace aligned with run_trace.json when possible.
    if summary.report_json_path:
        report_path = Path(summary.report_json_path)
        if report_path.exists():
            try:
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
                if isinstance(report_data, dict):
                    report_trace = report_data.get("trace")
                    if not isinstance(report_trace, dict):
                        report_trace = {}
                    report_trace["invocation"] = invocation_trace
                    report_data["trace"] = report_trace
                    report_path.write_text(
                        json.dumps(report_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except Exception:
                # Do not fail review completion because of metadata write-back.
                pass


def generate_delivery_bundle_for_mcp(
    *,
    run_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")
    audit_context = _resolve_audit_context(resolved_options)

    outputs_root = resolved_options.get("outputs_root", "outputs")
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_json = _load_json_object(run_dir / "report.json")
    if not report_json:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")

    run_output = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "result": report_json,
        "report_paths": {
            "report_md": str(run_dir / "report.md"),
            "report_json": str(run_dir / "report.json"),
            "run_trace": str(run_dir / "run_trace.json"),
            "implementation_pack": str(run_dir / "implementation_pack.json"),
            "test_pack": str(run_dir / "test_pack.json"),
            "execution_pack": str(run_dir / "execution_pack.json"),
        },
    }
    artifact_paths, bundle = _generate_delivery_bundle(run_output)
    report_payload = _load_json_object(run_dir / "report.json")
    artifacts = (
        report_payload.get("artifacts") if isinstance(report_payload, dict) else {}
    )
    if not isinstance(artifacts, dict):
        artifacts = {}
    artifacts.update(artifact_paths)
    if isinstance(report_payload, dict):
        report_payload["artifacts"] = artifacts
        (run_dir / "report.json").write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    _append_audit_event_safe(
        run_dir,
        operation="bundle_generation",
        status="ok",
        run_id=run_id,
        bundle_id=bundle.bundle_id,
        audit_context=audit_context,
        details={
            "artifact_count": len(artifact_paths),
            "delivery_bundle_path": artifact_paths.get("delivery_bundle", ""),
            "mode": "regenerate",
            "component_statuses": {"bundle_builder": "ok"},
        },
        retry=retry_metadata_for_status(status="ok", non_blocking=False),
    )

    return {
        "run_id": run_id,
        "bundle_id": bundle.bundle_id,
        "status": bundle.status,
        "artifacts": artifact_paths,
    }


def _persist_review_workspace_state(
    *,
    bundle_path: Path,
    updated_bundle: DeliveryBundle,
    action: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    if not updated_bundle.approval_history:
        raise ValueError("updated bundle is missing approval history")

    latest_event = updated_bundle.approval_history[-1]
    approval_record = build_approval_record(updated_bundle, latest_event, action=action)
    repository = ReviewWorkspaceRepository(bundle_path.parent)
    status_snapshot = repository.build_status_snapshot(
        run_id=updated_bundle.source_run_id,
        bundle_id=updated_bundle.bundle_id,
        bundle_status=updated_bundle.status,
        updated_at=approval_record.timestamp,
    )
    approval_records_path = repository.append_approval_record(approval_record)
    status_snapshot_path = repository.save_status_snapshot(status_snapshot)
    return (
        {
            "bundle_path": str(bundle_path),
            "approval_records_path": str(approval_records_path),
            "status_snapshot_path": str(status_snapshot_path),
        },
        status_snapshot.model_dump(mode="python"),
    )


def _resolve_approval_notification(
    *,
    action: str,
    updated_bundle: DeliveryBundle,
    previous_status: str,
    comment: str,
    reviewer: str,
) -> tuple[NotificationType, str, str, dict[str, Any]] | None:
    normalized_action = str(action or "").strip()
    normalized_comment = str(comment or "").strip()
    normalized_status = str(updated_bundle.status)
    metadata = {
        "action": normalized_action,
        "comment": normalized_comment,
        "reviewer": str(reviewer or "").strip(),
        "from_status": previous_status,
        "to_status": normalized_status,
    }

    if normalized_status == "blocked_by_risk":
        return (
            NotificationType.blocked_by_risk,
            f"Bundle blocked by risk: {updated_bundle.bundle_id}",
            normalized_comment
            or "Delivery bundle is blocked until identified risks are mitigated.",
            metadata,
        )

    if normalized_action in {"need_more_info", "reset_to_draft"}:
        summary = normalized_comment
        if not summary and normalized_action == "need_more_info":
            summary = "Delivery bundle needs more context before approval can proceed."
        if not summary:
            summary = "Delivery bundle re-entered the review queue and needs approval follow-up."
        return (
            NotificationType.approval_requested,
            f"Approval requested: {updated_bundle.bundle_id}",
            summary,
            metadata,
        )

    return None


def approve_handoff_for_mcp(
    *,
    bundle_id: str,
    action: str,
    reviewer: str = "",
    comment: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")
    audit_context = _resolve_audit_context(resolved_options)

    bundle_path = _locate_bundle_path(
        bundle_id, resolved_options.get("outputs_root", "outputs")
    )
    bundle = DeliveryBundle.model_validate(_load_json_object(bundle_path))
    previous_status = str(bundle.status)

    actions = {
        "approve": approve_bundle,
        "need_more_info": request_more_info,
        "block_by_risk": block_by_risk,
        "reset_to_draft": reset_to_draft,
    }
    if action not in actions:
        raise ValueError(
            "action must be one of approve, need_more_info, block_by_risk, reset_to_draft"
        )

    updated_bundle = actions[action](bundle, reviewer, comment)
    bundle_path.write_text(
        json.dumps(
            updated_bundle.model_dump(mode="python"), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    persisted_paths, status_snapshot = _persist_review_workspace_state(
        bundle_path=bundle_path,
        updated_bundle=updated_bundle,
        action=action,
    )
    _append_audit_event_safe(
        bundle_path.parent,
        operation="approval",
        status=str(updated_bundle.status),
        run_id=str(updated_bundle.source_run_id),
        bundle_id=str(updated_bundle.bundle_id),
        actor=reviewer,
        audit_context=audit_context,
        details={
            "action": action,
            "comment": comment,
            "from_status": previous_status,
            "to_status": str(updated_bundle.status),
        },
        retry=retry_metadata_for_status(
            status=str(updated_bundle.status), non_blocking=False
        ),
    )
    notification_spec = _resolve_approval_notification(
        action=action,
        updated_bundle=updated_bundle,
        previous_status=previous_status,
        comment=comment,
        reviewer=reviewer,
    )
    if notification_spec is not None:
        notification_type, title, summary, metadata = notification_spec
        _dispatch_notification(
            bundle_path.parent,
            notification_type=notification_type,
            title=title,
            summary=summary,
            run_id=str(updated_bundle.source_run_id),
            bundle_id=str(updated_bundle.bundle_id),
            metadata=metadata,
            audit_context=audit_context,
        )
    return {
        "bundle_id": updated_bundle.bundle_id,
        "status": updated_bundle.status,
        "approval_history": [
            event.model_dump(mode="python") for event in updated_bundle.approval_history
        ],
        "bundle_path": persisted_paths["bundle_path"],
        "approval_records_path": persisted_paths["approval_records_path"],
        "status_snapshot_path": persisted_paths["status_snapshot_path"],
        "status_snapshot": status_snapshot,
    }


def get_review_workspace_for_mcp(
    *,
    run_id: str | None = None,
    bundle_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")

    run_key = str(run_id or "").strip()
    bundle_key = str(bundle_id or "").strip()
    if bool(run_key) == bool(bundle_key):
        raise ValueError("provide exactly one of run_id or bundle_id")

    outputs_root = resolved_options.get("outputs_root", "outputs")
    if run_key:
        run_dir = _resolve_run_dir(run_key, outputs_root)
    else:
        run_dir = _locate_bundle_path(bundle_key, outputs_root).parent

    repository = ReviewWorkspaceRepository(run_dir)
    if not repository.delivery_bundle_path.exists():
        raise FileNotFoundError(
            f"delivery_bundle.json not found for run_id={run_dir.name}"
        )
    if not repository.approval_records_path.exists():
        raise FileNotFoundError(
            f"approval_records.json not found for run_id={run_dir.name}"
        )
    if not repository.status_snapshot_path.exists():
        raise FileNotFoundError(
            f"status_snapshot.json not found for run_id={run_dir.name}"
        )

    bundle = repository.load_bundle()
    if bundle is None:
        raise FileNotFoundError(
            f"delivery_bundle.json not found for run_id={run_dir.name}"
        )

    workspace = repository.load_workspace()
    return {
        "run_id": workspace.run_id,
        "bundle": {
            "bundle_id": bundle.bundle_id,
            "bundle_version": bundle.bundle_version,
            "created_at": bundle.created_at,
            "status": bundle.status,
            "source_run_id": bundle.source_run_id,
        },
        "approval_history": [
            event.model_dump(mode="python") for event in workspace.approval_history
        ],
        "approval_records": [
            record.model_dump(mode="python") for record in workspace.approval_records
        ],
        "status_snapshot": workspace.status_snapshot.model_dump(mode="python")
        if workspace.status_snapshot is not None
        else None,
        "paths": {
            "bundle_path": str(repository.delivery_bundle_path),
            "approval_records_path": str(repository.approval_records_path),
            "status_snapshot_path": str(repository.status_snapshot_path),
        },
    }


def _slugify_identifier(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "unknown"


def _extract_parallel_review_payload(report_payload: dict[str, Any]) -> dict[str, Any]:
    parallel_review = report_payload.get("parallel_review")
    return dict(parallel_review) if isinstance(parallel_review, dict) else {}


def _extract_parallel_review_meta_from_report(
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    legacy_meta = report_payload.get("parallel-review_meta")
    if isinstance(legacy_meta, dict):
        return dict(legacy_meta)
    modern_meta = report_payload.get("parallel_review_meta")
    return dict(modern_meta) if isinstance(modern_meta, dict) else {}


def _derive_single_review_findings(
    review_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in review_results:
        requirement_id = str(item.get("id", "") or "").strip()
        issues = [
            str(issue).strip()
            for issue in list(item.get("issues", []) or [])
            if str(issue).strip()
        ]
        suggestion = str(item.get("suggestions", "") or "").strip()
        flags = sum(
            [
                not item.get("is_clear", True),
                not item.get("is_testable", True),
                item.get("is_ambiguous", False),
            ]
        )
        if flags == 0 and not issues and not suggestion:
            continue

        detail = (
            "; ".join(issues)
            or suggestion
            or "Requirement needs clarification before implementation."
        )
        finding: dict[str, Any] = {
            "title": requirement_id or "Requirement review finding",
            "detail": detail,
            "description": detail,
            "severity": "high" if flags >= 2 else "medium",
            "category": "review_quality",
            "source_reviewer": "single_reviewer",
            "reviewers": ["single_reviewer"],
        }
        if requirement_id:
            finding["finding_id"] = f"finding-{_slugify_identifier(requirement_id)}"
            finding["requirement_id"] = requirement_id
        findings.append(finding)
    return findings


def _derive_review_findings(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings = _copy_dict_list(report_payload.get("findings"))
    if findings:
        return findings

    parallel_review = _extract_parallel_review_payload(report_payload)
    findings = _copy_dict_list(parallel_review.get("findings"))
    if findings:
        return findings

    review_results = _copy_dict_list(report_payload.get("review_results"))
    return _derive_single_review_findings(review_results)


def _derive_review_open_questions(
    report_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    questions = _copy_dict_list(report_payload.get("open_questions"))
    if questions:
        return questions

    parallel_review = _extract_parallel_review_payload(report_payload)
    questions = _copy_dict_list(parallel_review.get("open_questions"))
    if questions:
        return questions

    questions = _copy_dict_list(report_payload.get("review_open_questions"))
    if questions:
        return questions

    return []


def _derive_review_risk_items(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    risk_items = _copy_dict_list(report_payload.get("risk_items"))
    if risk_items:
        return risk_items

    parallel_review = _extract_parallel_review_payload(report_payload)
    risk_items = _copy_dict_list(parallel_review.get("risk_items"))
    if risk_items:
        return risk_items

    risk_items = _copy_dict_list(report_payload.get("review_risk_items"))
    if risk_items:
        return risk_items

    derived: list[dict[str, Any]] = []
    for risk in _copy_dict_list(report_payload.get("risks")):
        title = str(
            risk.get("id", "") or risk.get("title", "") or "review-risk"
        ).strip()
        detail = str(
            risk.get("description", "") or risk.get("detail", "") or ""
        ).strip()
        severity = (
            str(risk.get("impact", "") or risk.get("severity", "") or "medium")
            .strip()
            .lower()
            or "medium"
        )
        derived.append(
            {
                "title": title,
                "detail": detail,
                "severity": severity,
                "category": str(risk.get("category", "") or "delivery_risk"),
                "mitigation": str(risk.get("mitigation", "") or "").strip(),
            }
        )
    return derived


def _derive_review_tool_calls(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls = _copy_dict_list(report_payload.get("tool_calls"))
    if tool_calls:
        return tool_calls

    parallel_review = _extract_parallel_review_payload(report_payload)
    tool_calls = _copy_dict_list(parallel_review.get("tool_calls"))
    if tool_calls:
        return tool_calls

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    return _copy_dict_list(parallel_review_meta.get("tool_calls"))


def _derive_reviewer_insights(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    reviewer_summaries = _copy_dict_list(report_payload.get("reviewer_summaries"))
    if reviewer_summaries:
        return reviewer_summaries

    parallel_review = _extract_parallel_review_payload(report_payload)
    reviewer_summaries = _copy_dict_list(parallel_review.get("reviewer_summaries"))
    if reviewer_summaries:
        return reviewer_summaries

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    reviewer_summaries = _copy_dict_list(parallel_review_meta.get("reviewer_insights"))
    if reviewer_summaries:
        return reviewer_summaries

    return []


def _derive_clarification(report_payload: dict[str, Any]) -> dict[str, Any]:
    clarification = report_payload.get("clarification")
    if isinstance(clarification, dict):
        return dict(clarification)

    review_clarification = report_payload.get("review_clarification")
    if isinstance(review_clarification, dict):
        return dict(review_clarification)

    parallel_review = _extract_parallel_review_payload(report_payload)
    clarification = parallel_review.get("clarification")
    if isinstance(clarification, dict):
        return dict(clarification)

    findings = _derive_review_findings(report_payload)
    reviewer_summaries = _derive_reviewer_insights(report_payload)
    return build_clarification_payload(findings, reviewer_summaries)


def _normalize_clarification_answers(answers: Any) -> list[dict[str, str]]:
    if not isinstance(answers, list):
        raise TypeError("answers must be a list")

    normalized: list[dict[str, str]] = []
    for item in answers:
        if not isinstance(item, dict):
            raise TypeError("each clarification answer must be an object")
        question_id = str(item.get("question_id", "") or "").strip()
        answer = str(item.get("answer", "") or "").strip()
        if not question_id or not answer:
            raise ValueError(
                "each clarification answer must include question_id and answer"
            )
        normalized.append({"question_id": question_id, "answer": answer})
    return normalized


def _resolve_parallel_artifact_path(
    run_dir: Path, parallel_review: dict[str, Any], key: str, fallback_name: str
) -> Path:
    artifacts = (
        parallel_review.get("artifacts")
        if isinstance(parallel_review.get("artifacts"), dict)
        else {}
    )
    raw_path = str(artifacts.get(key, "") or "").strip()
    candidate = Path(raw_path) if raw_path else run_dir / fallback_name
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    return candidate.resolve()


def _persist_parallel_review_refresh(
    run_dir: Path, report_payload: dict[str, Any]
) -> None:
    parallel_review = _extract_parallel_review_payload(report_payload)
    if not parallel_review:
        return

    review_result_path = _resolve_parallel_artifact_path(
        run_dir, parallel_review, "review_result_json", "review_result.json"
    )
    review_report_json_path = _resolve_parallel_artifact_path(
        run_dir, parallel_review, "review_report_json", "review_report.json"
    )
    review_report_md_path = _resolve_parallel_artifact_path(
        run_dir, parallel_review, "review_report_md", "review_report.md"
    )
    review_summary_md_path = _resolve_parallel_artifact_path(
        run_dir, parallel_review, "review_summary_md", "review_summary.md"
    )

    _write_json_object(review_result_path, parallel_review)
    _write_json_object(review_report_json_path, parallel_review)
    review_report_md_path.write_text(
        _render_review_report(parallel_review), encoding="utf-8"
    )
    review_summary_md_path.write_text(
        _render_summary(parallel_review), encoding="utf-8"
    )


def _extract_clarification_patch_context(
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    direct = report_payload.get("clarification_patch_context")
    if isinstance(direct, dict):
        return dict(direct)
    parallel_review = _extract_parallel_review_payload(report_payload)
    direct = parallel_review.get("clarification_patch_context")
    return dict(direct) if isinstance(direct, dict) else {}


async def _load_patch_prompt_context_async(
    *,
    patch_context: dict[str, Any],
) -> tuple[str, int, list[dict[str, Any]]] | None:
    artifact_version_id = str(
        patch_context.get("artifact_version_id", "") or ""
    ).strip()
    workspace_db_path = str(patch_context.get("workspace_db_path", "") or "").strip()
    if not artifact_version_id or not workspace_db_path:
        return None

    artifact_repository = ArtifactRepository(workspace_db_path)
    initialize_result = await artifact_repository.initialize()
    if not initialize_result.ok:
        return None
    version_result = await artifact_repository.get_version(artifact_version_id)
    if not version_result.ok or version_result.value is None:
        return None

    content_path = Path(str(version_result.value.content_path or "").strip())
    if not content_path.exists() or not content_path.is_file():
        return None
    try:
        artifact_payload = json.loads(content_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(artifact_payload, dict):
        return None

    blocks = artifact_payload.get("blocks")
    if not isinstance(blocks, list):
        return None
    return (
        str(artifact_payload.get("artifact_id") or version_result.value.artifact_key),
        int(artifact_payload.get("version") or version_result.value.version_number),
        [dict(item) for item in blocks if isinstance(item, dict)],
    )


def _write_clarification_patch_payload(
    run_dir: Path, report_payload: dict[str, Any], artifact_patch: dict[str, Any]
) -> None:
    report_payload["artifact_patch"] = artifact_patch
    if isinstance(report_payload.get("parallel_review_meta"), dict):
        report_payload["parallel_review_meta"]["artifact_patch_status"] = (
            artifact_patch.get("status", "")
        )
    if isinstance(report_payload.get("parallel-review_meta"), dict):
        report_payload["parallel-review_meta"]["artifact_patch_status"] = (
            artifact_patch.get("status", "")
        )
    _write_json_object(run_dir / "report.json", report_payload)


def answer_review_clarification(
    *,
    run_id: str,
    answers: list[dict[str, Any]],
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_path = run_dir / "report.json"
    report_payload = _load_json_object(report_path)
    if not report_payload:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")

    normalized_answers = _normalize_clarification_answers(answers)
    clarification = _derive_clarification(report_payload)
    if not clarification.get("triggered"):
        raise ValueError(f"clarification gate not active for run_id={run_id}")

    parallel_review = _extract_parallel_review_payload(report_payload)
    if parallel_review:
        current_findings = _copy_dict_list(parallel_review.get("findings"))
        updated_findings, updated_clarification = apply_clarification_answers(
            current_findings, clarification, normalized_answers
        )
        parallel_review["findings"] = updated_findings
        parallel_review["clarification"] = updated_clarification
        report_payload["parallel_review"] = parallel_review
    else:
        current_findings = _copy_dict_list(report_payload.get("findings"))
        updated_findings, updated_clarification = apply_clarification_answers(
            current_findings, clarification, normalized_answers
        )
        if current_findings:
            report_payload["findings"] = updated_findings

    report_payload["clarification"] = updated_clarification
    report_payload["review_clarification"] = updated_clarification

    if isinstance(report_payload.get("parallel_review_meta"), dict):
        report_payload["parallel_review_meta"]["clarification_status"] = (
            updated_clarification.get("status", "not_needed")
        )
    if isinstance(report_payload.get("parallel-review_meta"), dict):
        report_payload["parallel-review_meta"]["clarification_status"] = (
            updated_clarification.get("status", "not_needed")
        )

    _write_json_object(report_path, report_payload)
    _persist_parallel_review_refresh(run_dir, report_payload)
    pending_questions = [
        item
        for item in updated_clarification.get("questions", [])
        if isinstance(item, dict) and item.get("id")
    ]
    answered_question_ids = [item["question_id"] for item in normalized_answers]
    _append_audit_event_safe(
        run_dir,
        operation="clarification_answer",
        status=str(updated_clarification.get("status", "answered") or "answered"),
        run_id=run_id,
        audit_context=audit_context,
        details={
            "question_ids": answered_question_ids,
            "answered_count": len(answered_question_ids),
            "remaining_question_count": len(pending_questions),
            "clarification_status": str(
                updated_clarification.get("status", "") or "answered"
            ),
        },
    )
    return get_review_result_payload(run_id=run_id, outputs_root=outputs_root)


async def answer_review_clarification_async(
    *,
    run_id: str,
    answers: list[dict[str, Any]],
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    patch_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_payload = answer_review_clarification(
        run_id=run_id,
        answers=answers,
        outputs_root=outputs_root,
        audit_context=audit_context,
    )

    run_dir = _resolve_run_dir(run_id, outputs_root)
    report_path = run_dir / "report.json"
    report_payload = _load_json_object(report_path)
    resolved_patch_context = {
        **_extract_clarification_patch_context(report_payload),
        **(dict(patch_context) if isinstance(patch_context, dict) else {}),
    }
    if not resolved_patch_context:
        return result_payload

    artifact_patch_payload: dict[str, Any] = {
        "status": "context_available",
        "context": resolved_patch_context,
    }
    prompt_context = await _load_patch_prompt_context_async(
        patch_context=resolved_patch_context
    )
    if prompt_context is not None:
        artifact_id, base_version, blocks = prompt_context
        questions = [
            dict(item)
            for item in _derive_clarification(report_payload).get("questions", [])
            if isinstance(item, dict)
        ]
        question_lookup = {str(item.get("id", "")).strip(): item for item in questions}
        normalized_answers = _normalize_clarification_answers(answers)
        prompt_questions = []
        for item in normalized_answers:
            question = question_lookup.get(str(item.get("question_id", "")).strip(), {})
            prompt_questions.append(
                {
                    "question_id": item["question_id"],
                    "question": str(question.get("question", "")).strip(),
                    "answer": item["answer"],
                }
            )
        merged_question = "\n".join(
            f"- {entry['question_id']}: {entry['question']}"
            for entry in prompt_questions
            if entry["question"]
        )
        merged_answer = "\n".join(
            f"- {entry['question_id']}: {entry['answer']}"
            for entry in prompt_questions
            if entry["answer"]
        )
        artifact_patch_payload["prompt"] = build_clarification_to_patch_prompt(
            artifact_id=artifact_id,
            base_version=base_version,
            blocks=blocks,
            clarification_question=merged_question or "See clarification answers.",
            clarification_answer=merged_answer or "No answer provided.",
        )

    if isinstance(patch, dict):
        apply_options = {
            "workspace_db_path": resolved_patch_context.get("workspace_db_path"),
            "artifact_output_root": resolved_patch_context.get("artifact_output_root"),
            "failure_mode": resolved_patch_context.get("failure_mode"),
        }
        apply_result = await apply_artifact_patch_async(
            str(resolved_patch_context.get("artifact_version_id", "") or ""),
            patch,
            {key: value for key, value in apply_options.items() if value is not None},
        )
        artifact_patch_payload["status"] = str(apply_result.status)
        artifact_patch_payload["apply_result"] = apply_result.model_dump(mode="json")

    _write_clarification_patch_payload(run_dir, report_payload, artifact_patch_payload)
    refreshed = get_review_result_payload(run_id=run_id, outputs_root=outputs_root)
    refreshed["artifact_patch"] = artifact_patch_payload
    return refreshed


def answer_review_clarification_for_mcp(
    *,
    run_id: str,
    answers: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")
    outputs_root = resolved_options.get("outputs_root", "outputs")
    patch = resolved_options.get("patch")
    patch_context = resolved_options.get("patch_context")
    if patch is not None or patch_context is not None:
        answer_result = asyncio.run(
            answer_review_clarification_async(
                run_id=run_id,
                answers=answers,
                outputs_root=outputs_root,
                audit_context=resolved_options.get("audit_context"),
                patch=patch if isinstance(patch, dict) else None,
                patch_context=patch_context
                if isinstance(patch_context, dict)
                else None,
            )
        )
    else:
        answer_result = answer_review_clarification(
            run_id=run_id,
            answers=answers,
            outputs_root=outputs_root,
            audit_context=resolved_options.get("audit_context"),
        )
    summary = ReviewResultSummary(
        run_id=run_id,
        report_md_path=str(Path(outputs_root) / run_id / "report.md"),
        report_json_path=str(Path(outputs_root) / run_id / "report.json"),
        high_risk_ratio=0.0,
        coverage_ratio=0.0,
        revision_round=0,
        status="completed",
    )
    payload = _build_review_requirement_payload(summary)
    if isinstance(answer_result, dict) and isinstance(
        answer_result.get("artifact_patch"), dict
    ):
        payload["artifact_patch"] = dict(answer_result["artifact_patch"])
    return payload


def _derive_review_conflicts(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = _copy_dict_list(report_payload.get("conflicts"))
    if conflicts:
        return conflicts

    parallel_review = _extract_parallel_review_payload(report_payload)
    return _copy_dict_list(parallel_review.get("conflicts"))


def _derive_gating(report_payload: dict[str, Any]) -> dict[str, Any]:
    gating = report_payload.get("gating")
    if isinstance(gating, dict):
        return dict(gating)

    parallel_review = _extract_parallel_review_payload(report_payload)
    gating = parallel_review.get("gating")
    if isinstance(gating, dict):
        return dict(gating)

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    gating = parallel_review_meta.get("gating")
    return dict(gating) if isinstance(gating, dict) else {}


def _derive_reviewers_used(report_payload: dict[str, Any]) -> list[str]:
    direct = report_payload.get("reviewers_used")
    if isinstance(direct, list):
        return [str(item) for item in direct if str(item).strip()]

    parallel_review = _extract_parallel_review_payload(report_payload)
    direct = parallel_review.get("reviewers_used")
    if isinstance(direct, list):
        return [str(item) for item in direct if str(item).strip()]

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    direct = parallel_review_meta.get("reviewers_used")
    if isinstance(direct, list):
        return [str(item) for item in direct if str(item).strip()]
    return []


def _derive_reviewers_skipped(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct = report_payload.get("reviewers_skipped")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]

    parallel_review = _extract_parallel_review_payload(report_payload)
    direct = parallel_review.get("reviewers_skipped")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    direct = parallel_review_meta.get("reviewers_skipped")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]
    return []


def _derive_review_mode(report_payload: dict[str, Any]) -> str:
    review_mode = str(
        report_payload.get("mode", report_payload.get("review_mode", "")) or ""
    ).strip()
    if review_mode:
        return review_mode

    parallel_review = _extract_parallel_review_payload(report_payload)
    review_mode = str(
        parallel_review.get("mode", parallel_review.get("review_mode", "")) or ""
    ).strip()
    if review_mode:
        return review_mode

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    review_mode = str(
        parallel_review_meta.get("selected_mode", "")
        or parallel_review_meta.get("review_mode", "")
        or ""
    ).strip()
    return review_mode or "quick"


def _derive_review_report_path(
    summary: ReviewResultSummary, report_payload: dict[str, Any]
) -> str:
    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    artifact_paths = parallel_review_meta.get("artifact_paths")
    if isinstance(artifact_paths, dict):
        review_result_json = str(
            artifact_paths.get("review_result_json", "")
            or artifact_paths.get("review_report_json", "")
            or ""
        ).strip()
        if review_result_json:
            return review_result_json

    parallel_review = _extract_parallel_review_payload(report_payload)
    artifacts = parallel_review.get("artifacts")
    if isinstance(artifacts, dict):
        review_result_json = str(
            artifacts.get("review_result_json", "")
            or artifacts.get("review_report_json", "")
            or ""
        ).strip()
        if review_result_json:
            return review_result_json

    return summary.report_json_path or summary.report_md_path


def _derive_memory_hits(report_payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct = report_payload.get("memory_hits")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]

    parallel_review = _extract_parallel_review_payload(report_payload)
    direct = parallel_review.get("memory_hits")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]

    parallel_review_meta = _extract_parallel_review_meta_from_report(report_payload)
    direct = parallel_review_meta.get("memory_hits")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]
    return []


def _derive_similar_reviews_referenced(report_payload: dict[str, Any]) -> list[str]:
    for source in (
        report_payload,
        _extract_parallel_review_payload(report_payload),
        _extract_parallel_review_meta_from_report(report_payload),
    ):
        direct = (
            source.get("similar_reviews_referenced")
            if isinstance(source, dict)
            else None
        )
        if isinstance(direct, list):
            return [str(item) for item in direct if str(item).strip()]
    return []


def _derive_normalizer_cache_hit(report_payload: dict[str, Any]) -> bool:
    for source in (
        report_payload,
        _extract_parallel_review_payload(report_payload),
        _extract_parallel_review_meta_from_report(report_payload),
    ):
        if isinstance(source, dict) and "normalizer_cache_hit" in source:
            return bool(source.get("normalizer_cache_hit"))
    return False


def _derive_rag_enabled(report_payload: dict[str, Any]) -> bool:
    for source in (
        report_payload,
        _extract_parallel_review_payload(report_payload),
        _extract_parallel_review_meta_from_report(report_payload),
    ):
        if isinstance(source, dict) and "rag_enabled" in source:
            return bool(source.get("rag_enabled"))
    return False


def _build_review_requirement_payload(summary: ReviewResultSummary) -> dict[str, Any]:
    report_payload = _load_json_object(Path(summary.report_json_path))
    meta = _extract_parallel_review_meta_from_report(report_payload)
    gating = _derive_gating(report_payload)
    reviewers_used = _derive_reviewers_used(report_payload)
    reviewers_skipped = _derive_reviewers_skipped(report_payload)
    memory_hits = _derive_memory_hits(report_payload)
    similar_reviews_referenced = _derive_similar_reviews_referenced(report_payload)
    normalizer_cache_hit = _derive_normalizer_cache_hit(report_payload)
    rag_enabled = _derive_rag_enabled(report_payload)
    return {
        "review_id": summary.run_id,
        "run_id": summary.run_id,
        "findings": _derive_review_findings(report_payload),
        "open_questions": _derive_review_open_questions(report_payload),
        "risk_items": _derive_review_risk_items(report_payload),
        "conflicts": _derive_review_conflicts(report_payload),
        "tool_calls": _derive_review_tool_calls(report_payload),
        "reviewer_insights": _derive_reviewer_insights(report_payload),
        "memory_hits": memory_hits,
        "similar_reviews_referenced": similar_reviews_referenced,
        "normalizer_cache_hit": normalizer_cache_hit,
        "report_path": _derive_review_report_path(summary, report_payload),
        "review_mode": _derive_review_mode(report_payload),
        "mode": _derive_review_mode(report_payload),
        "gating": gating,
        "reviewers_used": reviewers_used,
        "reviewers_skipped": reviewers_skipped,
        "clarification": _derive_clarification(report_payload),
        "artifact_patch": _derive_artifact_patch(report_payload),
        "meta": {
            "review_mode": _derive_review_mode(report_payload),
            "gating": gating,
            "reviewers_used": reviewers_used,
            "reviewers_skipped": reviewers_skipped,
            "clarification": _derive_clarification(report_payload),
            "artifact_patch": _derive_artifact_patch(report_payload),
            "tool_calls": _derive_review_tool_calls(report_payload),
            "reviewer_insights": _derive_reviewer_insights(report_payload),
            "memory_hits": memory_hits,
            "memory_hit_count": len(memory_hits),
            "similar_reviews_referenced": similar_reviews_referenced,
            "normalizer_cache_hit": normalizer_cache_hit,
            "rag_enabled": rag_enabled,
            **(
                {"summary": report_payload.get("summary")}
                if isinstance(report_payload.get("summary"), dict)
                else {}
            ),
            **meta,
        },
    }


def _build_cached_node_outputs(report_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "parsed_items": report_payload.get("parsed_items", []),
        "plan": report_payload.get("plan", {}),
        "tasks": report_payload.get("tasks", []),
        "risks": report_payload.get("risks", []),
        "evidence": report_payload.get("evidence", {}),
        "implementation_plan": report_payload.get("implementation_plan", {}),
        "test_plan": report_payload.get("test_plan", {}),
        "codex_prompt_handoff": report_payload.get("codex_prompt_handoff", {}),
        "claude_code_prompt_handoff": report_payload.get(
            "claude_code_prompt_handoff", {}
        ),
        "review_results": report_payload.get("review_results", []),
        "parallel_review": report_payload.get("parallel_review", {}),
        "parallel_review_meta": report_payload.get(
            "parallel_review_meta", report_payload.get("parallel-review_meta", {})
        ),
        "review_open_questions": report_payload.get("review_open_questions", []),
        "review_risk_items": report_payload.get("review_risk_items", []),
        "final_report": report_payload.get("final_report", ""),
    }


def _read_optional_text(path: str) -> str:
    resolved = str(path or "").strip()
    if not resolved:
        return ""
    candidate = Path(resolved)
    if candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return ""


async def _attach_selective_rerun_plan_if_requested(
    payload: dict[str, Any],
    *,
    summary: ReviewResultSummary,
    prd_text: str | None,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    resolved_options = dict(options or {})
    selective = resolved_options.get("selective_rerun")
    if not isinstance(selective, dict):
        return payload

    report_payload = _load_json_object(Path(summary.report_json_path))
    prd_v1 = str(selective.get("prd_v1") or "").strip() or _read_optional_text(
        summary.prd_v1_path
    )
    prd_v2 = str(selective.get("prd_v2") or "").strip() or str(prd_text or "").strip()
    if not prd_v1 or not prd_v2:
        return payload

    rerun_plan = await build_rerun_plan_async(
        prd_v1=prd_v1,
        prd_v2=prd_v2,
        cached_node_outputs=_build_cached_node_outputs(report_payload),
        baseline_snapshot=report_payload,
    )
    payload["artifact_diff"] = dict(rerun_plan.get("artifact_diff", {}))
    payload["rerun_plan"] = rerun_plan
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    meta["rerun_plan"] = rerun_plan
    payload["meta"] = meta
    return payload


async def _review_summary_for_mcp_async(
    *,
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
    options: dict[str, Any] | None = None,
    invocation_meta: dict[str, Any] | None = None,
) -> ReviewResultSummary:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")

    run_id_raw = resolved_options.get("run_id")
    run_id = str(run_id_raw).strip() if run_id_raw is not None else ""
    outputs_root = str(resolved_options.get("outputs_root", "outputs"))

    config_overrides: dict[str, Any] = {
        "outputs_root": outputs_root,
        "audit_context": resolved_options.get("audit_context"),
    }
    review_mode_override = resolved_options.get("review_mode_override")
    if isinstance(review_mode_override, str) and review_mode_override.strip():
        config_overrides["review_mode_override"] = review_mode_override.strip()
    mode = resolved_options.get("mode")
    if isinstance(mode, str) and mode.strip():
        config_overrides["mode"] = mode.strip()
    for option_key in (
        "review_memory_path",
        "review_memory_enabled",
        "review_memory_seeds_dir",
        "review_memory_db_path",
        "review_memory_extract_enabled",
        "review_memory_max_kept",
        "review_memory_mode",
        "normalizer_cache_path",
        "llm",
        "fast_llm",
        "smart_llm",
        "strategic_llm",
        "temperature",
        "llm_kwargs",
        "reasoning_effort",
        "LLM",
        "FAST_LLM",
        "SMART_LLM",
        "STRATEGIC_LLM",
        "TEMPERATURE",
        "LLM_KWARGS",
        "REASONING_EFFORT",
    ):
        if option_key in resolved_options:
            config_overrides[option_key] = resolved_options.get(option_key)

    summary = await review_prd_text_async(
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
        run_id=run_id or None,
        config_overrides=config_overrides,
    )

    trace_meta = {"invoked_via": "mcp"}
    if invocation_meta:
        trace_meta.update(invocation_meta)
    _attach_trace_invocation(summary, trace_meta)
    return summary


async def review_prd_for_mcp_async(
    *,
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
    options: dict[str, Any] | None = None,
    invocation_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = await _review_summary_for_mcp_async(
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
        options=options,
        invocation_meta=invocation_meta,
    )

    return {
        "run_id": summary.run_id,
        "status": summary.status,
        "metrics": {
            "coverage_ratio": summary.coverage_ratio,
            "high_risk_ratio": summary.high_risk_ratio,
            "revision_round": summary.revision_round,
        },
        "artifacts": {
            "report_md_path": summary.report_md_path,
            "report_json_path": summary.report_json_path,
            "trace_path": summary.run_trace_path,
            "implementation_pack_path": summary.implementation_pack_path,
            "test_pack_path": summary.test_pack_path,
            "execution_pack_path": summary.execution_pack_path,
            "prd_v1_path": summary.prd_v1_path,
            "task_bundle_v1_path": summary.task_bundle_v1_path,
            "delivery_bundle_path": summary.delivery_bundle_path,
        },
    }


async def review_requirement_for_mcp_async(
    *,
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
    options: dict[str, Any] | None = None,
    invocation_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a review-only facade payload without bundle or execution workflow fields."""
    summary = await _review_summary_for_mcp_async(
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
        options=options,
        invocation_meta=invocation_meta,
    )
    payload = _build_review_requirement_payload(summary)
    return await _attach_selective_rerun_plan_if_requested(
        payload,
        summary=summary,
        prd_text=prd_text,
        options=options,
    )


async def prepare_agent_handoff_for_mcp_async(
    *,
    agent: str = "all",
    run_id: str | None = None,
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
    options: dict[str, Any] | None = None,
    invocation_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")

    resolved_run_id = str(run_id or resolved_options.get("run_id", "") or "").strip()
    if not resolved_run_id:
        summary = await _review_summary_for_mcp_async(
            prd_text=prd_text,
            prd_path=prd_path,
            source=source,
            options=resolved_options,
            invocation_meta=invocation_meta,
        )
        resolved_run_id = summary.run_id

    outputs_root = str(resolved_options.get("outputs_root", "outputs"))
    delivery_bundle_path = Path(outputs_root) / resolved_run_id / "delivery_bundle.json"
    if not delivery_bundle_path.exists():
        generate_delivery_bundle_for_mcp(
            run_id=resolved_run_id, options=resolved_options
        )

    from pm_pal.service.execution_service import prepare_agent_handoff_for_run_for_mcp

    return prepare_agent_handoff_for_run_for_mcp(
        run_id=resolved_run_id,
        agent=agent,
        options=resolved_options,
    )


def review_prd_for_mcp(
    *,
    prd_text: str | None,
    prd_path: str | None,
    source: str | None,
    options: dict[str, Any] | None = None,
    invocation_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            review_prd_for_mcp_async(
                prd_text=prd_text,
                prd_path=prd_path,
                source=source,
                options=options,
                invocation_meta=invocation_meta,
            )
        )
    raise RuntimeError(
        "review_prd_for_mcp cannot run inside an active event loop; use review_prd_for_mcp_async"
    )
