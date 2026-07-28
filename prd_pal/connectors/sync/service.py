"""Connector sync enqueue, dedup, retry, and health updates."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from prd_pal.monitoring.retry import build_retry_metadata

from .store import ConnectorSyncStore

SyncHandler = Callable[[str, str, dict[str, Any]], Any]

DEFAULT_MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_RUNNING = "running"
SYNC_STATUS_COMPLETED = "completed"
SYNC_STATUS_FAILED = "failed"
SYNC_STATUS_RETRY_SCHEDULED = "retry_scheduled"

HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_ERROR = "error"
HEALTH_UNKNOWN = "unknown"

_handler_registry: dict[str, SyncHandler] = {}


def register_sync_handler(provider: str, handler: SyncHandler) -> None:
    _handler_registry[str(provider or "").strip().lower()] = handler


def resolve_sync_handler(provider: str) -> SyncHandler | None:
    return _handler_registry.get(str(provider or "").strip().lower())


def build_sync_idempotency_key(
    project_id: str,
    provider: str,
    *,
    resource: str = "",
    suffix: str = "",
) -> str:
    parts = [str(project_id or "").strip(), str(provider or "").strip().lower()]
    if resource.strip():
        parts.append(resource.strip())
    if suffix.strip():
        parts.append(suffix.strip())
    return ":".join(parts)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now(now_fn: Callable[[], str] | None = None) -> str:
    if now_fn is not None:
        return now_fn()
    return _utc_now().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def backoff_seconds_for_attempt(attempt: int, *, base_seconds: int = BACKOFF_BASE_SECONDS) -> int:
    normalized = max(1, int(attempt or 1))
    return int(base_seconds * (2 ** (normalized - 1)))


def public_sync_task(row: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(row.get("payload_json") or "{}")
    retry = build_retry_metadata(
        retryable=row.get("status") in {SYNC_STATUS_FAILED, SYNC_STATUS_RETRY_SCHEDULED},
        attempt=int(row.get("attempts") or 0),
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        strategy="exponential_backoff",
        backoff_seconds=backoff_seconds_for_attempt(int(row.get("attempts") or 1)),
        last_error=str(row.get("last_error") or ""),
        state="available"
        if row.get("status") == SYNC_STATUS_RETRY_SCHEDULED
        else "exhausted"
        if row.get("status") == SYNC_STATUS_FAILED
        else "not_needed"
        if row.get("status") == SYNC_STATUS_COMPLETED
        else "",
    )
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "provider": row["provider"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
        "attempts": int(row.get("attempts") or 0),
        "last_error": str(row.get("last_error") or ""),
        "payload": payload,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "next_retry_at": row.get("next_retry_at"),
        "retry": retry,
    }


def enqueue_sync_task(
    store: ConnectorSyncStore,
    *,
    project_id: str,
    provider: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    if not normalized_provider:
        raise ValueError("provider is required")
    key = idempotency_key or build_sync_idempotency_key(project_id, normalized_provider)
    existing = store.row(
        "SELECT * FROM sync_tasks WHERE idempotency_key=?",
        (key,),
    )
    if existing:
        return {**public_sync_task(existing), "deduplicated": True}

    task_id = new_id("sync")
    stamp = _iso_now(now)
    body = dict(payload or {})
    store.execute(
        "INSERT INTO sync_tasks "
        "(id, project_id, provider, idempotency_key, status, attempts, last_error, "
        "payload_json, created_at, updated_at, next_retry_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            task_id,
            project_id,
            normalized_provider,
            key,
            SYNC_STATUS_PENDING,
            0,
            "",
            json.dumps(body),
            stamp,
            stamp,
            None,
        ),
    )
    created = store.row("SELECT * FROM sync_tasks WHERE id=?", (task_id,))
    assert created is not None
    return {**public_sync_task(created), "deduplicated": False}


def mark_event_processed(
    store: ConnectorSyncStore,
    *,
    provider: str,
    event_id: str,
    project_id: str,
    now: Callable[[], str] | None = None,
) -> bool:
    normalized_provider = str(provider or "").strip().lower()
    normalized_event_id = str(event_id or "").strip()
    if not normalized_provider or not normalized_event_id:
        raise ValueError("provider and event_id are required")
    if is_event_processed(store, provider=normalized_provider, event_id=normalized_event_id):
        return False
    store.execute(
        "INSERT INTO processed_events (provider, event_id, project_id, processed_at) "
        "VALUES (?,?,?,?)",
        (normalized_provider, normalized_event_id, project_id, _iso_now(now)),
    )
    return True


def is_event_processed(
    store: ConnectorSyncStore,
    *,
    provider: str,
    event_id: str,
) -> bool:
    normalized_provider = str(provider or "").strip().lower()
    normalized_event_id = str(event_id or "").strip()
    row = store.row(
        "SELECT 1 FROM processed_events WHERE provider=? AND event_id=?",
        (normalized_provider, normalized_event_id),
    )
    return row is not None


def _update_connector_health(
    store: ConnectorSyncStore,
    *,
    project_id: str,
    provider: str,
    status: str,
    last_error: str = "",
    last_success_at: str | None = None,
    now: Callable[[], str],
) -> None:
    stamp = _iso_now(now)
    lag_seconds = 0
    if last_success_at:
        parsed = _parse_iso(last_success_at)
        if parsed is not None:
            lag_seconds = max(0, int((_utc_now() - parsed).total_seconds()))
    store.execute(
        "INSERT INTO connector_health "
        "(project_id, provider, status, last_success_at, last_error, lag_seconds, updated_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(project_id, provider) DO UPDATE SET "
        "status=excluded.status, "
        "last_success_at=COALESCE(excluded.last_success_at, connector_health.last_success_at), "
        "last_error=excluded.last_error, "
        "lag_seconds=excluded.lag_seconds, "
        "updated_at=excluded.updated_at",
        (
            project_id,
            provider,
            status,
            last_success_at,
            last_error,
            lag_seconds,
            stamp,
        ),
    )


def _record_sync_timeline_event(
    project_store: Any,
    *,
    project_id: str,
    provider: str,
    label: str,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> None:
    project_store.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?)",
        (new_id("event"), project_id, "connector_sync", label, provider, now()),
    )


def run_sync_task(
    store: ConnectorSyncStore,
    project_store: Any,
    *,
    task_id: str,
    handler: SyncHandler | None = None,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    task = store.row("SELECT * FROM sync_tasks WHERE id=?", (task_id,))
    if task is None:
        raise ValueError(f"sync task not found: {task_id}")

    project_id = str(task["project_id"])
    provider = str(task["provider"])
    attempt = int(task.get("attempts") or 0) + 1
    stamp = _iso_now(now)
    payload = json.loads(task.get("payload_json") or "{}")

    store.execute(
        "UPDATE sync_tasks SET status=?, attempts=?, updated_at=? WHERE id=?",
        (SYNC_STATUS_RUNNING, attempt, stamp, task_id),
    )

    sync_handler = handler or resolve_sync_handler(provider)
    try:
        if sync_handler is None:
            raise RuntimeError(f"No sync handler registered for provider: {provider}")
        result = sync_handler(project_id, provider, payload)
        success_stamp = _iso_now(now)
        store.execute(
            "UPDATE sync_tasks SET status=?, last_error=?, updated_at=?, next_retry_at=? "
            "WHERE id=?",
            (SYNC_STATUS_COMPLETED, "", success_stamp, None, task_id),
        )
        _update_connector_health(
            store,
            project_id=project_id,
            provider=provider,
            status=HEALTH_HEALTHY,
            last_error="",
            last_success_at=success_stamp,
            now=now,
        )
        _record_sync_timeline_event(
            project_store,
            project_id=project_id,
            provider=provider,
            label=f"{provider} sync completed",
            new_id=new_id,
            now=now,
        )
        updated = store.row("SELECT * FROM sync_tasks WHERE id=?", (task_id,))
        assert updated is not None
        body = public_sync_task(updated)
        body["result"] = result
        return body
    except Exception as exc:
        error_message = str(exc)
        if attempt >= max_attempts:
            final_status = SYNC_STATUS_FAILED
            next_retry_at = None
            health_status = HEALTH_ERROR
        else:
            final_status = SYNC_STATUS_RETRY_SCHEDULED
            delay = backoff_seconds_for_attempt(attempt)
            next_retry_at = (_utc_now() + timedelta(seconds=delay)).isoformat()
            health_status = HEALTH_DEGRADED
        failure_stamp = _iso_now(now)
        store.execute(
            "UPDATE sync_tasks SET status=?, attempts=?, last_error=?, updated_at=?, next_retry_at=? "
            "WHERE id=?",
            (final_status, attempt, error_message, failure_stamp, next_retry_at, task_id),
        )
        _update_connector_health(
            store,
            project_id=project_id,
            provider=provider,
            status=health_status,
            last_error=error_message,
            now=now,
        )
        _record_sync_timeline_event(
            project_store,
            project_id=project_id,
            provider=provider,
            label=f"{provider} sync failed: {error_message[:120]}",
            new_id=new_id,
            now=now,
        )
        updated = store.row("SELECT * FROM sync_tasks WHERE id=?", (task_id,))
        assert updated is not None
        return public_sync_task(updated)


def list_connector_summaries(
    store: ConnectorSyncStore,
    *,
    project_id: str,
    recent_task_limit: int = 5,
) -> list[dict[str, Any]]:
    health_rows = store.rows(
        "SELECT * FROM connector_health WHERE project_id=? ORDER BY provider ASC",
        (project_id,),
    )
    health_by_provider = {row["provider"]: row for row in health_rows}
    providers = sorted(
        set(health_by_provider.keys())
        | {
            row["provider"]
            for row in store.rows(
                "SELECT DISTINCT provider FROM sync_tasks WHERE project_id=?",
                (project_id,),
            )
        }
    )
    summaries: list[dict[str, Any]] = []
    for provider in providers:
        health = health_by_provider.get(provider) or {}
        recent = store.rows(
            "SELECT * FROM sync_tasks WHERE project_id=? AND provider=? "
            "ORDER BY updated_at DESC LIMIT ?",
            (project_id, provider, max(1, int(recent_task_limit))),
        )
        summaries.append(
            {
                "provider": provider,
                "status": health.get("status") or HEALTH_UNKNOWN,
                "last_success_at": health.get("last_success_at"),
                "last_error": str(health.get("last_error") or ""),
                "lag_seconds": int(health.get("lag_seconds") or 0),
                "updated_at": health.get("updated_at"),
                "recent_tasks": [public_sync_task(row) for row in recent],
            }
        )
    return summaries
