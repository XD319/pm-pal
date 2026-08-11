from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

from pm_pal.utils.time import utc_now_iso as _utc_now_iso


class ProgressBroadcaster:
    _instance: ProgressBroadcaster | None = None

    def __new__(cls) -> ProgressBroadcaster:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._subscribers = defaultdict(set)
            cls._instance._lock = threading.Lock()
        return cls._instance

    def subscribe(self, run_id: str) -> AsyncGenerator[str, None]:
        normalized_run_id = str(run_id or "").strip()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subscribers[normalized_run_id].add(queue)

        async def _stream() -> AsyncGenerator[str, None]:
            try:
                while True:
                    payload = await queue.get()
                    yield self.encode_event(payload)
                    if payload.get("terminal") is True:
                        break
            finally:
                self.unsubscribe(normalized_run_id, queue)

        return _stream()

    def publish(self, run_id: str, event_type: str, data: dict[str, Any]) -> None:
        normalized_run_id = str(run_id or "").strip()
        payload = dict(data)
        payload.setdefault("event_type", str(event_type or "").strip() or "progress")
        payload.setdefault("timestamp", _utc_now_iso())

        with self._lock:
            subscribers = list(self._subscribers.get(normalized_run_id, set()))

        for queue in subscribers:
            queue.put_nowait(dict(payload))

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        normalized_run_id = str(run_id or "").strip()
        with self._lock:
            subscribers = self._subscribers.get(normalized_run_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(normalized_run_id, None)

    @staticmethod
    def encode_event(data: dict[str, Any]) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
