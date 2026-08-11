from __future__ import annotations

import asyncio
import sqlite3

from fastapi.testclient import TestClient

from pm_pal.server import app as app_module
from pm_pal.workspace import (
    ArtifactVersion,
    ArtifactVersionStatus,
    WorkspaceRepository,
    WorkspaceState,
    WorkspaceStateStatus,
)


def _build_client() -> TestClient:
    return TestClient(app_module.app)


def _seed_workspace(
    db_path,
    *,
    workspace_id: str = "ws-1",
    artifact_key: str = "prd_doc",
    version_id: str = "artifact-v1",
    tenant_key: str = "tenant-a",
    open_id: str = "ou_owner",
    content_path: str = "",
) -> None:
    async def _write() -> None:
        repository = WorkspaceRepository(db_path)
        await repository.initialize()
        workspace = WorkspaceState(
            workspace_id=workspace_id,
            name="Workspace 1",
            status=WorkspaceStateStatus.active,
            source_run_id="seed-run-1",
            current_run_id="seed-run-1",
            created_at="2026-04-13T08:00:00+00:00",
            updated_at="2026-04-13T08:00:00+00:00",
            versions=[
                ArtifactVersion(
                    version_id=version_id,
                    workspace_id=workspace_id,
                    artifact_key=artifact_key,
                    artifact_type="prd_markdown",
                    status=ArtifactVersionStatus.active,
                    version_number=1,
                    title="Payments PRD",
                    source_run_id="seed-run-1",
                    created_at="2026-04-13T08:00:00+00:00",
                    updated_at="2026-04-13T08:00:00+00:00",
                    content_path=content_path,
                )
            ],
            current_version_ids={artifact_key: version_id},
            metadata={"tenant_key": tenant_key, "submitter_open_id": open_id},
        )
        await repository.upsert_workspace(workspace)

    asyncio.run(_write())


def test_feishu_workspace_list_returns_workspace_context(tmp_path, monkeypatch):
    workspace_db = tmp_path / "workspace.sqlite3"
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir(parents=True)
    _seed_workspace(workspace_db)
    monkeypatch.setattr(app_module, "WORKSPACE_DB_PATH", workspace_db)
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", outputs_root)

    with sqlite3.connect(workspace_db) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_review_runs (
                run_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                artifact_version_id TEXT NOT NULL,
                review_result_version_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO artifact_review_runs (
                run_id, workspace_id, artifact_version_id, review_result_version_id, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-123",
                "ws-1",
                "artifact-v1",
                "review-v1",
                "2026-04-13T08:00:00+00:00",
                "2026-04-13T08:00:00+00:00",
                "{}",
            ),
        )
        connection.commit()

    client = _build_client()
    response = client.get("/api/feishu/workspaces?open_id=ou_owner&tenant_key=tenant-a")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["workspaces"][0]["workspace_id"] == "ws-1"
    assert payload["workspaces"][0]["recent_reviews"][0]["run_id"] == "run-123"
    assert (
        payload["workspaces"][0]["recent_reviews"][0]["result_url"]
        == "/run/run-123?open_id=ou_owner&tenant_key=tenant-a&trigger_source=feishu&embed=feishu"
    )


def test_feishu_workspace_review_uses_artifact_version_content(tmp_path, monkeypatch):
    workspace_db = tmp_path / "workspace.sqlite3"
    outputs_root = tmp_path / "outputs"
    outputs_root.mkdir(parents=True)
    content_path = tmp_path / "artifact.md"
    content_path.write_text("# PRD\n\nTest content", encoding="utf-8")
    _seed_workspace(workspace_db, content_path=str(content_path))
    monkeypatch.setattr(app_module, "WORKSPACE_DB_PATH", workspace_db)
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", outputs_root)
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    app_module._jobs.clear()

    client = _build_client()
    response = client.post(
        "/api/feishu/workspaces/ws-1/artifacts/prd_doc/versions/artifact-v1/review?open_id=ou_owner&tenant_key=tenant-a",
        json={},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == "ws-1"
    assert payload["artifact_key"] == "prd_doc"
    assert payload["version_id"] == "artifact-v1"
    assert payload["run_id"]
    assert payload["result_page"]["url"].endswith("&embed=feishu")
    assert "open_id=ou_owner" in payload["result_page"]["url"]
    assert "tenant_key=tenant-a" in payload["result_page"]["url"]
