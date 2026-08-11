from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pm_pal.utils.time import utc_now_iso


class LocalArtifactStore:
    """Filesystem implementation; callers never couple themselves to outputs/."""

    def __init__(self, root: str | Path = "data/artifacts") -> None:
        self.root = Path(root)

    async def put_json(self, key: str, value: dict[str, Any]) -> str:
        target = self.root / f"{key}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(target)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        target = self.root / f"{key}.json"
        return (
            json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        )


class LocalJobQueue:
    """Idempotent local job queue with optional SQLite durability and restart recovery."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.max_attempts = max(1, int(max_attempts))
        self._jobs: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {}
        if self.db_path is not None:
            self._ensure_schema()
            self._load_into_memory()

    def register_handler(
        self,
        kind: str,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        self._handlers[str(kind)] = handler

    async def initialize(self) -> bool:
        if self.db_path is not None:
            self._ensure_schema()
            self._load_into_memory()
        return True

    async def enqueue(
        self,
        *,
        key: str,
        kind: str,
        payload: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        self.register_handler(kind, handler)
        existing = self._jobs.get(key)
        if existing is not None:
            status = str(existing.get("status") or "")
            if status in {"completed", "running"}:
                return existing
            if status == "failed" and int(existing.get("attempts") or 0) >= self.max_attempts:
                return existing
            # Retryable failed / pending jobs continue below with incremented attempts.
            record = existing
            record["attempts"] = int(record.get("attempts") or 0) + 1
            record["status"] = "running"
            record["payload"] = payload
            record["kind"] = kind
            record["updated_at"] = utc_now_iso()
            record.pop("error", None)
            record.pop("result", None)
        else:
            record = {
                "key": key,
                "kind": kind,
                "status": "running",
                "payload": payload,
                "attempts": 1,
                "retry_count": 0,
                "source_error": "",
                "audit_events": [],
                "notification_events": [],
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            }
            self._jobs[key] = record
        self._persist(record)
        try:
            result = await handler(payload)
            record.update(
                status="completed",
                result=result,
                updated_at=utc_now_iso(),
                retry_count=max(0, int(record.get("attempts") or 1) - 1),
            )
            self._append_event(
                record,
                bucket="audit_events",
                event={"action": "completed", "at": utc_now_iso(), "kind": kind},
            )
        except Exception as exc:
            record.update(
                status="failed",
                error=str(exc),
                source_error=str(exc),
                updated_at=utc_now_iso(),
                retry_count=max(0, int(record.get("attempts") or 1) - 1),
            )
            self._append_event(
                record,
                bucket="audit_events",
                event={
                    "action": "failed",
                    "at": utc_now_iso(),
                    "kind": kind,
                    "error": str(exc),
                },
            )
            self._append_event(
                record,
                bucket="notification_events",
                event={
                    "event": "job_failed",
                    "at": utc_now_iso(),
                    "payload": {"key": key, "kind": kind, "error": str(exc)},
                },
            )
        self._persist(record)
        return record

    async def get(self, key: str) -> dict[str, Any] | None:
        return self._jobs.get(key)

    async def list_jobs(self, *, status: str = "") -> list[dict[str, Any]]:
        items = list(self._jobs.values())
        if status:
            items = [item for item in items if str(item.get("status")) == status]
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    async def recover(
        self,
        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Resume unfinished jobs after process restart."""
        if handlers:
            self._handlers.update(handlers)
        resumed: list[dict[str, Any]] = []
        for key, record in list(self._jobs.items()):
            status = str(record.get("status") or "")
            if status not in {"pending", "running"}:
                continue
            kind = str(record.get("kind") or "")
            handler = self._handlers.get(kind)
            if handler is None:
                record.update(
                    status="failed",
                    error=f"No handler registered for kind '{kind}' during recover",
                    source_error=f"missing_handler:{kind}",
                    updated_at=utc_now_iso(),
                )
                self._persist(record)
                resumed.append(record)
                continue
            # Mark pending before re-enqueue semantics; force a fresh running attempt.
            record["status"] = "failed"
            record["attempts"] = max(0, int(record.get("attempts") or 1) - 1)
            self._persist(record)
            resumed.append(
                await self.enqueue(
                    key=key,
                    kind=kind,
                    payload=dict(record.get("payload") or {}),
                    handler=handler,
                )
            )
        return resumed

    def _append_event(self, record: dict[str, Any], *, bucket: str, event: dict[str, Any]) -> None:
        events = list(record.get(bucket) or [])
        events.append(event)
        record[bucket] = events

    def _ensure_schema(self) -> None:
        assert self.db_path is not None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_job (
                    key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    source_error TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    audit_events_json TEXT NOT NULL DEFAULT '[]',
                    notification_events_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _load_into_memory(self) -> None:
        assert self.db_path is not None
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM platform_job").fetchall()
        for row in rows:
            result_raw = str(row["result_json"] or "")
            self._jobs[str(row["key"])] = {
                "key": str(row["key"]),
                "kind": str(row["kind"]),
                "status": str(row["status"]),
                "payload": json.loads(row["payload_json"] or "{}"),
                "result": json.loads(result_raw) if result_raw else None,
                "error": str(row["error"] or ""),
                "source_error": str(row["source_error"] or ""),
                "attempts": int(row["attempts"] or 0),
                "retry_count": int(row["retry_count"] or 0),
                "audit_events": json.loads(row["audit_events_json"] or "[]"),
                "notification_events": json.loads(row["notification_events_json"] or "[]"),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }

    def _persist(self, record: dict[str, Any]) -> None:
        if self.db_path is None:
            return
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO platform_job
                (key, kind, status, payload_json, result_json, error, source_error, attempts,
                 retry_count, audit_events_json, notification_events_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                kind=excluded.kind, status=excluded.status, payload_json=excluded.payload_json,
                result_json=excluded.result_json, error=excluded.error,
                source_error=excluded.source_error, attempts=excluded.attempts,
                retry_count=excluded.retry_count, audit_events_json=excluded.audit_events_json,
                notification_events_json=excluded.notification_events_json,
                updated_at=excluded.updated_at
                """,
                (
                    record["key"],
                    record["kind"],
                    record["status"],
                    json.dumps(record.get("payload") or {}, ensure_ascii=False),
                    json.dumps(record.get("result"), ensure_ascii=False)
                    if record.get("result") is not None
                    else "",
                    str(record.get("error") or ""),
                    str(record.get("source_error") or ""),
                    int(record.get("attempts") or 0),
                    int(record.get("retry_count") or 0),
                    json.dumps(record.get("audit_events") or [], ensure_ascii=False),
                    json.dumps(record.get("notification_events") or [], ensure_ascii=False),
                    str(record.get("created_at") or utc_now_iso()),
                    str(record.get("updated_at") or utc_now_iso()),
                ),
            )
            connection.commit()


class NullNotificationSink:
    async def notify(self, *, event: str, payload: dict[str, Any]) -> None:
        return None


class RecordingNotificationSink:
    """In-memory sink that also mirrors events into an optional artifact store."""

    def __init__(self, artifacts: LocalArtifactStore | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.artifacts = artifacts

    async def notify(self, *, event: str, payload: dict[str, Any]) -> None:
        record = {"event": event, "payload": payload, "at": utc_now_iso()}
        self.events.append(record)
        if self.artifacts is not None:
            key = f"notifications/{event}/{len(self.events)}"
            await self.artifacts.put_json(key, record)
