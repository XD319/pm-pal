"""Asia/Shanghai daily evidence sync scheduler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from .models import SyncTrigger
from .sync_service import SHANGHAI_TZ, EvidenceSyncService

DAILY_SYNC_HOUR = 2
DAILY_SYNC_MINUTE = 0


def next_shanghai_0200(now: datetime | None = None) -> datetime:
    current = now.astimezone(SHANGHAI_TZ) if now else datetime.now(SHANGHAI_TZ)
    candidate = current.replace(
        hour=DAILY_SYNC_HOUR, minute=DAILY_SYNC_MINUTE, second=0, microsecond=0
    )
    if current >= candidate:
        candidate = candidate + timedelta(days=1)
    return candidate


class DailyEvidenceSyncScheduler:
    """Background loop that fires evidence sync at 02:00 Asia/Shanghai."""

    def __init__(
        self,
        sync_service: EvidenceSyncService,
        *,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.sync_service = sync_service
        self._sleep = sleep or asyncio.sleep
        self._clock = clock or (lambda: datetime.now(SHANGHAI_TZ))
        self._task: asyncio.Task[Any] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> asyncio.Task[Any]:
        if self._task and not self._task.done():
            return self._task
        self._stopped.clear()
        self._task = asyncio.create_task(self._run_loop(), name="evidence-daily-sync")
        return self._task

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_once(self) -> list[dict[str, Any]]:
        return await self.sync_service.sync_all_sources(
            trigger=SyncTrigger.scheduled, now=self._clock()
        )

    async def _run_loop(self) -> None:
        while not self._stopped.is_set():
            now = self._clock()
            target = next_shanghai_0200(now)
            delay = max(0.0, (target - now).total_seconds())
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
            if self._stopped.is_set():
                break
            await self.run_once()
