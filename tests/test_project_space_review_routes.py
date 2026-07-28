"""Project-scoped review route membership and wiring."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.server.project_space import create_project_space_router


@pytest.fixture()
def project_client(tmp_path: Path):
    calls: dict[str, list] = {}

    async def enqueue_review(**kwargs):
        return {"run_id": "20260409T120001Z", "status": "queued"}

    async def get_run_status(run_id: str):
        calls.setdefault("status", []).append(run_id)
        return {"run_id": run_id, "status": "completed"}

    async def get_run_result(run_id: str):
        calls.setdefault("result", []).append(run_id)
        return {"run_id": run_id, "findings": []}

    async def stream_progress(run_id: str):
        from fastapi.responses import StreamingResponse

        calls.setdefault("stream", []).append(run_id)

        async def _gen():
            yield b"data: {}\n\n"

        return StreamingResponse(_gen(), media_type="text/event-stream")

    async def submit_clarification(run_id: str, payload):
        calls.setdefault("clarification", []).append((run_id, payload))
        return {"run_id": run_id, "ok": True}

    async def update_revision_stage(run_id: str, payload):
        return {"run_id": run_id, "revision_stage": {"decision": payload.decision}}

    async def submit_revision_input(run_id: str, payload):
        return {"run_id": run_id, "ok": True}

    async def generate_revision(run_id: str):
        return {"run_id": run_id, "ok": True}

    async def confirm_revision(run_id: str, payload):
        return {"run_id": run_id, "action": payload.action}

    async def generate_roadmap(run_id: str):
        return {"run_id": run_id, "roadmap": {}}

    async def get_artifact_preview(run_id: str, artifact_key: str):
        return {"run_id": run_id, "artifact_key": artifact_key, "preview": "x"}

    async def get_report(run_id: str, format: str):
        from fastapi.responses import Response

        return Response(content=f"report-{format}", media_type="text/plain")

    app = FastAPI()
    app.include_router(
        create_project_space_router(
            db_path=tmp_path / "project_space.sqlite3",
            enqueue_review=enqueue_review,
            get_run_status=get_run_status,
            get_run_result=get_run_result,
            stream_progress=stream_progress,
            submit_clarification=submit_clarification,
            update_revision_stage=update_revision_stage,
            submit_revision_input=submit_revision_input,
            generate_revision=generate_revision,
            confirm_revision=confirm_revision,
            generate_roadmap=generate_roadmap,
            get_artifact_preview=get_artifact_preview,
            get_report=get_report,
        )
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Demo", "description": ""}).json()
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"title": "PRD", "content": "# Hello", "is_prd": True},
    ).json()
    review = client.post(
        f"/api/projects/{project['id']}/reviews",
        json={"source_id": source["id"]},
    ).json()
    return client, project["id"], review["run_id"], calls


def test_project_review_ops_require_membership(project_client):
    client, project_id, run_id, calls = project_client

    assert client.get(f"/api/projects/{project_id}/reviews/{run_id}").status_code == 200
    assert client.get(f"/api/projects/{project_id}/reviews/{run_id}/result").status_code == 200
    assert (
        client.get(f"/api/projects/{project_id}/reviews/{run_id}/progress/stream").status_code
        == 200
    )
    assert (
        client.post(
            f"/api/projects/{project_id}/reviews/{run_id}/clarification",
            json={"answers": []},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/projects/{project_id}/reviews/{run_id}/revision-stage",
            json={"decision": "skip_revision"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/projects/{project_id}/reviews/{run_id}/revision-input",
            json={
                "selected_review_basis": "all_review_suggestions",
                "extra_instructions": "",
                "meeting_notes_text": "",
                "meeting_notes_file_ref": None,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/projects/{project_id}/reviews/{run_id}/revision-generate"
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/projects/{project_id}/reviews/{run_id}/revision-confirm",
            json={"action": "confirm_revision", "additional_requirements": ""},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/projects/{project_id}/reviews/{run_id}/roadmap-generate"
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/projects/{project_id}/reviews/{run_id}/artifacts/report_md"
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/projects/{project_id}/reviews/{run_id}/report?format=md"
        ).status_code
        == 200
    )
    lookup = client.get(f"/api/projects/by-run/{run_id}")
    assert lookup.status_code == 200
    assert lookup.json()["project_id"] == project_id

    foreign = client.get(f"/api/projects/{project_id}/reviews/not-a-run")
    assert foreign.status_code == 404
    assert calls["status"] == [run_id]
    assert calls["result"] == [run_id]
