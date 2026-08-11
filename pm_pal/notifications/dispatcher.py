"""Notification dispatch lifecycle and persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from pm_pal.monitoring import (
    append_audit_event,
    normalize_audit_context,
    resolve_audit_actor,
    resolve_audit_client_metadata,
    resolve_audit_source,
)
from pm_pal.utils.time import utc_now_iso as _utc_now_iso

from .base import BaseNotifier, resolve_notifiers
from .models import (
    DispatchStatus,
    NotificationDeliveryResult,
    NotificationDispatchRecord,
    NotificationDispatchResult,
    NotificationEvent,
    NotificationType,
)

NOTIFICATIONS_FILENAME = "notifications.jsonl"


def _resolve_notification_type(
    notification_type: NotificationType | str,
) -> NotificationType:
    try:
        return NotificationType(str(notification_type or "").strip())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in NotificationType)
        raise ValueError(f"notification_type must be one of: {allowed}") from exc


def notifications_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / NOTIFICATIONS_FILENAME


def _merge_notification_metadata(
    metadata: dict[str, Any] | None,
    audit_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = normalize_audit_context(audit_context)
    merged = dict(metadata) if isinstance(metadata, dict) else {}

    actor = resolve_audit_actor(context, default="")
    source = resolve_audit_source(context, default="service")
    tool_name = str(context.get("tool_name") or "").strip()
    client_metadata = resolve_audit_client_metadata(context)

    if actor and "actor" not in merged:
        merged["actor"] = actor
    if source and "source" not in merged:
        merged["source"] = source
    if tool_name and "tool_name" not in merged:
        merged["tool_name"] = tool_name

    existing_client_metadata = merged.get("client_metadata")
    merged_client_metadata = dict(client_metadata)
    if isinstance(existing_client_metadata, dict):
        merged_client_metadata.update(existing_client_metadata)
    if merged_client_metadata:
        merged["client_metadata"] = merged_client_metadata

    return merged


def build_notification_event(
    *,
    notification_type: NotificationType | str,
    title: str,
    summary: str = "",
    run_id: str = "",
    bundle_id: str = "",
    task_id: str = "",
    metadata: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
) -> NotificationEvent:
    resolved_type = _resolve_notification_type(notification_type)
    created_at = _utc_now_iso()
    merged_metadata = _merge_notification_metadata(metadata, audit_context)
    identity_seed = run_id or bundle_id or task_id or created_at
    return NotificationEvent(
        notification_id=f"notification:{resolved_type.value}:{identity_seed}",
        event_type=resolved_type,
        run_id=str(run_id or "").strip(),
        bundle_id=str(bundle_id or "").strip(),
        task_id=str(task_id or "").strip(),
        title=str(title or "").strip(),
        summary=str(summary or title or "").strip(),
        created_at=created_at,
        metadata=merged_metadata,
    )


def build_notification_record(
    *,
    notification_type: NotificationType | str,
    title: str,
    summary: str = "",
    run_id: str = "",
    bundle_id: str = "",
    task_id: str = "",
    metadata: dict[str, Any] | None = None,
    delivery_metadata: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
    channel: str,
    payload: dict[str, Any] | None = None,
    dispatch_status: DispatchStatus | str = DispatchStatus.dispatched,
    dispatched_at: str = "",
    error_message: str = "",
) -> NotificationDispatchRecord:
    event = build_notification_event(
        notification_type=notification_type,
        title=title,
        summary=summary,
        run_id=run_id,
        bundle_id=bundle_id,
        task_id=task_id,
        metadata=metadata,
        audit_context=audit_context,
    )
    return NotificationDispatchRecord(
        notification_id=event.notification_id,
        event_type=event.event_type,
        channel=str(channel or "").strip(),
        payload=dict(payload) if isinstance(payload, dict) else {},
        dispatch_status=DispatchStatus(
            str(dispatch_status or DispatchStatus.dispatched)
        ),
        created_at=event.created_at,
        dispatched_at=str(dispatched_at or "").strip(),
        error_message=str(error_message or "").strip(),
        run_id=event.run_id,
        bundle_id=event.bundle_id,
        task_id=event.task_id,
        title=event.title,
        summary=event.summary,
        metadata=event.metadata,
        delivery_metadata=dict(delivery_metadata)
        if isinstance(delivery_metadata, dict)
        else {},
        dry_run=event.dry_run,
    )


def append_notification_record(
    run_dir: str | Path,
    record: NotificationDispatchRecord | dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    run_dir_path = Path(run_dir)
    run_dir_path.mkdir(parents=True, exist_ok=True)

    if isinstance(record, NotificationDispatchRecord):
        payload = record.model_dump(mode="python")
    elif isinstance(record, dict):
        payload = NotificationDispatchRecord.model_validate(record).model_dump(
            mode="python"
        )
    else:
        raise TypeError("record must be a NotificationDispatchRecord or dict")

    path = notifications_path(run_dir_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path, payload


def _append_dispatch_audit_event(
    run_dir: str | Path,
    *,
    record: NotificationDispatchRecord,
    audit_context: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    return append_audit_event(
        run_dir,
        operation="notification_dispatch",
        status=str(record.dispatch_status),
        run_id=record.run_id,
        bundle_id=record.bundle_id,
        task_id=record.task_id,
        audit_context=audit_context,
        details={
            "notification_id": record.notification_id,
            "event_type": str(record.event_type),
            "channel": record.channel,
            "dispatch_status": str(record.dispatch_status),
            "title": record.title,
            "summary": record.summary,
            "created_at": record.created_at,
            "dispatched_at": record.dispatched_at,
            "dry_run": record.dry_run,
            "error_message": record.error_message,
            "payload_keys": sorted(record.payload.keys()),
            "delivery_metadata_keys": sorted(record.delivery_metadata.keys()),
        },
    )


def dispatch_notification(
    run_dir: str | Path,
    *,
    notification_type: NotificationType | str,
    title: str,
    summary: str = "",
    run_id: str = "",
    bundle_id: str = "",
    task_id: str = "",
    metadata: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
    notifiers: Sequence[BaseNotifier] | None = None,
) -> NotificationDispatchResult:
    event = build_notification_event(
        notification_type=notification_type,
        title=title,
        summary=summary,
        run_id=run_id,
        bundle_id=bundle_id,
        task_id=task_id,
        metadata=metadata,
        audit_context=audit_context,
    )
    resolved_notifiers = resolve_notifiers(notifiers)
    dispatches: list[NotificationDispatchRecord] = []

    for notifier in resolved_notifiers:
        dispatched_at = _utc_now_iso()
        payload: dict[str, Any] = {}
        try:
            payload = notifier.build_payload(event)
            delivery_result = notifier.send_payload(event, payload)
            if isinstance(delivery_result, NotificationDeliveryResult):
                recorded_payload = (
                    dict(delivery_result.payload)
                    if isinstance(delivery_result.payload, dict)
                    else dict(payload)
                )
                record_dry_run = delivery_result.dry_run
                delivery_metadata = (
                    dict(delivery_result.delivery_metadata)
                    if isinstance(delivery_result.delivery_metadata, dict)
                    else {}
                )
            else:
                recorded_payload = dict(payload)
                record_dry_run = event.dry_run
                delivery_metadata = {}
            record = NotificationDispatchRecord(
                notification_id=event.notification_id,
                event_type=event.event_type,
                channel=str(notifier.channel or "").strip(),
                payload=recorded_payload,
                dispatch_status=DispatchStatus.dispatched,
                created_at=event.created_at,
                dispatched_at=dispatched_at,
                run_id=event.run_id,
                bundle_id=event.bundle_id,
                task_id=event.task_id,
                title=event.title,
                summary=event.summary,
                metadata=event.metadata,
                delivery_metadata=delivery_metadata,
                dry_run=record_dry_run,
            )
        except Exception as exc:
            record = NotificationDispatchRecord(
                notification_id=event.notification_id,
                event_type=event.event_type,
                channel=str(notifier.channel or "").strip()
                or notifier.__class__.__name__,
                payload=payload if isinstance(payload, dict) else {},
                dispatch_status=DispatchStatus.failed,
                created_at=event.created_at,
                dispatched_at=dispatched_at,
                error_message=str(exc),
                run_id=event.run_id,
                bundle_id=event.bundle_id,
                task_id=event.task_id,
                title=event.title,
                summary=event.summary,
                metadata=event.metadata,
                delivery_metadata={},
                dry_run=event.dry_run,
            )

        append_notification_record(run_dir, record)
        _append_dispatch_audit_event(
            run_dir, record=record, audit_context=audit_context
        )
        dispatches.append(record)

    if dispatches and all(
        record.dispatch_status == DispatchStatus.failed for record in dispatches
    ):
        status = "failed"
    elif dispatches and any(
        record.dispatch_status == DispatchStatus.failed for record in dispatches
    ):
        status = "partial_success"
    else:
        status = DispatchStatus.dispatched.value

    latest_dispatched_at = dispatches[-1].dispatched_at if dispatches else ""
    return NotificationDispatchResult(
        notification_id=event.notification_id,
        event_type=event.event_type,
        created_at=event.created_at,
        dispatched_at=latest_dispatched_at,
        status=status,
        dispatches=dispatches,
    )


def record_dry_run_notification(
    run_dir: str | Path,
    *,
    notification_type: NotificationType | str,
    title: str,
    summary: str = "",
    run_id: str = "",
    bundle_id: str = "",
    task_id: str = "",
    metadata: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
    notifiers: Sequence[BaseNotifier] | None = None,
) -> NotificationDispatchResult:
    return dispatch_notification(
        run_dir,
        notification_type=notification_type,
        title=title,
        summary=summary,
        run_id=run_id,
        bundle_id=bundle_id,
        task_id=task_id,
        metadata=metadata,
        audit_context=audit_context,
        notifiers=notifiers,
    )


def read_notification_records(run_dir: str | Path) -> list[dict[str, Any]]:
    path = notifications_path(run_dir)
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = line.strip()
        if not payload:
            continue
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            records.append(loaded)
    return records
