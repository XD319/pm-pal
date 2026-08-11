"""SQLite persistence for connector sync tasks, webhook dedup, and health."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ConnectorSyncStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
CREATE TABLE IF NOT EXISTS sync_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    next_retry_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_tasks_project_provider
    ON sync_tasks(project_id, provider, updated_at DESC);
CREATE TABLE IF NOT EXISTS processed_events (
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (provider, event_id)
);
CREATE TABLE IF NOT EXISTS connector_health (
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_success_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    lag_seconds INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, provider)
);
"""
            )
            connection.commit()

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def row(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.rows(query, params)
        return rows[0] if rows else None

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(query, params)
            connection.commit()
