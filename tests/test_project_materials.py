"""Project materials upload, versioning, diff, rollback, and delete."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.server.project_space import create_project_space_router


@pytest.fixture()
def materials_client(tmp_path: Path):
    async def enqueue_review(**kwargs):
        return {"run_id": "20260409T120001Z", "status": "queued"}

    app = FastAPI()
    app.include_router(
        create_project_space_router(
            db_path=tmp_path / "project_space.sqlite3",
            enqueue_review=enqueue_review,
        )
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Materials", "description": ""}).json()
    return client, project["id"]


def test_upload_txt_and_md(materials_client):
    client, project_id = materials_client

    txt = client.post(
        f"/api/projects/{project_id}/sources/upload",
        files={"file": ("notes.txt", BytesIO(b"Hello PRD text"), "text/plain")},
    )
    assert txt.status_code == 200
    txt_body = txt.json()
    assert txt_body["version"] == 1
    assert txt_body["metadata"]["validation"]["valid"] is True

    md = client.post(
        f"/api/projects/{project_id}/sources/upload",
        files={"file": ("spec.md", BytesIO(b"# Title\n\nBody"), "text/markdown")},
        data={"title": "Spec Doc"},
    )
    assert md.status_code == 200
    assert md.json()["version"] == 1


def test_version_increment_with_same_title(materials_client):
    client, project_id = materials_client

    first = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "version one", "is_prd": True},
    ).json()
    second = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "version two", "is_prd": True},
    ).json()

    assert first["version"] == 1
    assert second["version"] == 2

    detail = client.get(f"/api/projects/{project_id}/sources/{second['id']}").json()
    assert detail["content"] == "version two"
    assert detail["parent_source_id"] == first["id"]


def test_diff_between_versions(materials_client):
    client, project_id = materials_client

    v1 = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "alpha\nbeta", "is_prd": True},
    ).json()
    v2 = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "alpha\ngamma", "is_prd": True},
    ).json()

    diff = client.get(
        f"/api/projects/{project_id}/sources/{v1['id']}/diff",
        params={"against": v2["id"]},
    ).json()
    assert "-beta" in diff["diff"]
    assert "+gamma" in diff["diff"]


def test_rollback_creates_new_version(materials_client):
    client, project_id = materials_client

    v1 = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "original content", "is_prd": True},
    ).json()
    v2 = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "changed content", "is_prd": True},
    ).json()
    rolled = client.post(f"/api/projects/{project_id}/sources/{v1['id']}/rollback").json()

    assert rolled["version"] == 3
    detail = client.get(f"/api/projects/{project_id}/sources/{rolled['id']}").json()
    assert detail["content"] == "original content"
    assert detail["metadata"]["rollback_from"] == v1["id"]


def test_delete_source(materials_client):
    client, project_id = materials_client

    created = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Temp", "content": "remove me", "is_prd": True},
    ).json()
    deleted = client.delete(f"/api/projects/{project_id}/sources/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/projects/{project_id}/sources/{created['id']}").status_code == 404


def test_timeline_includes_source_mutations(materials_client):
    client, project_id = materials_client

    created = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "Audit", "content": "track me", "is_prd": True},
    ).json()
    client.patch(
        f"/api/projects/{project_id}/sources/{created['id']}",
        json={"title": "Audit Renamed"},
    )
    timeline = client.get(f"/api/projects/{project_id}/timeline").json()["events"]
    kinds = {e["kind"] for e in timeline}
    assert "source_added" in kinds
    assert "source_updated" in kinds
