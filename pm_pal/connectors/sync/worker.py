"""Single-instance connector sync worker backed by SQLite task queue. :-)"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from pm_pal.connectors.sync.service import (
    SYNC_STATUS_PENDING,
    SYNC_STATUS_RETRY_SCHEDULED,
    run_sync_task,
)
from pm_pal.connectors.sync.store import ConnectorSyncStore

log = logging.getLogger("pm_pal.connectors.sync.worker")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def claim_due_tasks(
    store: ConnectorSyncStore,
    *,
    limit: int = 5,
    now: Callable[[], str] | None = None,
) -> list[dict[str, Any]]:
    """Return due pending/retry tasks ordered by creation time."""
    stamp = now() if now is not None else _utc_now().isoformat()
    pending = store.rows(
        "SELECT * FROM sync_tasks WHERE status=? ORDER BY created_at ASC LIMIT ?",
        (SYNC_STATUS_PENDING, max(1, int(limit))),
    )
    retries = store.rows(
        "SELECT * FROM sync_tasks WHERE status=? AND "
        "(next_retry_at IS NULL OR next_retry_at <= ?) "
        "ORDER BY next_retry_at ASC, created_at ASC LIMIT ?",
        (SYNC_STATUS_RETRY_SCHEDULED, stamp, max(1, int(limit))),
    )
    seen: set[str] = set()
    claimed: list[dict[str, Any]] = []
    for row in [*pending, *retries]:
        task_id = str(row["id"])
        if task_id in seen:
            continue
        seen.add(task_id)
        claimed.append(row)
        if len(claimed) >= limit:
            break
    return claimed


class ConnectorSyncWorker:
    """Poll SQLite for due sync tasks and execute them in-process."""

    def __init__(
        self,
        sync_store: ConnectorSyncStore,
        project_store: Any,
        *,
        new_id: Callable[[str], str],
        now: Callable[[], str],
        poll_interval_sec: float = 2.0,
        batch_size: int = 5,
    ) -> None:
        self.sync_store = sync_store
        self.project_store = project_store
        self.new_id = new_id
        self.now = now
        self.poll_interval_sec = max(0.5, float(poll_interval_sec))
        self.batch_size = max(1, int(batch_size))
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self.alive = False
        self.last_tick_at: str = ""
        self.last_error: str = ""
        self.processed_count = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="connector-sync-worker")
        self.alive = True
        log.info("connector sync worker started")

    async def stop(self) -> None:
        stop_event = self._stop
        if stop_event is not None:
            stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        self.alive = False
        self._stop = None
        log.info("connector sync worker stopped")

    def snapshot(self) -> dict[str, Any]:
        return {
            "alive": self.alive and self.running,
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
            "processed_count": self.processed_count,
            "poll_interval_sec": self.poll_interval_sec,
        }

    async def _loop(self) -> None:
        stop_event = self._stop
        assert stop_event is not None
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(self._tick)
                self.last_error = ""
            except Exception as exc:  # pragma: no cover - defensive
                self.last_error = str(exc)
                log.exception("connector sync worker tick failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                continue

    def _tick(self) -> None:
        self.last_tick_at = self.now()
        due = claim_due_tasks(
            self.sync_store, limit=self.batch_size, now=self.now
        )
        for row in due:
            run_sync_task(
                self.sync_store,
                self.project_store,
                task_id=str(row["id"]),
                new_id=self.new_id,
                now=self.now,
            )
            self.processed_count += 1
