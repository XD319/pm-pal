from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol


class Repository(Protocol):
    async def initialize(self) -> Any: ...


class ArtifactStore(Protocol):
    async def put_json(self, key: str, value: dict[str, Any]) -> str: ...
    async def get_json(self, key: str) -> dict[str, Any] | None: ...


class JobQueue(Protocol):
    async def enqueue(self, *, key: str, kind: str, payload: dict[str, Any], handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> dict[str, Any]: ...


class NotificationSink(Protocol):
    async def notify(self, *, event: str, payload: dict[str, Any]) -> None: ...
