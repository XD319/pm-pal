from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from pm_pal.server.job_state import (
    JobRecord,
    hydrate_job_record,
    normalize_persisted_job,
)
from pm_pal.service.report_service import RUN_ID_PATTERN


class JobRegistry:
    def __init__(self, outputs_root_getter: Callable[[], Path]) -> None:
        self._outputs_root_getter = outputs_root_getter
        self.jobs: dict[str, JobRecord] = {}
        self.lock = asyncio.Lock()

    def outputs_root(self) -> Path:
        return self._outputs_root_getter()

    async def register(self, job: JobRecord) -> None:
        async with self.lock:
            self.jobs[job.run_id] = job

    async def get(self, run_id: str) -> JobRecord | None:
        async with self.lock:
            return self.jobs.get(run_id)

    async def snapshot(self) -> dict[str, JobRecord]:
        async with self.lock:
            return dict(self.jobs)

    async def recover(self) -> None:
        outputs_root = self.outputs_root()
        if not outputs_root.exists() or not outputs_root.is_dir():
            return

        recovered: dict[str, JobRecord] = {}
        for run_dir in outputs_root.iterdir():
            if not run_dir.is_dir() or not RUN_ID_PATTERN.fullmatch(run_dir.name):
                continue
            payload = normalize_persisted_job(run_dir.name, run_dir)
            job = hydrate_job_record(run_dir, payload)
            if job is not None:
                recovered[job.run_id] = job

        async with self.lock:
            self.jobs.clear()
            self.jobs.update(recovered)
