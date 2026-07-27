from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


class LocalArtifactStore:
    """Filesystem implementation; callers never couple themselves to outputs/."""
    def __init__(self, root: str | Path = "data/artifacts") -> None:
        self.root = Path(root)

    async def put_json(self, key: str, value: dict[str, Any]) -> str:
        target = self.root / f"{key}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(target)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        target = self.root / f"{key}.json"
        return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


class LocalJobQueue:
    """Idempotent in-process queue; the record is intentionally observable to callers."""
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}

    async def enqueue(self, *, key: str, kind: str, payload: dict[str, Any], handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        if key in self._jobs:
            return self._jobs[key]
        record = {"key": key, "kind": kind, "status": "running", "payload": payload, "attempts": 1}
        self._jobs[key] = record
        try:
            record.update(status="completed", result=await handler(payload))
        except Exception as exc:
            record.update(status="failed", error=str(exc))
        return record


class NullNotificationSink:
    async def notify(self, *, event: str, payload: dict[str, Any]) -> None:
        return None
