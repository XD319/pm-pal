"""Feishu run context helpers used by HTTP routes and integrations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from prd_pal.monitoring import resolve_audit_client_metadata
from prd_pal.utils.redaction import redact_mapping
from prd_pal.utils.time import utc_now_iso

RUN_ENTRY_CONTEXT_FILENAME = "entry_context.json"


def entry_context_path(run_dir: Path) -> Path:
    return run_dir / RUN_ENTRY_CONTEXT_FILENAME


def extract_result_page_context(context: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(context, dict):
        return {}

    preserved: dict[str, str] = {}
    for key, value in context.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if normalized_key.startswith("_"):
            continue
        if normalized_key in {"open_id", "tenant_key"} or normalized_key.endswith(
            "_token"
        ):
            scalar = str(value or "").strip()
            if scalar:
                preserved[normalized_key] = scalar
            continue
        if isinstance(value, (str, int, float, bool)):
            scalar = str(value).strip()
            if scalar:
                preserved[normalized_key] = scalar
    return preserved


def merge_result_page_context(*contexts: dict[str, Any] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for context in contexts:
        extracted = extract_result_page_context(context)
        for key, value in extracted.items():
            if value:
                merged[key] = value
    return merged


def is_feishu_audit_context(audit_context: dict[str, Any] | None) -> bool:
    if not isinstance(audit_context, dict):
        return False
    if str(audit_context.get("source") or "").strip().lower() == "feishu":
        return True
    client_metadata = resolve_audit_client_metadata(audit_context)
    return str(client_metadata.get("trigger_source") or "").strip().lower() == "feishu"


def build_run_entry_context(
    audit_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not is_feishu_audit_context(audit_context):
        return None

    client_metadata = resolve_audit_client_metadata(audit_context)
    submitter_open_id = str(client_metadata.get("open_id") or "").strip()
    tenant_key = str(client_metadata.get("tenant_key") or "").strip()
    actor = (
        str(audit_context.get("actor") or submitter_open_id or "feishu").strip()
        or "feishu"
    )
    return {
        "source_origin": "feishu",
        "entry_mode": "plugin",
        "submitter_open_id": submitter_open_id,
        "tenant_key": tenant_key,
        "trigger_source": str(client_metadata.get("trigger_source") or "feishu").strip()
        or "feishu",
        "result_page_context": extract_result_page_context(client_metadata),
        "submitted_by": actor,
        "tool_name": str(audit_context.get("tool_name") or "").strip(),
        "created_at": utc_now_iso(),
    }


def persist_run_entry_context(
    run_dir: Path, audit_context: dict[str, Any] | None
) -> dict[str, Any] | None:
    entry_context = build_run_entry_context(audit_context)
    if entry_context is None:
        return None
    entry_context_path(run_dir).write_text(
        json.dumps(redact_mapping(entry_context), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return entry_context


def read_run_entry_context(run_dir: Path) -> dict[str, Any]:
    path = entry_context_path(run_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_result_page_payload(
    run_id: str,
    *,
    audit_context: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, str]:
    run_id = str(run_id or "").strip()
    base_path = f"/run/{run_id}"
    normalized_audit_context = audit_context if isinstance(audit_context, dict) else {}

    client_metadata = resolve_audit_client_metadata(normalized_audit_context)
    entry_context = read_run_entry_context(run_dir) if run_dir is not None else {}
    entry_result_context = entry_context.get("result_page_context")
    if not isinstance(entry_result_context, dict):
        entry_result_context = {}

    is_feishu_entry = (
        str(entry_context.get("source_origin") or "").strip().lower() == "feishu"
        or str(normalized_audit_context.get("source") or "").strip().lower() == "feishu"
        or str(client_metadata.get("trigger_source") or "").strip().lower() == "feishu"
    )
    if not is_feishu_entry:
        return {"path": base_path, "url": base_path}

    query_params = merge_result_page_context(
        entry_result_context, client_metadata, request_context
    )
    expected_open_id = str(entry_context.get("submitter_open_id") or "").strip()
    expected_tenant_key = str(entry_context.get("tenant_key") or "").strip()
    if expected_open_id:
        query_params["open_id"] = expected_open_id
    if expected_tenant_key:
        query_params["tenant_key"] = expected_tenant_key
    query_params["embed"] = "feishu"

    open_id = str(query_params.get("open_id") or "").strip()
    tenant_key = str(query_params.get("tenant_key") or "").strip()
    if not open_id:
        query_params.pop("open_id", None)
    if not tenant_key:
        query_params.pop("tenant_key", None)

    query_string = urlencode(query_params)
    url = f"{base_path}?{query_string}" if query_string else base_path
    return {"path": url, "url": url}


def resolve_run_feishu_context(
    run_id: str, context: dict[str, Any] | None, *, outputs_root: Path
) -> dict[str, str]:
    resolved = extract_result_page_context(context)
    run_dir = outputs_root / str(run_id or "").strip()
    entry_context = read_run_entry_context(run_dir)
    if str(entry_context.get("source_origin") or "").strip().lower() != "feishu":
        return resolved

    entry_result_context = entry_context.get("result_page_context")
    if isinstance(entry_result_context, dict):
        merged = merge_result_page_context(entry_result_context, resolved)
    else:
        merged = dict(resolved)

    submitter_open_id = str(entry_context.get("submitter_open_id") or "").strip()
    tenant_key = str(entry_context.get("tenant_key") or "").strip()
    if submitter_open_id and not str(merged.get("open_id") or "").strip():
        merged["open_id"] = submitter_open_id
    if tenant_key and not str(merged.get("tenant_key") or "").strip():
        merged["tenant_key"] = tenant_key
    merged.setdefault("trigger_source", "feishu")
    return merged


def resolve_request_feishu_context(request: Any | None) -> dict[str, str]:
    if request is None:
        return {}

    resolved: dict[str, str] = {}
    candidates = (
        ("open_id", str(request.query_params.get("open_id", "") or "").strip()),
        ("tenant_key", str(request.query_params.get("tenant_key", "") or "").strip()),
        ("open_id", str(request.headers.get("x-feishu-open-id", "") or "").strip()),
        (
            "tenant_key",
            str(request.headers.get("x-feishu-tenant-key", "") or "").strip(),
        ),
    )
    for key, value in candidates:
        if value and key not in resolved:
            resolved[key] = value

    if "embed" in request.query_params:
        embed = str(request.query_params.get("embed", "") or "").strip()
        if embed:
            resolved.setdefault("embed", embed)
    if resolved:
        resolved.setdefault("trigger_source", "feishu")
    return resolved


def validate_feishu_run_access(
    *, run_dir: Path, context: dict[str, Any] | None
) -> None:
    entry_context = read_run_entry_context(run_dir)
    if str(entry_context.get("source_origin") or "").strip().lower() != "feishu":
        return

    provided_context = dict(context) if isinstance(context, dict) else {}
    provided_open_id = str(provided_context.get("open_id") or "").strip()
    provided_tenant_key = str(provided_context.get("tenant_key") or "").strip()
    expected_open_id = str(entry_context.get("submitter_open_id") or "").strip()
    expected_tenant_key = str(entry_context.get("tenant_key") or "").strip()

    if not provided_tenant_key or (expected_open_id and not provided_open_id):
        raise PermissionError(
            "This run requires the originating Feishu identity context."
        )
    if expected_tenant_key and provided_tenant_key != expected_tenant_key:
        raise PermissionError(
            "This run is not accessible from the current Feishu tenant context."
        )
    if expected_open_id and provided_open_id != expected_open_id:
        raise PermissionError("This run is not accessible to the current Feishu user.")
