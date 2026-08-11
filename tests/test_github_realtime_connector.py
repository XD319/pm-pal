"""Phase 5d GitHub realtime connector: events, config, signature, and sync handler."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from pm_pal.connectors.github import GitHubConnector, GitHubHTTPResponse
from pm_pal.connectors.github_sync import register_github_sync_handler
from pm_pal.connectors.schemas import SourceDocument, SourceMetadata, SourceType
from pm_pal.connectors.sync import (
    ConnectorSyncStore,
    enqueue_sync_task,
    is_event_processed,
    mark_event_processed,
    run_sync_task,
)
from pm_pal.integrations.github.config_routes import register_github_connector_config_routes
from pm_pal.integrations.github.config_store import (
    GitHubAuthMode,
    GitHubConfigStore,
    GitHubConnectorSecrets,
    GitHubRepoMapping,
)
from pm_pal.integrations.github.events import handle_github_event_payload
from pm_pal.integrations.github.router import create_github_router
from pm_pal.integrations.github.security import build_github_signature


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
def github_realtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARRDP_GITHUB_SIGNATURE_DISABLED", "true")
    monkeypatch.setenv("GITHUB_TOKEN", "env-github-token")
    db_path = tmp_path / "project_space.sqlite3"
    project_store = _ProjectStore(db_path)
    project_store.initialize()
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = GitHubConfigStore(db_path)
    config_store.initialize()

    stamp = _utc_now()
    project_id = _new_id("project")
    project_store.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?,?)",
        (project_id, "GitHub Sync", "", None, stamp, stamp),
    )

    def get_project(pid: str) -> dict:
        rows = project_store.rows("SELECT * FROM projects WHERE id=?", (pid,))
        if not rows:
            raise HTTPException(status_code=404, detail="Project not found")
        return rows[0]

    router = APIRouter(prefix="/api")
    register_github_connector_config_routes(
        router,
        config_store=config_store,
        get_project=get_project,
        now=_utc_now,
    )
    app = FastAPI()
    app.include_router(router)
    app.include_router(
        create_github_router(
            sync_store=sync_store,
            config_store=config_store,
            new_id=_new_id,
            now=_utc_now,
        )
    )
    client = TestClient(app)
    return client, project_id, sync_store, config_store, project_store, db_path


def test_github_events_ping(github_realtime_env):
    client, _, _, _, _, _ = github_realtime_env
    response = client.post(
        "/api/github/events",
        json={"zen": "Keep it logically awesome."},
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "delivery-ping"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "ping"


def test_github_events_reject_invalid_signature_when_enabled(
    monkeypatch, github_realtime_env
):
    client, _, _, _, _, _ = github_realtime_env
    monkeypatch.setenv("MARRDP_GITHUB_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_GITHUB_WEBHOOK_SECRET", "test-secret")
    body = json.dumps({"zen": "test"}).encode("utf-8")
    response = client.post(
        "/api/github/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-invalid",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_github_signature"


def test_github_events_accepts_valid_signature(monkeypatch, github_realtime_env):
    client, _, _, _, _, _ = github_realtime_env
    secret = "test-secret"
    body = json.dumps({"zen": "valid"}).encode("utf-8")
    signature = build_github_signature(secret=secret, body=body)
    monkeypatch.setenv("MARRDP_GITHUB_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_GITHUB_WEBHOOK_SECRET", secret)
    response = client.post(
        "/api/github/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-valid",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "ping"


def test_github_push_event_enqueues_sync_and_dedupes(github_realtime_env):
    client, project_id, sync_store, config_store, _, _ = github_realtime_env
    config_store.upsert(
        project_id,
        repo_mappings={
            "acme/docs": GitHubRepoMapping(
                title="Product PRD",
                owner="acme",
                repo="docs",
                paths=("docs/**",),
                include_readme=True,
            ),
        },
        updated_at=_utc_now(),
    )
    event_payload = {
        "repository": {"full_name": "acme/docs"},
        "commits": [
            {
                "added": ["docs/prd.md"],
                "modified": [],
                "removed": [],
            }
        ],
    }
    headers = {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "delivery-push-1",
    }
    first = client.post("/api/github/events", json=event_payload, headers=headers)
    second = client.post("/api/github/events", json=event_payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["kind"] == "sync_enqueued"
    assert first.json()["project_id"] == project_id
    assert second.status_code == 200
    assert second.json()["kind"] == "duplicate"
    assert is_event_processed(sync_store, provider="github", event_id="delivery-push-1")
    rows = sync_store.rows("SELECT * FROM sync_tasks WHERE project_id=?", (project_id,))
    assert len(rows) == 1


def test_github_issue_event_enqueues_sync(github_realtime_env):
    client, project_id, sync_store, config_store, _, _ = github_realtime_env
    config_store.upsert(
        project_id,
        repo_mappings={
            "acme/product": GitHubRepoMapping(
                title="Issues",
                owner="acme",
                repo="product",
                sync_issues=True,
            ),
        },
        updated_at=_utc_now(),
    )
    response = client.post(
        "/api/github/events",
        json={
            "action": "opened",
            "repository": {"full_name": "acme/product"},
            "issue": {"number": 42, "title": "Clarify scope", "body": "Need details"},
        },
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "delivery-issue-1",
        },
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "sync_enqueued"
    rows = sync_store.rows("SELECT payload_json FROM sync_tasks WHERE project_id=?", (project_id,))
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["source_url"] == "github://acme/product/issue/42"


def test_github_connector_config_api_get_put(github_realtime_env):
    client, project_id, _, _, _, _ = github_realtime_env
    created = client.put(
        f"/api/projects/{project_id}/connectors/github",
        json={
            "auth_mode": "pat",
            "personal_access_token": "ghp_test_token",
            "webhook_secret": "whsec_test",
            "repo_mappings": {
                "acme/docs": {
                    "title": "Docs Repo",
                    "owner": "acme",
                    "repo": "docs",
                    "paths": ["docs/**", "README.md"],
                },
            },
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["auth_mode"] == "pat"
    assert body["has_personal_access_token"] is True
    assert body["has_webhook_secret"] is True
    assert body["repo_mappings"]["acme/docs"]["title"] == "Docs Repo"

    fetched = client.get(f"/api/projects/{project_id}/connectors/github")
    assert fetched.status_code == 200
    assert fetched.json()["auth_mode"] == "pat"
    assert "ghp_test_token" not in json.dumps(fetched.json())


@dataclass(frozen=True, slots=True)
class _FakeGitHubDocument:
    title: str
    content: str


class _FakeGitHubConnector(GitHubConnector):
    def __init__(self, document: _FakeGitHubDocument) -> None:
        super().__init__()
        self._document = document

    def get_content(self, source: str) -> SourceDocument:
        return SourceDocument(
            source_type=SourceType.github,
            source=source,
            title=self._document.title,
            content_markdown=self._document.content,
            metadata=SourceMetadata(mime_type="text/markdown", extra={"connector": "github"}),
        )


class _FakeGitHubHTTPClient:
    def __init__(self, responses: list[GitHubHTTPResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> GitHubHTTPResponse:
        _ = headers, params
        self.calls.append((method.upper(), path))
        if not self._responses:
            raise AssertionError(f"No mocked response for {method} {path}")
        return self._responses.pop(0)


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


def test_github_sync_handler_creates_project_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_sync_token")
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
    config_store = GitHubConfigStore(db_path)
    config_store.initialize()
    config_store.upsert(
        project_id,
        secrets=GitHubConnectorSecrets(
            auth_mode=GitHubAuthMode.pat.value,
            personal_access_token="ghp_sync_token",
        ),
        repo_mappings={
            "acme/docs": GitHubRepoMapping(title="Synced PRD", owner="acme", repo="docs"),
        },
        updated_at=stamp,
    )
    fake_connector = _FakeGitHubConnector(
        _FakeGitHubDocument(title="Synced PRD", content="# Synced GitHub content")
    )
    register_github_sync_handler(
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
        provider="github",
        payload={
            "source_url": "github://acme/docs/file/docs/prd.md",
            "title": "Synced PRD",
            "owner": "acme",
            "repo": "docs",
        },
        idempotency_key=f"{project_id}:github:docs/prd.md",
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
    assert "Synced GitHub content" in sources[0]["content"]
    assert sources[0]["source_type"] == "github"


def test_github_connector_fetches_readme_with_mock_api():
    import base64

    encoded = base64.b64encode(b"# README\n\nHello GitHub").decode("ascii")
    client = _FakeGitHubHTTPClient(
        [
            GitHubHTTPResponse(
                status_code=200,
                json_body={
                    "name": "README.md",
                    "encoding": "base64",
                    "content": encoded,
                },
                headers={},
            )
        ]
    )
    connector = GitHubConnector(http_client=client)
    document = connector.get_content("github://acme/docs/readme")
    assert document.title == "README.md"
    assert "Hello GitHub" in document.content_markdown
    assert document.source_type == SourceType.github
    assert client.calls == [("GET", "/repos/acme/docs/readme")]


def test_handle_github_event_payload_marks_processed_once(tmp_path: Path):
    db_path = tmp_path / "project_space.sqlite3"
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    config_store = GitHubConfigStore(db_path)
    config_store.initialize()
    project_id = "project_test"
    config_store.upsert(
        project_id,
        repo_mappings={
            "acme/docs": GitHubRepoMapping(
                title="Docs",
                owner="acme",
                repo="docs",
                paths=("docs/**",),
            ),
        },
        updated_at=_utc_now(),
    )
    payload = {
        "repository": {"full_name": "acme/docs"},
        "commits": [{"added": ["docs/prd.md"], "modified": [], "removed": []}],
    }
    headers = {"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-9"}
    first = handle_github_event_payload(
        payload,
        headers=headers,
        sync_store=sync_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
    )
    second = handle_github_event_payload(
        payload,
        headers=headers,
        sync_store=sync_store,
        config_store=config_store,
        new_id=_new_id,
        now=_utc_now,
    )
    assert first["kind"] == "sync_enqueued"
    assert second["kind"] == "duplicate"
    assert mark_event_processed(
        sync_store,
        provider="github",
        event_id="delivery-9",
        project_id=project_id,
        now=_utc_now,
    ) is False
