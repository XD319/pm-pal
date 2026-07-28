"""Audit log persistence and query helpers for workflow governance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prd_pal.utils.redaction import redact_mapping
from prd_pal.utils.time import utc_now_iso as _utc_now_iso

AUDIT_LOG_FILENAME = "audit_log.jsonl"


def normalize_audit_context(audit_context: dict[str, Any] | None) -> dict[str, Any]:
    return dict(audit_context) if isinstance(audit_context, dict) else {}


def resolve_audit_client_metadata(
    audit_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = normalize_audit_context(audit_context)
    client_metadata = context.get("client_metadata")
    return dict(client_metadata) if isinstance(client_metadata, dict) else {}


def resolve_audit_actor(
    audit_context: dict[str, Any] | None, *, default: str = "system"
) -> str:
    context = normalize_audit_context(audit_context)
    actor = str(context.get("actor") or "").strip()
    if actor:
        return actor

    client_metadata = resolve_audit_client_metadata(context)
    client_id = str(client_metadata.get("client_id") or "").strip()
    if client_id:
        return client_id

    return str(default or "").strip()


def resolve_audit_source(
    audit_context: dict[str, Any] | None, *, default: str = "service"
) -> str:
    context = normalize_audit_context(audit_context)
    source = str(
        context.get("source") or context.get("tool_name") or default or ""
    ).strip()
    return source


def audit_log_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / AUDIT_LOG_FILENAME


def append_audit_event(
    run_dir: str | Path,
    *,
    operation: str,
    status: str,
    run_id: str = "",
    bundle_id: str = "",
    task_id: str = "",
    actor: str = "",
    source: str = "",
    details: dict[str, Any] | None = None,
    client_metadata: dict[str, Any] | None = None,
    retry: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    run_dir_path = Path(run_dir)
    run_dir_path.mkdir(parents=True, exist_ok=True)
    context = normalize_audit_context(audit_context)

    merged_details = redact_mapping(dict(details) if isinstance(details, dict) else {})
    tool_name = str(context.get("tool_name") or "").strip()
    if tool_name and "tool_name" not in merged_details:
        merged_details["tool_name"] = tool_name

    merged_client_metadata = redact_mapping(resolve_audit_client_metadata(context))
    if isinstance(client_metadata, dict):
        merged_client_metadata.update(redact_mapping(client_metadata))

    normalized_actor = str(actor or "").strip() or resolve_audit_actor(
        context, default=""
    )
    normalized_source = str(source or "").strip() or resolve_audit_source(
        context, default=""
    )
    timestamp = _utc_now_iso()
    event = {
        "event_id": f"{str(operation or 'operation').strip() or 'operation'}:{run_id or bundle_id or task_id or run_dir_path.name}:{timestamp}",
        "timestamp": timestamp,
        "operation": str(operation or "").strip(),
        "status": str(status or "unknown").strip() or "unknown",
        "run_id": str(run_id or "").strip(),
        "bundle_id": str(bundle_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "actor": normalized_actor,
        "source": normalized_source,
        "details": merged_details,
        "client_metadata": merged_client_metadata,
        "retry": dict(retry) if isinstance(retry, dict) else {},
    }
    path = audit_log_path(run_dir_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path, event


def read_audit_events(run_dir: str | Path) -> list[dict[str, Any]]:
    path = audit_log_path(run_dir)
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = line.strip()
        if not payload:
            continue
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return events


def _iter_run_dirs(outputs_root: str | Path) -> list[Path]:
    root = Path(outputs_root)
    if not root.exists():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir()], key=lambda path: path.name
    )


def _matches_event_type(event: dict[str, Any], event_type: str) -> bool:
    normalized_event_type = str(event_type or "").strip()
    if not normalized_event_type:
        return True
    operation = str(event.get("operation") or "").strip()
    if operation == normalized_event_type:
        return True
    details = event.get("details")
    if (
        isinstance(details, dict)
        and str(details.get("event_type") or "").strip() == normalized_event_type
    ):
        return True
    return False


def query_audit_events(
    outputs_root: str | Path = "outputs",
    *,
    run_id: str | None = None,
    bundle_id: str | None = None,
    task_id: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    run_key = str(run_id or "").strip()
    bundle_key = str(bundle_id or "").strip()
    task_key = str(task_id or "").strip()
    event_type_key = str(event_type or "").strip()
    status_key = str(status or "").strip()

    run_dirs = (
        [Path(outputs_root) / run_key] if run_key else _iter_run_dirs(outputs_root)
    )
    events: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        if not run_dir.exists() or not run_dir.is_dir():
            continue
        for event in read_audit_events(run_dir):
            if run_key and str(event.get("run_id") or run_dir.name) != run_key:
                continue
            if bundle_key and str(event.get("bundle_id") or "").strip() != bundle_key:
                continue
            if task_key and str(event.get("task_id") or "").strip() != task_key:
                continue
            if status_key and str(event.get("status") or "").strip() != status_key:
                continue
            if event_type_key and not _matches_event_type(event, event_type_key):
                continue
            events.append(event)
    return sorted(
        events,
        key=lambda item: (
            str(item.get("timestamp") or ""),
            str(item.get("event_id") or ""),
        ),
    )
