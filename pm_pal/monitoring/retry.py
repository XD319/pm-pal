"""Retry metadata helpers and controlled retry entrypoints for non-blocking workflow steps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .audit import append_audit_event, read_audit_events


class RetryOperationError(ValueError):
    """Base class for controlled retry-operation errors."""


class RetryOperationNotSupportedError(RetryOperationError):
    """Raised when the target operation is not retryable."""


class RetryTargetNotFoundError(RetryOperationError):
    """Raised when the requested retry target cannot be found."""


class RetryTargetNotApplicableError(RetryOperationError):
    """Raised when the target exists but there is nothing retryable to replay."""


RETRYABLE_OPERATIONS = {
    "artifact_generation": "bundle_generation",
    "bundle_generation": "bundle_generation",
    "notification_dispatch": "notification_dispatch",
}


def build_retry_metadata(
    *,
    retryable: bool,
    attempt: int = 1,
    max_attempts: int = 3,
    strategy: str = "manual",
    backoff_seconds: int = 0,
    last_error: str = "",
    state: str = "",
) -> dict[str, Any]:
    normalized_attempt = max(1, int(attempt or 1))
    normalized_max_attempts = max(
        normalized_attempt, int(max_attempts or normalized_attempt)
    )
    normalized_last_error = str(last_error or "").strip()

    if state:
        resolved_state = str(state).strip()
    elif not retryable:
        resolved_state = "not_needed"
    elif normalized_attempt >= normalized_max_attempts:
        resolved_state = "exhausted"
    else:
        resolved_state = "available"

    if resolved_state == "available":
        recommended_action = "manual_retry"
    elif resolved_state == "blocked":
        recommended_action = "retry_after_dependency_recovery"
    elif resolved_state == "exhausted":
        recommended_action = "escalate"
    else:
        recommended_action = "none"

    return {
        "retryable": bool(retryable),
        "state": resolved_state,
        "attempt": normalized_attempt,
        "max_attempts": normalized_max_attempts,
        "strategy": str(strategy or "manual").strip() or "manual",
        "backoff_seconds": max(0, int(backoff_seconds or 0)),
        "last_error": normalized_last_error,
        "recommended_action": recommended_action,
    }


def retry_metadata_for_status(
    *,
    status: str,
    non_blocking: bool,
    error_message: str = "",
    attempt: int = 1,
    max_attempts: int = 3,
    strategy: str = "manual",
) -> dict[str, Any]:
    normalized_status = str(status or "unknown").strip().lower()
    normalized_error = str(error_message or "").strip()
    success_statuses = {"ok", "success", "completed"}

    if normalized_status in success_statuses:
        return build_retry_metadata(
            retryable=False,
            attempt=attempt,
            max_attempts=max_attempts,
            strategy=strategy,
        )

    if normalized_status == "skipped":
        return build_retry_metadata(
            retryable=bool(non_blocking and normalized_error),
            attempt=attempt,
            max_attempts=max_attempts,
            strategy=strategy,
            last_error=normalized_error,
            state="blocked" if non_blocking and normalized_error else "not_needed",
        )

    return build_retry_metadata(
        retryable=bool(non_blocking),
        attempt=attempt,
        max_attempts=max_attempts,
        strategy=strategy,
        last_error=normalized_error,
    )


def normalize_retry_operation(operation: str) -> str:
    normalized = str(operation or "").strip()
    resolved = RETRYABLE_OPERATIONS.get(normalized)
    if resolved:
        return resolved
    allowed = ", ".join(sorted(RETRYABLE_OPERATIONS))
    raise RetryOperationNotSupportedError(f"operation must be one of: {allowed}")


def _resolve_run_dir(run_id: str, outputs_root: str | Path) -> Path:
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run_id is required")
    run_dir = Path(outputs_root) / normalized_run_id
    if not run_dir.exists() or not run_dir.is_dir():
        raise RetryTargetNotFoundError(f"run_id not found: {normalized_run_id}")
    return run_dir


def _operation_error_message(event: dict[str, Any]) -> str:
    retry_meta = event.get("retry")
    if isinstance(retry_meta, dict):
        last_error = str(retry_meta.get("last_error") or "").strip()
        if last_error:
            return last_error
    details = event.get("details")
    if isinstance(details, dict):
        return str(details.get("error_message") or "").strip()
    return ""


def _latest_operation_event(run_dir: Path, operation: str) -> dict[str, Any] | None:
    candidates = [
        event
        for event in read_audit_events(run_dir)
        if str(event.get("operation") or "") == operation
    ]
    if not candidates:
        return None
    return candidates[-1]


def _retry_bundle_generation(
    run_id: str, run_dir: Path, audit_context: dict[str, Any]
) -> dict[str, Any]:
    from pm_pal.service.review_service import (
        _load_json_object,
        build_delivery_handoff_outputs,
    )

    report_payload = _load_json_object(run_dir / "report.json")
    if not report_payload:
        raise RetryTargetNotFoundError(f"report.json not found for run_id={run_id}")

    previous_event = _latest_operation_event(run_dir, "bundle_generation") or {}
    previous_status = str(previous_event.get("status") or "unknown")
    previous_error = _operation_error_message(previous_event)

    run_output = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "result": report_payload,
        "report_paths": {
            "report_md": str(run_dir / "report.md"),
            "report_json": str(run_dir / "report.json"),
            "run_trace": str(run_dir / "run_trace.json"),
            "implementation_pack": str(run_dir / "implementation_pack.json"),
            "test_pack": str(run_dir / "test_pack.json"),
            "execution_pack": str(run_dir / "execution_pack.json"),
            "delivery_bundle": str(run_dir / "delivery_bundle.json"),
        },
    }
    artifact_paths = build_delivery_handoff_outputs(
        run_output, audit_context=audit_context
    )

    next_event = _latest_operation_event(run_dir, "bundle_generation") or {}
    return {
        "operation": "bundle_generation",
        "before_status": previous_status,
        "after_status": str(next_event.get("status") or "unknown"),
        "before_error": previous_error,
        "after_error": _operation_error_message(next_event),
        "artifacts": artifact_paths,
        "retryable": True,
    }


def _latest_failed_notification_records(run_dir: Path) -> list[dict[str, Any]]:
    from pm_pal.notifications import read_notification_records

    failed_records = [
        record
        for record in read_notification_records(run_dir)
        if str(record.get("dispatch_status") or "") == "failed"
    ]
    if not failed_records:
        return []

    latest_by_notification_id: dict[str, dict[str, Any]] = {}
    for record in failed_records:
        latest_by_notification_id[str(record.get("notification_id") or "")] = record
    return list(latest_by_notification_id.values())


def _retry_notification_dispatch(
    run_id: str, run_dir: Path, audit_context: dict[str, Any]
) -> dict[str, Any]:
    from pm_pal.notifications import dispatch_notification

    failed_records = _latest_failed_notification_records(run_dir)
    if not failed_records:
        raise RetryTargetNotApplicableError(
            f"no failed notification dispatch records found for run_id={run_id}"
        )

    previous_status = "failed"
    previous_error = "; ".join(
        sorted(
            {
                str(record.get("error_message") or "").strip()
                for record in failed_records
                if str(record.get("error_message") or "").strip()
            }
        )
    )

    dispatch_results = []
    for record in failed_records:
        dispatch_results.append(
            dispatch_notification(
                run_dir,
                notification_type=str(record.get("event_type") or "").strip(),
                title=str(record.get("title") or "").strip(),
                summary=str(record.get("summary") or "").strip(),
                run_id=str(record.get("run_id") or run_id),
                bundle_id=str(record.get("bundle_id") or "").strip(),
                task_id=str(record.get("task_id") or "").strip(),
                metadata=record.get("metadata")
                if isinstance(record.get("metadata"), dict)
                else {},
                audit_context=audit_context,
            )
        )

    after_statuses = [result.status for result in dispatch_results]
    if after_statuses and all(status == "failed" for status in after_statuses):
        after_status = "failed"
    elif any(status == "failed" for status in after_statuses):
        after_status = "partial_success"
    else:
        after_status = "dispatched"

    after_error = "; ".join(
        sorted(
            {
                dispatch.error_message
                for result in dispatch_results
                for dispatch in result.dispatches
                if str(dispatch.error_message or "").strip()
            }
        )
    )
    return {
        "operation": "notification_dispatch",
        "before_status": previous_status,
        "after_status": after_status,
        "before_error": previous_error,
        "after_error": after_error,
        "notifications_retried": len(dispatch_results),
        "retryable": True,
    }


def retry_operation(
    run_id: str,
    operation: str,
    *,
    outputs_root: str | Path = "outputs",
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_operation = normalize_retry_operation(operation)
    run_dir = _resolve_run_dir(run_id, outputs_root)
    resolved_audit_context = (
        dict(audit_context) if isinstance(audit_context, dict) else {}
    )

    try:
        if normalized_operation == "bundle_generation":
            result = _retry_bundle_generation(run_id, run_dir, resolved_audit_context)
        elif normalized_operation == "notification_dispatch":
            result = _retry_notification_dispatch(
                run_id, run_dir, resolved_audit_context
            )
        else:  # pragma: no cover - guarded by normalize_retry_operation
            raise RetryOperationNotSupportedError(
                f"unsupported retry operation: {normalized_operation}"
            )
    except Exception as exc:
        append_audit_event(
            run_dir,
            operation="retry_operation",
            status="failed",
            run_id=run_id,
            audit_context=resolved_audit_context,
            details={
                "target_operation": normalized_operation,
                "before_status": "unknown",
                "after_status": "failed",
                "error_message": str(exc),
            },
        )
        raise

    append_audit_event(
        run_dir,
        operation="retry_operation",
        status=str(result.get("after_status") or "unknown"),
        run_id=run_id,
        audit_context=resolved_audit_context,
        details={
            "target_operation": normalized_operation,
            "before_status": str(result.get("before_status") or "unknown"),
            "after_status": str(result.get("after_status") or "unknown"),
            "before_error": str(result.get("before_error") or "").strip(),
            "after_error": str(result.get("after_error") or "").strip(),
        },
        retry=build_retry_metadata(retryable=False, state="not_needed"),
    )
    result["run_id"] = run_id
    result["requested_operation"] = str(operation or "").strip()
    return result
