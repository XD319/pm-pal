"""Phase 5c Notion realtime connector: events, config, security, and sync handler."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from pm_pal.connectors.notion import NotionConnector
from pm_pal.connectors.notion_sync import register_notion_sync_handler
from pm_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType
from pm_pal.connectors.sync import (
    ConnectorSyncStore,
    enqueue_sync_task,
    is_event_processed,
    mark_event_processed,
    run_sync_task,
)
from pm_pal.integrations.notion.config_routes import register_notion_connector_config_routes
from pm_pal.integrations.notion.config_store import (
    NotionConfigStore,
    NotionConnectorSecrets,
    NotionPageMapping,
    normalize_notion_page_id,
)
from pm_pal.integrations.notion.events import handle_notion_event_payload
from pm_pal.integrations.notion.router import create_notion_router
from pm_pal.integrations.notion.security import build_notion_signature

NOTION_PAGE_ID = "0123456789abcdef0123456789abcdef"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(kind: str) -> str:
    return f"{kind}_{uuid.uuid4().hex[:12]}"


class _ProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    model_preset_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    is_prd INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    parent_source_id TEXT,
    checksum TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS project_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    source_id TEXT,
    created_at TEXT NOT NULL
);
"""
            )
            connection.commit()

    def rows(self, query: str, params: tuple = ()):
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def execute(self, query: str, params: tuple = ()) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(query, params)
            connection.commit()


@pytest.fixture()
def notion_realtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARRDP_NOTION_SIGNATURE_DISABLED", "true")
    monkeypatch.setenv("MARRDP_NOTION_TOKEN", "env-notion-token")
    db_path = tmp_path / "project_space.sqlite3"
    project_store = _ProjectStore(db_path)
    project_store.initialize()
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = NotionConfigStore(db_path)
    config_store.initialize()

    stamp = _utc_now()
    project_id = _new_id("project")
    project_store.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, "Notion Sync", "", None, stamp, stamp),
    )

    def get_project(pid: str) -> dict:
        rows = project_store.rows("SELECT * FROM projects WHERE id=?", (pid,))
        if not rows:
            raise HTTPException(status_code=404, detail="Project not found")
        return rows[0]

    router = APIRouter(prefix="/api")
    register_notion_connector_config_routes(
        router,
        config_store=config_store,
        get_project=get_project,
        now=_utc_now,
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(
        create_notion_router(
            sync_store=sync_store,
            config_store=config_store,
            new_id=_new_id,
            now=_utc_now,
        )
    )
    client = TestClient(app)
    return client, project_id, sync_store, config_store, project_store, db_path


def test_notion_events_verification_handshake(notion_realtime_env):
    client, _, _, _, _, _ = notion_realtime_env
    response = client.post(
        "/api/notion/events",
        json={"verification_token": "secret_verify_token"},
    )
    assert response.status_code == 200
    assert response.json()["verification_token"] == "secret_verify_token"
    assert response.json()["kind"] == "verification"


def test_notion_events_reject_invalid_signature_when_enabled(monkeypatch, notion_realtime_env):
    client, _, _, _, _, _ = notion_realtime_env
    monkeypatch.setenv("MARRDP_NOTION_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_NOTION_SIGNING_SECRET", "test-secret")
    body = json.dumps(
        {
            "id": "evt-1",
            "type": "page.content_updated",
            "entity": {"id": NOTION_PAGE_ID, "type": "page"},
        }
    ).encode("utf-8")
    response = client.post(
        "/api/notion/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Notion-Signature": "sha256=invalid",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_notion_signature"


def test_notion_events_accepts_valid_signature(monkeypatch, notion_realtime_env):
    client, project_id, _, config_store, _, _ = notion_realtime_env
    secret = "test-signing-secret"
    config_store.upsert(
        project_id,
        secrets=NotionConnectorSecrets(signing_secret=secret),
        page_mappings={
            NOTION_PAGE_ID: NotionPageMapping(title="Product PRD"),
        },
        updated_at=_utc_now(),
    )
    body = json.dumps(
        {
            "id": "evt-signed-1",
            "type": "page.content_updated",
            "entity": {"id": NOTION_PAGE_ID, "type": "page"},
        }
    ).encode("utf-8")
    signature = build_notion_signature(signing_secret=secret, body=body)
    monkeypatch.setenv("MARRDP_NOTION_SIGNATURE_DISABLED", "false")
    monkeypatch.delenv("MARRDP_NOTION_SIGNING_SECRET", raising=False)
    response = client.post(
        "/api/notion/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Notion-Signature": signature,
        },
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "sync_enqueued"


def test_notion_page_event_enqueues_sync_and_dedupes(notion_realtime_env):
    client, project_id, sync_store, config_store, _, _ = notion_realtime_env
    config_store.upsert(
        project_id,
        page_mappings={NOTION_PAGE_ID: NotionPageMapping(title="Product PRD")},
        updated_at=_utc_now(),
    )
    event_payload = {
        "id": "evt-page-1",
        "type": "page.content_updated",
        "entity": {"id": NOTION_PAGE_ID, "type": "page"},
    }
    first = client.post("/api/notion/events", json=event_payload)
    second = client.post("/api/notion/events", json=event_payload)

    assert first.status_code == 200
    assert first.json()["kind"] == "sync_enqueued"
    assert first.json()["project_id"] == project_id
    assert second.status_code == 200
    assert second.json()["kind"] == "duplicate"
    assert is_event_processed(sync_store, provider="notion", event_id="evt-page-1")
    rows = sync_store.rows("SELECT * FROM sync_tasks WHERE project_id=?", (project_id,))
    assert len(rows) == 1


def test_notion_connector_config_api_get_put(notion_realtime_env):
    client, project_id, _, _, _, _ = notion_realtime_env
    created = client.put(
        f"/api/projects/{project_id}/connectors/notion",
        json={
            "integration_token": "db-notion-token",
            "signing_secret": "db-signing-secret",
            "page_mappings": {
                NOTION_PAGE_ID: {"title": "Roadmap"},
            },
            "last_synced_at": "2026-07-28T00:00:00+00:00",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["has_integration_token"] is True
    assert body["has_signing_secret"] is True
    assert body["page_mappings"][NOTION_PAGE_ID]["title"] == "Roadmap"
    assert body["last_synced_at"] == "2026-07-28T00:00:00+00:00"

    fetched = client.get(f"/api/projects/{project_id}/connectors/notion")
    assert fetched.status_code == 200
    assert fetched.json()["has_integration_token"] is True
    assert "db-notion-token" not in json.dumps(fetched.json())


def test_normalize_notion_page_id():
    dashed = "01234567-89ab-cdef-0123-456789abcdef"
    assert normalize_notion_page_id(dashed) == NOTION_PAGE_ID
    assert normalize_notion_page_id(f"notion://page/{NOTION_PAGE_ID}") == NOTION_PAGE_ID


@dataclass(frozen=True, slots=True)
class _FakeNotionDocument:
    title: str
    content: str
    last_edited_time: str = "2026-07-28T12:00:00+00:00"


class _FakeNotionConnector(NotionConnector):
    def __init__(self, document: _FakeNotionDocument) -> None:
        super().__init__()
        self._document = document

    def get_content(self, source: str) -> SourceDocument:
        return SourceDocument(
            source_type=SourceType.notion,
            source=source,
            title=self._document.title,
            content_markdown=self._document.content,
            metadata=SourceMetadata(
                mime_type="text/markdown",
                extra={
                    "connector": "notion",
                    "last_edited_time": self._document.last_edited_time,
                },
            ),
        )


def _insert_test_source(
    store,
    *,
    project_id: str,
    title: str,
    source_type: str,
    content: str,
    source_url: str,
    metadata_extra: dict,
    new_id,
    now,
) -> dict:
    source_id = new_id("source")
    stamp = now()
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    store.execute(
        "INSERT INTO project_sources "
        "(id,project_id,title,source_type,content,source_url,is_prd,version,created_at,parent_source_id,checksum,metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            project_id,
            title,
            source_type,
            content,
            source_url,
            1,
            1,
            stamp,
            None,
            checksum,
            json.dumps(metadata_extra),
        ),
    )
    return {"id": source_id, "version": 1, "checksum": checksum, "metadata": metadata_extra}


def test_notion_sync_handler_creates_project_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARRDP_NOTION_TOKEN", "notion-token")
    db_path = tmp_path / "project_space.sqlite3"
    project_store = _ProjectStore(db_path)
    project_store.initialize()
    stamp = _utc_now()
    project_id = _new_id("project")
    project_store.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, "Sync Project", "", None, stamp, stamp),
    )
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = NotionConfigStore(db_path)
    config_store.initialize()
    config_store.upsert(
        project_id,
        secrets=NotionConnectorSecrets(integration_token="notion-token"),
        page_mappings={NOTION_PAGE_ID: NotionPageMapping(title="Synced PRD")},
        updated_at=stamp,
    )
    fake_connector = _FakeNotionConnector(
        _FakeNotionDocument(title="Synced PRD", content="# Synced content")
    )
    register_notion_sync_handler(
        project_store=project_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
        connector_factory=lambda _config: fake_connector,
        upsert_source=_insert_test_source,
    )
    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="notion",
        payload={
            "page_id": NOTION_PAGE_ID,
            "title": "Synced PRD",
            "source_url": f"notion://page/{NOTION_PAGE_ID}",
        },
        idempotency_key=f"{project_id}:notion:{NOTION_PAGE_ID}",
        new_id=_new_id,
        now=_utc_now,
    )
    result = run_sync_task(
        sync_store,
        project_store,
        task_id=task["id"],
        new_id=_new_id,
        now=_utc_now,
    )
    assert result["status"] == "completed"
    assert result["result"]["source_id"]
    sources = project_store.rows(
        "SELECT title,content,source_type FROM project_sources WHERE project_id=?",
        (project_id,),
    )
    assert len(sources) == 1
    assert sources[0]["title"] == "Synced PRD"
    assert "Synced content" in sources[0]["content"]
    assert sources[0]["source_type"] == "notion"
    updated_config = config_store.get(project_id)
    assert updated_config.last_synced_at


def test_notion_sync_handler_manual_compensation_skips_unchanged_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MARRDP_NOTION_TOKEN", "notion-token")
    db_path = tmp_path / "project_space.sqlite3"
    project_store = _ProjectStore(db_path)
    project_store.initialize()
    stamp = _utc_now()
    project_id = _new_id("project")
    project_store.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, "Sync Project", "", None, stamp, stamp),
    )
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = NotionConfigStore(db_path)
    config_store.initialize()
    config_store.upsert(
        project_id,
        secrets=NotionConnectorSecrets(integration_token="notion-token"),
        page_mappings={NOTION_PAGE_ID: NotionPageMapping(title="Synced PRD")},
        last_synced_at="2026-07-28T13:00:00+00:00",
        updated_at=stamp,
    )
    fake_connector = _FakeNotionConnector(
        _FakeNotionDocument(
            title="Synced PRD",
            content="# Old content",
            last_edited_time="2026-07-28T12:00:00+00:00",
        )
    )
    register_notion_sync_handler(
        project_store=project_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
        connector_factory=lambda _config: fake_connector,
        upsert_source=_insert_test_source,
    )
    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="notion",
        payload={"trigger": "manual"},
        idempotency_key=f"{project_id}:notion:manual",
        new_id=_new_id,
        now=_utc_now,
    )
    result = run_sync_task(
        sync_store,
        project_store,
        task_id=task["id"],
        new_id=_new_id,
        now=_utc_now,
    )
    assert result["status"] == "completed"
    assert result["result"]["count"] == 0
    assert project_store.rows("SELECT id FROM project_sources WHERE project_id=?", (project_id,)) == []


def test_handle_notion_event_payload_marks_processed_once(tmp_path: Path):
    db_path = tmp_path / "project_space.sqlite3"
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = NotionConfigStore(db_path)
    config_store.initialize()
    project_id = "project_test"
    config_store.upsert(
        project_id,
        page_mappings={NOTION_PAGE_ID: NotionPageMapping(title="Doc 9")},
        updated_at=_utc_now(),
    )
    payload = {
        "id": "evt-9",
        "type": "page.content_updated",
        "entity": {"id": NOTION_PAGE_ID, "type": "page"},
    }
    first = handle_notion_event_payload(
        payload,
        sync_store=sync_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
    )
    second = handle_notion_event_payload(
        payload,
        sync_store=sync_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
    )
    assert first["kind"] == "sync_enqueued"
    assert second["kind"] == "duplicate"
    assert (
        mark_event_processed(
            sync_store,
            provider="notion",
            event_id="evt-9",
            project_id=project_id,
            now=_utc_now,
        )
        is False
    )


def test_build_notion_signature_matches_hmac():
    secret = "verification-token"
    body = b'{"id":"evt-1","type":"page.content_updated"}'
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    assert build_notion_signature(signing_secret=secret, body=body) == expected
