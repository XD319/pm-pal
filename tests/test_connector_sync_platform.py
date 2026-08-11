"""Connector sync platform: dedup, retry, health, and project API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from pm_pal.connectors.sync import (
    ConnectorSyncStore,
    build_sync_idempotency_key,
    enqueue_sync_task,
    is_event_processed,
    mark_event_processed,
    register_connector_sync_routes,
    run_sync_task,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(kind: str) -> str:
    return f"{kind}_{uuid.uuid4().hex[:12]}"


class _ProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        import sqlite3

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS projects "
                "(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
                "model_preset_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS project_events "
                "(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, "
                "label TEXT NOT NULL, source_id TEXT, created_at TEXT NOT NULL)"
            )
            connection.commit()

    def rows(self, query: str, params: tuple = ()):
        import sqlite3

        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def execute(self, query: str, params: tuple = ()) -> None:
        import sqlite3

        with sqlite3.connect(self.path) as connection:
            connection.execute(query, params)
            connection.commit()


@pytest.fixture()
def sync_env(tmp_path: Path):
    db_path = tmp_path / "project_space.sqlite3"
    project_store = _ProjectStore(db_path)
    project_store.initialize()
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()

    stamp = _now()
    project_id = _new_id("project")
    project_store.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, "Sync Project", "", None, stamp, stamp),
    )

    def get_project(pid: str) -> dict:
        rows = project_store.rows("SELECT * FROM projects WHERE id=?", (pid,))
        if not rows:
            raise HTTPException(status_code=404, detail="Project not found")
        return rows[0]

    app = FastAPI()
    router = APIRouter(prefix="/api")
    register_connector_sync_routes(
        router,
        sync_store=sync_store,
        get_project=get_project,
        new_id=_new_id,
        now=_now,
    )
    app.include_router(router)
    client = TestClient(app)
    return client, project_id, sync_store, project_store


def test_enqueue_sync_task_deduplicates_by_idempotency_key(sync_env):
    _, project_id, sync_store, _ = sync_env
    key = build_sync_idempotency_key(project_id, "feishu", resource="wiki/doc-1")

    first = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="feishu",
        payload={"resource": "wiki/doc-1"},
        idempotency_key=key,
        new_id=_new_id,
        now=_now,
    )
    second = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="feishu",
        payload={"resource": "wiki/doc-1"},
        idempotency_key=key,
        new_id=_new_id,
        now=_now,
    )

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert first["id"] == second["id"]
    rows = sync_store.rows("SELECT id FROM sync_tasks")
    assert len(rows) == 1


def test_mark_event_processed_dedupes_webhook_events(sync_env):
    _, project_id, sync_store, _ = sync_env

    assert is_event_processed(sync_store, provider="notion", event_id="evt-1") is False
    inserted = mark_event_processed(
        sync_store,
        provider="notion",
        event_id="evt-1",
        project_id=project_id,
        now=_now,
    )
    duplicate = mark_event_processed(
        sync_store,
        provider="notion",
        event_id="evt-1",
        project_id=project_id,
        now=_now,
    )

    assert inserted is True
    assert duplicate is False
    assert is_event_processed(sync_store, provider="notion", event_id="evt-1") is True


def test_run_sync_task_bumps_attempts_and_schedules_retry(sync_env):
    _, project_id, sync_store, project_store = sync_env
    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="github",
        payload={"repo": "acme/spec"},
        idempotency_key=build_sync_idempotency_key(project_id, "github"),
        new_id=_new_id,
        now=_now,
    )

    def failing_handler(project_id: str, provider: str, payload: dict) -> None:
        raise RuntimeError("upstream timeout")

    result = run_sync_task(
        sync_store,
        project_store,
        task_id=task["id"],
        handler=failing_handler,
        new_id=_new_id,
        now=_now,
        max_attempts=3,
    )

    assert result["status"] == "retry_scheduled"
    assert result["attempts"] == 1
    assert result["last_error"] == "upstream timeout"
    assert result["next_retry_at"]
    assert result["retry"]["backoff_seconds"] == 30

    health = sync_store.row(
        "SELECT status, last_error FROM connector_health WHERE project_id=? AND provider=?",
        (project_id, "github"),
    )
    assert health is not None
    assert health["status"] == "degraded"
    assert "upstream timeout" in health["last_error"]

    events = project_store.rows(
        "SELECT kind, label FROM project_events WHERE project_id=? AND kind=?",
        (project_id, "connector_sync"),
    )
    assert len(events) == 1
    assert "failed" in events[0]["label"]


def test_run_sync_task_success_updates_health(sync_env):
    _, project_id, sync_store, project_store = sync_env
    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="feishu",
        payload={"resource": "doc-9"},
        idempotency_key=build_sync_idempotency_key(
            project_id, "feishu", resource="doc-9"
        ),
        new_id=_new_id,
        now=_now,
    )

    def ok_handler(project_id: str, provider: str, payload: dict) -> dict:
        return {"synced": 2, "resource": payload.get("resource")}

    result = run_sync_task(
        sync_store,
        project_store,
        task_id=task["id"],
        handler=ok_handler,
        new_id=_new_id,
        now=_now,
    )

    assert result["status"] == "completed"
    assert result["result"] == {"synced": 2, "resource": "doc-9"}

    health = sync_store.row(
        "SELECT status, last_success_at, last_error FROM connector_health "
        "WHERE project_id=? AND provider=?",
        (project_id, "feishu"),
    )
    assert health is not None
    assert health["status"] == "healthy"
    assert health["last_success_at"]
    assert health["last_error"] == ""


def test_connectors_list_api_returns_health_and_recent_tasks(sync_env):
    client, project_id, sync_store, project_store = sync_env

    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="notion",
        payload={"trigger": "manual"},
        idempotency_key=build_sync_idempotency_key(project_id, "notion"),
        new_id=_new_id,
        now=_now,
    )
    run_sync_task(
        sync_store,
        project_store,
        task_id=task["id"],
        handler=lambda _pid, _provider, _payload: {"ok": True},
        new_id=_new_id,
        now=_now,
    )

    response = client.get(f"/api/projects/{project_id}/connectors")
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project_id
    assert len(body["connectors"]) == 1
    connector = body["connectors"][0]
    assert connector["provider"] == "notion"
    assert connector["status"] == "healthy"
    assert len(connector["recent_tasks"]) == 1
    assert connector["recent_tasks"][0]["status"] == "completed"


def test_manual_sync_api_enqueues_task(sync_env):
    client, project_id, sync_store, _ = sync_env

    created = client.post(
        f"/api/projects/{project_id}/connectors/sync",
        json={"provider": "feishu", "resource": "wiki/123"},
    )
    assert created.status_code == 200
    created_body = created.json()
    assert created_body["deduplicated"] is False
    assert created_body["task"]["provider"] == "feishu"
    assert created_body["task"]["payload"]["resource"] == "wiki/123"
    assert created_body["task"]["payload"]["source_url"] == "wiki/123"

    duplicate = client.post(
        f"/api/projects/{project_id}/connectors/sync",
        json={"provider": "feishu", "resource": "wiki/123"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["deduplicated"] is True

    rows = sync_store.rows(
        "SELECT id FROM sync_tasks WHERE project_id=?", (project_id,)
    )
    assert len(rows) == 1
