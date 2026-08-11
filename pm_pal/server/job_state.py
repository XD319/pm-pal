from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, model_validator

from pm_pal.server.sse import ProgressBroadcaster
from pm_pal.service.report_service import RUN_ID_PATTERN
from pm_pal.service.review_service import (
    classify_review_input_error,
    review_prd_text_async,
)

RUN_PROGRESS_FILENAME = "run_progress.json"
PRIMARY_NODES = (
    "parser",
    "parallel_start",
    "planner",
    "risk",
    "review_join",
    "delivery_planning",
    "reviewer",
    "route_decider",
    "reporter",
    "finalize_artifacts",
)
TRACKED_NODES = PRIMARY_NODES + ("clarify",)
NODE_ORDER = (
    "parser",
    "parallel_start",
    "planner",
    "risk",
    "review_join",
    "delivery_planning",
    "reviewer",
    "route_decider",
    "clarify",
    "reporter",
    "finalize_artifacts",
)
RUN_LIST_ARTIFACTS: dict[str, str] = {
    "report_md": "report.md",
    "report_json": "report.json",
    "run_trace": "run_trace.json",
    "review_report_json": "review_report.json",
    "risk_items_json": "risk_items.json",
    "open_questions_json": "open_questions.json",
    "review_summary_md": "review_summary.md",
}


class ReviewCreateRequest(BaseModel):
    prd_text: str | None = None
    prd_path: str | None = None
    source: str | None = None
    mode: Literal["auto", "quick", "full"] | None = None
    llm: str | None = None
    # Legacy aliases still accepted; all collapse to llm :-)
    fast_llm: str | None = None
    smart_llm: str | None = None
    strategic_llm: str | None = None
    temperature: float | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    llm_kwargs: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_input(self) -> "ReviewCreateRequest":
        has_source = bool(self.source and self.source.strip())
        if has_source:
            return self

        has_text = bool(self.prd_text and self.prd_text.strip())
        has_path = bool(self.prd_path and self.prd_path.strip())
        if has_text == has_path:
            raise ValueError("Provide source, or exactly one of prd_text or prd_path.")
        return self


class ClarificationAnswerItem(BaseModel):
    question_id: str
    answer: str


class ClarificationAnswerRequest(BaseModel):
    answers: list[ClarificationAnswerItem]
    patch: dict[str, Any] | None = None
    patch_context: dict[str, Any] | None = None


class RevisionStageRequest(BaseModel):
    decision: Literal[
        "generate_from_review", "custom_requirements", "upload_notes", "skip_revision"
    ]


class RevisionInputRequest(BaseModel):
    selected_review_basis: Literal[
        "all_review_suggestions", "partial_review_suggestions", "user_only"
    ]
    extra_instructions: str = ""
    meeting_notes_text: str = ""
    meeting_notes_file_ref: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_notes_input(self) -> "RevisionInputRequest":
        notes_text = str(self.meeting_notes_text or "").strip()
        if notes_text:
            return self
        if (
            isinstance(self.meeting_notes_file_ref, dict)
            and self.meeting_notes_file_ref
        ):
            return self
        return self


class RevisionConfirmRequest(BaseModel):
    action: Literal[
        "confirm_revision", "regenerate_revision", "continue_without_revision"
    ]
    additional_requirements: str = ""


@dataclass
class JobRecord:
    run_id: str
    run_dir: Path
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    current_node: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    error: str = ""
    error_code: str = ""
    report_paths: dict[str, str] = field(default_factory=dict)
    node_progress: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            name: {"status": "pending", "runs": 0} for name in TRACKED_NODES
        }
    )
    task: asyncio.Task[Any] | None = None

    def as_progress_payload(self) -> dict[str, Any]:
        ordered_nodes = {
            name: self.node_progress[name]
            for name in ordered_node_names(self.node_progress)
        }
        return {
            "percent": progress_percent_from_nodes(ordered_nodes, status=self.status),
            "current_node": self.current_node,
            "nodes": ordered_nodes,
            "updated_at": self.updated_at,
            "error": self.error,
        }


def run_id_to_datetime(run_id: str) -> datetime | None:
    normalized = str(run_id or "").strip()
    if not RUN_ID_PATTERN.fullmatch(normalized):
        return None
    return datetime.strptime(normalized, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def timestamp_to_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def safe_iso_to_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def artifact_presence(run_dir: Path) -> dict[str, bool]:
    return {
        key: (run_dir / filename).exists()
        for key, filename in RUN_LIST_ARTIFACTS.items()
    }


def latest_run_timestamp(run_dir: Path) -> float:
    timestamps = [run_dir.stat().st_mtime]
    for child in run_dir.iterdir():
        try:
            timestamps.append(child.stat().st_mtime)
        except OSError:
            continue
    return max(timestamps)


def ordered_node_names(nodes: dict[str, Any]) -> list[str]:
    known = [name for name in NODE_ORDER if name in nodes]
    extras = sorted(name for name in nodes if name not in NODE_ORDER)
    return [*known, *extras]


def build_run_list_entry(
    run_dir: Path,
    *,
    persisted_status_payload,
    job: JobRecord | None = None,
) -> dict[str, Any]:
    run_id = run_dir.name
    artifacts = artifact_presence(run_dir)

    if job is not None:
        created_dt = safe_iso_to_datetime(job.created_at)
        if created_dt is None:
            created_dt = run_id_to_datetime(run_id)
        created_at = (
            created_dt.isoformat()
            if created_dt
            else timestamp_to_iso(run_dir.stat().st_ctime)
        )

        updated_dt = safe_iso_to_datetime(job.updated_at)
        updated_at = (
            updated_dt.isoformat()
            if updated_dt
            else timestamp_to_iso(latest_run_timestamp(run_dir))
        )
        status = job.status
    else:
        persisted = persisted_status_payload(run_id, run_dir)
        created_dt = safe_iso_to_datetime(str(persisted.get("created_at", "") or ""))
        if created_dt is None:
            created_dt = run_id_to_datetime(run_id)
        created_at = (
            created_dt.isoformat()
            if created_dt
            else timestamp_to_iso(run_dir.stat().st_ctime)
        )

        updated_dt = safe_iso_to_datetime(str(persisted.get("updated_at", "") or ""))
        updated_at = (
            updated_dt.isoformat()
            if updated_dt
            else timestamp_to_iso(latest_run_timestamp(run_dir))
        )
        status = str(persisted.get("status", "failed") or "failed")

    return {
        "run_id": run_id,
        "status": status,
        "created_at": created_at,
        "updated_at": updated_at,
        "artifact_presence": artifacts,
    }


def run_sort_timestamp(run_dir: Path, job: JobRecord | None = None) -> float:
    if job is not None:
        created_dt = safe_iso_to_datetime(job.created_at)
        if created_dt is not None:
            return created_dt.timestamp()
    run_dt = run_id_to_datetime(run_dir.name)
    if run_dt is not None:
        return run_dt.timestamp()
    return latest_run_timestamp(run_dir)


def resolve_review_inputs(payload: ReviewCreateRequest) -> dict[str, str | None]:
    if payload.source and payload.source.strip():
        return {
            "prd_text": None,
            "prd_path": None,
            "source": payload.source.strip(),
            "mode": payload.mode,
        }

    if payload.prd_text:
        return {
            "prd_text": payload.prd_text,
            "prd_path": None,
            "source": None,
            "mode": payload.mode,
        }

    if not payload.prd_path:
        raise HTTPException(status_code=400, detail="Missing prd_path")

    prd_file = Path(payload.prd_path).expanduser()
    if not prd_file.is_absolute():
        prd_file = Path.cwd() / prd_file
    if not prd_file.exists() or not prd_file.is_file():
        raise HTTPException(status_code=404, detail=f"PRD file not found: {prd_file}")
    return {
        "prd_text": None,
        "prd_path": str(prd_file),
        "source": None,
        "mode": payload.mode,
    }


def resolve_runtime_llm_options(payload: ReviewCreateRequest) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in ("llm", "smart_llm", "fast_llm", "strategic_llm"):
        value = getattr(payload, key)
        if value is not None:
            options["llm"] = value
            break
    for key in ("temperature", "reasoning_effort", "llm_kwargs"):
        value = getattr(payload, key)
        if value is not None:
            options[key] = value
    return options


def apply_progress_event(
    job: JobRecord, event: str, node_name: str, state: dict[str, Any]
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    node = job.node_progress.setdefault(node_name, {"status": "pending", "runs": 0})
    if event == "start":
        job.current_node = node_name
        node["status"] = "running"
        node["runs"] = int(node.get("runs", 0) or 0) + 1
        node["last_start"] = now
    elif event == "end":
        trace = state.get("trace", {}) if isinstance(state, dict) else {}
        node_status = "completed"
        if isinstance(trace, dict):
            trace_status = trace.get(node_name, {}).get("status")
            if isinstance(trace_status, str) and trace_status:
                node_status = trace_status_to_node_status(trace_status)
        node["status"] = node_status
        node["last_end"] = now
    job.updated_at = now
    persist_job_snapshot(job)


def read_trace_progress(run_dir: Path) -> dict[str, Any] | None:
    trace_path = run_dir / "run_trace.json"
    if not trace_path.exists():
        return None
    try:
        trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(trace_data, dict):
        return None

    observed_nodes = {
        name: payload
        for name, payload in trace_data.items()
        if isinstance(payload, dict)
        and any(key in payload for key in ("status", "start", "end"))
    }
    if not observed_nodes:
        return None

    nodes = {
        name: {"status": "pending", "runs": 0}
        for name in ordered_node_names(observed_nodes)
    }
    for node_name in ordered_node_names(observed_nodes):
        node_trace = trace_data.get(node_name)
        if not isinstance(node_trace, dict):
            continue
        status = node_trace.get("status", "ok")
        nodes[node_name] = {
            "status": trace_status_to_node_status(status),
            "runs": 1,
            "last_start": node_trace.get("start"),
            "last_end": node_trace.get("end"),
        }
    return nodes


def progress_snapshot_path(run_dir: Path) -> Path:
    return run_dir / RUN_PROGRESS_FILENAME


def ordered_nodes_payload(nodes: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(nodes, dict):
        return {}

    ordered: dict[str, dict[str, Any]] = {}
    for node_name in ordered_node_names(nodes):
        node_payload = nodes.get(node_name)
        if isinstance(node_payload, dict):
            ordered[node_name] = dict(node_payload)
        else:
            ordered[node_name] = {"status": "pending", "runs": 0}
    return ordered


def trace_status_to_node_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "ok", "success", "completed", "partial_success", "skipped"}:
        return "completed"
    if normalized == "running":
        return "running"
    return "failed"


def node_progress_weight(value: Any) -> float:
    normalized = str(value or "").strip().lower()
    if normalized in {"completed", "failed"}:
        return 1.0
    if normalized == "running":
        return 0.5
    return 0.0


def progress_percent_from_nodes(nodes: dict[str, Any] | None, *, status: str) -> int:
    normalized_nodes = nodes if isinstance(nodes, dict) else {}
    completed_weight = sum(
        node_progress_weight(normalized_nodes.get(node_name, {}).get("status"))
        for node_name in PRIMARY_NODES
    )
    percent = int((completed_weight / len(PRIMARY_NODES)) * 100) if PRIMARY_NODES else 0
    if status in {"queued", "running"}:
        return min(percent, 99)
    if status == "completed":
        return 100
    return percent


def terminal_sse_payload(
    run_id: str, status: str, timestamp: str, *, error: str = ""
) -> dict[str, Any]:
    payload = {
        "node": "run",
        "status": str(status or "").strip(),
        "timestamp": str(timestamp or "").strip()
        or datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "terminal": True,
    }
    if error:
        payload["error"] = error
    return payload


def terminal_payload_for_job(job: JobRecord) -> dict[str, Any] | None:
    if job.status not in {"completed", "failed"}:
        return None
    return terminal_sse_payload(job.run_id, job.status, job.updated_at, error=job.error)


def terminal_payload_for_run_dir(
    run_id: str,
    run_dir: Path,
    *,
    persisted_status_payload,
) -> dict[str, Any] | None:
    persisted = persisted_status_payload(run_id, run_dir)
    status = str(persisted.get("status", "") or "").strip()
    if status not in {"completed", "failed"}:
        return None
    progress = (
        persisted.get("progress", {})
        if isinstance(persisted.get("progress"), dict)
        else {}
    )
    error = str(progress.get("error", "") or "")
    return terminal_sse_payload(
        run_id, status, str(persisted.get("updated_at", "") or ""), error=error
    )


def job_status_payload(job: JobRecord) -> dict[str, Any]:
    payload = {
        "run_id": job.run_id,
        "status": job.status,
        "progress": job.as_progress_payload(),
        "report_paths": dict(job.report_paths),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if job.status == "failed" and job.error_code:
        payload["error"] = {"code": job.error_code, "message": job.error}
    elif job.status == "failed" and job.error:
        payload["error"] = {"code": "run_error", "message": job.error}
    return payload


def persist_job_snapshot(job: JobRecord) -> None:
    job.run_dir.mkdir(parents=True, exist_ok=True)
    write_job_snapshot(job.run_dir, job_status_payload(job))


def write_job_snapshot(run_dir: Path, payload: dict[str, Any]) -> None:
    from pm_pal.utils.redaction import redact_mapping

    progress_snapshot_path(run_dir).write_text(
        json.dumps(redact_mapping(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_progress_snapshot(run_dir: Path) -> dict[str, Any] | None:
    snapshot_path = progress_snapshot_path(run_dir)
    if not snapshot_path.exists():
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    progress = (
        dict(payload.get("progress", {}) or {})
        if isinstance(payload.get("progress"), dict)
        else {}
    )
    nodes = ordered_nodes_payload(progress.get("nodes"))
    progress["nodes"] = nodes
    progress["percent"] = progress_percent_from_nodes(
        nodes, status=str(payload.get("status", "running") or "running")
    )
    progress.setdefault("current_node", "")
    progress.setdefault("updated_at", str(payload.get("updated_at", "") or ""))
    progress.setdefault("error", "")
    payload["progress"] = progress
    return payload


def merge_trace_nodes(
    progress_nodes: dict[str, Any] | None, trace_nodes: dict[str, Any] | None
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    if isinstance(progress_nodes, dict):
        for node_name, node_payload in progress_nodes.items():
            if isinstance(node_payload, dict):
                merged[node_name] = dict(node_payload)
    if isinstance(trace_nodes, dict):
        for node_name, node_payload in trace_nodes.items():
            if not isinstance(node_payload, dict):
                continue
            current = dict(merged.get(node_name, {}))
            trace_status = str(node_payload.get("status", "pending") or "pending")
            current_status = str(current.get("status", "pending") or "pending")
            if current_status in {"pending", "running"} and trace_status in {
                "completed",
                "failed",
            }:
                current["status"] = trace_status
            else:
                current.setdefault("status", trace_status)
            current["runs"] = max(
                int(current.get("runs", 0) or 0), int(node_payload.get("runs", 0) or 0)
            )
            if node_payload.get("last_start"):
                current["last_start"] = node_payload.get("last_start")
            else:
                current.setdefault("last_start", "")
            if node_payload.get("last_end"):
                current["last_end"] = node_payload.get("last_end")
            else:
                current.setdefault("last_end", "")
            merged[node_name] = current
    return ordered_nodes_payload(merged)


def inactive_run_message(run_id: str) -> str:
    return f"Run {run_id} is no longer active. The backend process was restarted or exited before the review finished."


def persisted_status_payload(run_id: str, run_dir: Path) -> dict[str, Any]:
    report_md = run_dir / "report.md"
    report_json = run_dir / "report.json"
    run_trace = run_dir / "run_trace.json"
    completed = report_md.exists() and report_json.exists()

    created_dt = run_id_to_datetime(run_id)
    default_created_at = (
        created_dt.isoformat()
        if created_dt
        else timestamp_to_iso(run_dir.stat().st_ctime)
    )
    default_updated_at = timestamp_to_iso(latest_run_timestamp(run_dir))

    payload = read_progress_snapshot(run_dir) or {
        "run_id": run_id,
        "status": "completed" if completed else "failed",
        "created_at": default_created_at,
        "updated_at": default_updated_at,
        "progress": {
            "percent": 100 if completed else 0,
            "current_node": "",
            "nodes": {},
            "updated_at": default_updated_at,
            "error": "",
        },
        "report_paths": {},
    }

    progress = (
        dict(payload.get("progress", {}) or {})
        if isinstance(payload.get("progress"), dict)
        else {}
    )
    trace_nodes = read_trace_progress(run_dir) or {}
    merged_nodes = merge_trace_nodes(progress.get("nodes"), trace_nodes)
    progress["nodes"] = merged_nodes

    created_at = str(payload.get("created_at", "") or default_created_at)
    updated_at = str(
        payload.get("updated_at", "")
        or progress.get("updated_at", "")
        or default_updated_at
    )

    payload["run_id"] = run_id
    payload["created_at"] = created_at
    payload["updated_at"] = updated_at

    report_paths = (
        dict(payload.get("report_paths", {}) or {})
        if isinstance(payload.get("report_paths"), dict)
        else {}
    )
    report_paths.setdefault("report_md", str(report_md))
    report_paths.setdefault("report_json", str(report_json))
    report_paths.setdefault("run_trace", str(run_trace))
    payload["report_paths"] = report_paths

    error_payload = (
        payload.get("error") if isinstance(payload.get("error"), dict) else None
    )
    status = (
        str(payload.get("status", "") or ("completed" if completed else "failed"))
        .strip()
        .lower()
    )

    if completed:
        payload["status"] = "completed"
        progress["percent"] = 100
        progress["current_node"] = ""
        progress["error"] = ""
        payload.pop("error", None)
    else:
        message = str(progress.get("error", "") or "")
        if error_payload and error_payload.get("message"):
            message = message or str(error_payload.get("message", "") or "")
        if status in {"queued", "running"}:
            message = message or inactive_run_message(run_id)
            payload["status"] = "failed"
            payload["error"] = {"code": "run_interrupted", "message": message}
            progress["error"] = message
        elif status == "failed":
            message = message or inactive_run_message(run_id)
            code = (
                str(error_payload.get("code", "") or "run_failed")
                if error_payload
                else "run_failed"
            )
            payload["error"] = {"code": code, "message": message}
            progress["error"] = message
        progress["percent"] = progress_percent_from_nodes(
            progress["nodes"],
            status=str(payload.get("status", "failed") or "failed"),
        )

    progress.setdefault("current_node", "")
    progress["updated_at"] = updated_at
    payload["progress"] = progress
    return payload


def hydrate_job_record(
    run_dir: Path, payload: dict[str, Any] | None = None
) -> JobRecord | None:
    hydrated = payload or read_progress_snapshot(run_dir)
    if not isinstance(hydrated, dict):
        return None

    run_id = str(hydrated.get("run_id", run_dir.name) or run_dir.name)
    progress = (
        hydrated.get("progress") if isinstance(hydrated.get("progress"), dict) else {}
    )
    nodes = ordered_nodes_payload(progress.get("nodes"))
    error_payload = (
        hydrated.get("error") if isinstance(hydrated.get("error"), dict) else {}
    )
    return JobRecord(
        run_id=run_id,
        run_dir=run_dir,
        status=str(hydrated.get("status", "failed") or "failed"),
        current_node=str(progress.get("current_node", "") or ""),
        created_at=str(
            hydrated.get("created_at", "") or datetime.now(timezone.utc).isoformat()
        ),
        updated_at=str(
            hydrated.get("updated_at", "") or datetime.now(timezone.utc).isoformat()
        ),
        error=str(error_payload.get("message", "") or progress.get("error", "") or ""),
        error_code=str(error_payload.get("code", "") or ""),
        report_paths=dict(hydrated.get("report_paths", {}) or {})
        if isinstance(hydrated.get("report_paths"), dict)
        else {},
        node_progress=nodes
        or {name: {"status": "pending", "runs": 0} for name in TRACKED_NODES},
    )


def normalize_persisted_job(run_id: str, run_dir: Path) -> dict[str, Any]:
    payload = persisted_status_payload(run_id, run_dir)
    write_job_snapshot(run_dir, payload)
    return payload


def result_unavailable_detail(
    run_id: str,
    job: JobRecord | None,
    *,
    outputs_root: Path,
) -> dict[str, Any]:
    if job is not None:
        status = job.status
        if status in {"queued", "running"}:
            detail = {
                "code": "result_not_ready",
                "message": f"report.json not ready for run_id={run_id}",
                "run_id": run_id,
                "status": status,
            }
        else:
            detail = {
                "code": job.error_code if job.error_code else "result_unavailable",
                "message": job.error
                if job.error
                else f"review result unavailable for run_id={run_id}",
                "run_id": run_id,
                "status": status,
            }
        detail["progress"] = job.as_progress_payload()
        if job.error_code:
            detail["error"] = {"code": job.error_code, "message": job.error}
        return detail

    persisted = persisted_status_payload(run_id, outputs_root / run_id)
    persisted_error = (
        persisted.get("error") if isinstance(persisted.get("error"), dict) else {}
    )
    status = str(persisted.get("status", "failed") or "failed")
    if status in {"queued", "running"}:
        detail = {
            "code": "result_not_ready",
            "message": f"report.json not ready for run_id={run_id}",
            "run_id": run_id,
            "status": status,
        }
    else:
        detail = {
            "code": str(persisted_error.get("code", "") or "result_unavailable"),
            "message": str(
                persisted_error.get("message", "")
                or persisted.get("progress", {}).get("error", "")
                or f"review result unavailable for run_id={run_id}"
            ),
            "run_id": run_id,
            "status": status,
        }
    if isinstance(persisted.get("progress"), dict):
        detail["progress"] = persisted["progress"]
    if persisted_error:
        detail["error"] = persisted_error
    return detail


async def run_job(
    job: JobRecord,
    *,
    outputs_root: Path,
    prd_text: str | None = None,
    prd_path: str | None = None,
    source: str | None = None,
    mode: str | None = None,
    llm_options: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
) -> None:
    job.status = "running"
    job.error = ""
    job.error_code = ""
    job.updated_at = datetime.now(timezone.utc).isoformat()
    persist_job_snapshot(job)
    try:
        summary = await review_prd_text_async(
            prd_text=prd_text,
            prd_path=prd_path,
            source=source,
            run_id=job.run_id,
            config_overrides={
                "outputs_root": outputs_root,
                "progress_hook": lambda event, node, state: apply_progress_event(
                    job, event, node, state
                ),
                **(
                    {"audit_context": dict(audit_context)}
                    if isinstance(audit_context, dict) and audit_context
                    else {}
                ),
                **({"mode": mode} if isinstance(mode, str) and mode.strip() else {}),
                **(dict(llm_options) if isinstance(llm_options, dict) else {}),
            },
        )
        job.status = summary.status
        job.current_node = ""
        job.report_paths = summary.to_report_paths()
    except Exception as exc:
        job.status = "failed"
        classified_error = classify_review_input_error(exc)
        job.error_code = (
            classified_error.code if classified_error is not None else "INTERNAL_ERROR"
        )
        job.error = (
            classified_error.message if classified_error is not None else str(exc)
        )
        job.current_node = ""
    finally:
        job.updated_at = datetime.now(timezone.utc).isoformat()
        persist_job_snapshot(job)
        ProgressBroadcaster().publish(
            job.run_id,
            "run_status",
            terminal_sse_payload(
                job.run_id, job.status, job.updated_at, error=job.error
            ),
        )
