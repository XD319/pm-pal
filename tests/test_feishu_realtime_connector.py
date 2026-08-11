"""Phase 5b Feishu realtime connector: events, config, decrypt, and sync handler."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from pm_pal.connectors.feishu import FeishuConnector
from pm_pal.connectors.feishu_sync import register_feishu_sync_handler
from pm_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType
from pm_pal.connectors.sync import (
    ConnectorSyncStore,
    enqueue_sync_task,
    is_event_processed,
    mark_event_processed,
    run_sync_task,
)
from pm_pal.integrations.feishu.config_routes import (
    register_feishu_connector_config_routes,
)
from pm_pal.integrations.feishu.config_store import (
    FeishuConfigStore,
    FeishuConnectorSecrets,
    FeishuDocMapping,
)
from pm_pal.integrations.feishu.crypto import (
    decrypt_feishu_event_payload,
    encrypt_feishu_event_string,
)
from pm_pal.integrations.feishu.events import handle_feishu_event_payload
from pm_pal.integrations.feishu.router import create_feishu_router
from pm_pal.integrations.feishu.security import (
    build_feishu_encrypt_signature,
    build_feishu_signature,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
def feishu_realtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "env-app-id")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "env-app-secret")
    db_path = tmp_path / "project_space.sqlite3"
    project_store = _ProjectStore(db_path)
    project_store.initialize()
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = FeishuConfigStore(db_path)
    config_store.initialize()

    stamp = _utc_now()
    project_id = _new_id("project")
    project_store.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, "Feishu Sync", "", None, stamp, stamp),
    )

    def get_project(pid: str) -> dict:
        rows = project_store.rows("SELECT * FROM projects WHERE id=?", (pid,))
        if not rows:
            raise HTTPException(status_code=404, detail="Project not found")
        return rows[0]

    async def _noop_review(**kwargs):
        return {"run_id": "20260728T000001Z", "status": "queued"}

    router = APIRouter(prefix="/api")
    register_feishu_connector_config_routes(
        router,
        config_store=config_store,
        get_project=get_project,
        now=_utc_now,
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(
        create_feishu_router(
            submit_review_run=_noop_review,
            submit_clarification=_noop_review,
            list_workspace_overviews=_noop_review,
            get_workspace_overview=_noop_review,
            list_workspace_versions=_noop_review,
            start_workspace_review=_noop_review,
            submit_workspace_clarification=_noop_review,
            derive_workspace_version=_noop_review,
            get_workspace_diff=_noop_review,
            update_workspace_roadmap=_noop_review,
            sync_store=sync_store,
            config_store=config_store,
            new_id=_new_id,
            now=_utc_now,
        )
    )
    client = TestClient(app)
    return client, project_id, sync_store, config_store, project_store, db_path


def test_feishu_events_challenge_returns_challenge(feishu_realtime_env):
    client, _, _, _, _, _ = feishu_realtime_env
    response = client.post(
        "/api/feishu/events",
        json={"type": "url_verification", "challenge": "challenge-token"},
    )
    assert response.status_code == 200
    assert response.json()["challenge"] == "challenge-token"


def test_feishu_events_reject_invalid_signature_when_enabled(
    monkeypatch, feishu_realtime_env
):
    client, _, _, _, _, _ = feishu_realtime_env
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_FEISHU_WEBHOOK_SECRET", "test-secret")
    body = json.dumps(
        {"type": "url_verification", "challenge": "challenge-token"}
    ).encode("utf-8")
    response = client.post(
        "/api/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": str(int(time.time())),
            "X-Lark-Signature": "invalid",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_feishu_signature"


def test_feishu_events_accepts_valid_webhook_signature(
    monkeypatch, feishu_realtime_env
):
    client, _, _, _, _, _ = feishu_realtime_env
    secret = "test-secret"
    timestamp = str(int(time.time()))
    body = json.dumps(
        {"type": "url_verification", "challenge": "challenge-token"}
    ).encode("utf-8")
    signature = build_feishu_signature(secret=secret, timestamp=timestamp, body=body)
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_FEISHU_WEBHOOK_SECRET", secret)
    response = client.post(
        "/api/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Signature": signature,
        },
    )
    assert response.status_code == 200
    assert response.json()["challenge"] == "challenge-token"


def test_feishu_encrypt_signature_and_decrypt_payload():
    encrypt_key = "test-encrypt-key"
    inner = {"type": "url_verification", "challenge": "encrypted-challenge"}
    encrypted = encrypt_feishu_event_string(
        encrypt_key=encrypt_key,
        plaintext=json.dumps(inner),
    )
    decoded = decrypt_feishu_event_payload(encrypt_key=encrypt_key, encrypted=encrypted)
    assert decoded == inner


def test_feishu_events_accepts_encrypt_key_signature(monkeypatch, feishu_realtime_env):
    client, _, _, config_store, _, _ = feishu_realtime_env
    encrypt_key = "encrypt-key-123"
    config_store.upsert(
        "",
        secrets=FeishuConnectorSecrets(encrypt_key=encrypt_key),
        updated_at=_utc_now(),
    )
    inner = {"type": "url_verification", "challenge": "encrypted-challenge"}
    encrypted_body = {
        "encrypt": encrypt_feishu_event_string(
            encrypt_key=encrypt_key, plaintext=json.dumps(inner)
        )
    }
    body = json.dumps(encrypted_body).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = "nonce-abc"
    signature = build_feishu_encrypt_signature(
        encrypt_key=encrypt_key,
        timestamp=timestamp,
        nonce=nonce,
        body=body,
    )
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "false")
    monkeypatch.delenv("MARRDP_FEISHU_WEBHOOK_SECRET", raising=False)
    response = client.post(
        "/api/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": nonce,
            "X-Lark-Signature": signature,
        },
    )
    assert response.status_code == 200
    assert response.json()["challenge"] == "encrypted-challenge"


def test_feishu_document_event_enqueues_sync_and_dedupes(feishu_realtime_env):
    client, project_id, sync_store, config_store, _, _ = feishu_realtime_env
    config_store.upsert(
        project_id,
        doc_mappings={
            "doc-token-1": FeishuDocMapping(title="Product PRD", document_kind="docx"),
        },
        updated_at=_utc_now(),
    )
    event_payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt-doc-1",
            "event_type": "drive.file.edit_v1",
        },
        "event": {"file_token": "doc-token-1", "file_type": "docx"},
    }
    first = client.post("/api/feishu/events", json=event_payload)
    second = client.post("/api/feishu/events", json=event_payload)

    assert first.status_code == 200
    assert first.json()["kind"] == "sync_enqueued"
    assert first.json()["project_id"] == project_id
    assert second.status_code == 200
    assert second.json()["kind"] == "duplicate"
    assert is_event_processed(sync_store, provider="feishu", event_id="evt-doc-1")
    rows = sync_store.rows("SELECT * FROM sync_tasks WHERE project_id=?", (project_id,))
    assert len(rows) == 1


def test_feishu_connector_config_api_get_put(feishu_realtime_env):
    client, project_id, _, _, _, _ = feishu_realtime_env
    created = client.put(
        f"/api/projects/{project_id}/connectors/feishu",
        json={
            "app_id": "db-app-id",
            "app_secret": "db-app-secret",
            "encrypt_key": "db-encrypt",
            "verification_token": "verify-token",
            "webhook_secret": "webhook-secret",
            "doc_mappings": {
                "doc-abc": {"title": "Roadmap", "document_kind": "docx"},
            },
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["app_id"] == "db-app-id"
    assert body["has_app_secret"] is True
    assert body["has_encrypt_key"] is True
    assert body["doc_mappings"]["doc-abc"]["title"] == "Roadmap"

    fetched = client.get(f"/api/projects/{project_id}/connectors/feishu")
    assert fetched.status_code == 200
    assert fetched.json()["app_id"] == "db-app-id"
    assert "db-app-secret" not in json.dumps(fetched.json())


@dataclass(frozen=True, slots=True)
class _FakeFeishuDocument:
    title: str
    content: str


class _FakeFeishuConnector(FeishuConnector):
    def __init__(self, document: _FakeFeishuDocument) -> None:
        super().__init__()
        self._document = document

    def get_content(self, source: str) -> SourceDocument:
        return SourceDocument(
            source_type=SourceType.feishu,
            source=source,
            title=self._document.title,
            content_markdown=self._document.content,
            metadata=SourceMetadata(
                mime_type="text/markdown", extra={"connector": "feishu"}
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
    import hashlib
    import json

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
    return {
        "id": source_id,
        "version": 1,
        "checksum": checksum,
        "metadata": metadata_extra,
    }


def test_feishu_sync_handler_creates_project_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "app-secret")
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
    config_store = FeishuConfigStore(db_path)
    config_store.initialize()
    config_store.upsert(
        project_id,
        app_id="app-id",
        secrets=FeishuConnectorSecrets(app_secret="app-secret"),
        doc_mappings={
            "doc-sync-1": FeishuDocMapping(title="Synced PRD", document_kind="docx")
        },
        updated_at=stamp,
    )
    fake_connector = _FakeFeishuConnector(
        _FakeFeishuDocument(title="Synced PRD", content="# Synced content")
    )
    register_feishu_sync_handler(
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
        provider="feishu",
        payload={
            "doc_token": "doc-sync-1",
            "document_kind": "docx",
            "title": "Synced PRD",
            "source_url": "feishu://docx/doc-sync-1",
        },
        idempotency_key=f"{project_id}:feishu:doc-sync-1",
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
    assert sources[0]["source_type"] == "feishu"


def test_handle_feishu_event_payload_marks_processed_once(tmp_path: Path):
    db_path = tmp_path / "project_space.sqlite3"
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = FeishuConfigStore(db_path)
    config_store.initialize()
    project_id = "project_test"
    config_store.upsert(
        project_id,
        doc_mappings={"doc-9": FeishuDocMapping(title="Doc 9", document_kind="docx")},
        updated_at=_utc_now(),
    )
    payload = {
        "header": {"event_id": "evt-9", "event_type": "drive.file.edit_v1"},
        "event": {"file_token": "doc-9", "file_type": "docx"},
    }
    first = handle_feishu_event_payload(
        payload,
        sync_store=sync_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
    )
    second = handle_feishu_event_payload(
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
            provider="feishu",
            event_id="evt-9",
            project_id=project_id,
            now=_utc_now,
        )
        is False
    )
