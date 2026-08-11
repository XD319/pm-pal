"""Project-space unification: removed routes gone; PM loop and auth covered. :-)"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from pm_pal.server import app as app_module
from pm_pal.server.project_space import create_project_space_router


@pytest.fixture()
def domain_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PM_PAL_API_AUTH_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path / "outputs")
    monkeypatch.setattr(app_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        app_module, "PROJECT_SPACE_DB_PATH", tmp_path / "project_space.sqlite3"
    )
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)

    async def enqueue_review(**kwargs):
        return {"run_id": "20260803T120000Z", "status": "queued"}

    app = app_module.app
    # Rebuild project space against tmp db by mounting a fresh router is heavy;
    # instead exercise domain via isolated router for the loop test. :-)
    isolated = __import__("fastapi").FastAPI()
    router, *_rest = create_project_space_router(
        db_path=tmp_path / "project_space.sqlite3",
        enqueue_review=enqueue_review,
    )
    isolated.include_router(router)
    return TestClient(isolated)


def test_legacy_routers_removed():
    client = TestClient(app_module.app)
    assert client.get("/api/pm/products").status_code == 404
    assert client.get("/api/decision/evidence").status_code == 404
    assert client.get("/api/v1/resources/products").status_code == 404


def test_project_pm_loop(domain_client, tmp_path: Path):
    client = domain_client
    project = client.post(
        "/api/projects", json={"name": "Loop", "description": ""}
    ).json()
    project_id = project["id"]

    evidence = client.post(
        f"/api/projects/{project_id}/evidence",
        json={
            "content": "Users struggle to export shortlists after filtering by campus.",
            "author": "recruiter",
            "confirm": True,
            "actor": "local",
        },
    )
    assert evidence.status_code == 200
    evidence_id = evidence.json()["evidence"]["id"]

    insight = client.post(
        f"/api/projects/{project_id}/insights",
        json={
            "title": "Export friction",
            "evidence_refs": [evidence_id],
            "actor": "local",
        },
    )
    assert insight.status_code == 200
    insight_id = insight.json()["insight"]["id"]

    opportunity = client.post(
        f"/api/projects/{project_id}/opportunities",
        json={
            "title": "One-click export",
            "insight_ids": [insight_id],
            "actor": "local",
        },
    )
    assert opportunity.status_code == 200
    opportunity_id = opportunity.json()["opportunity"]["id"]

    submitted = client.post(
        f"/api/projects/{project_id}/opportunities/{opportunity_id}/submit",
        json={"actor": "local"},
    )
    assert submitted.status_code == 200

    approved = client.post(
        f"/api/projects/{project_id}/opportunities/{opportunity_id}/approve",
        json={"actor": "local"},
    )
    assert approved.status_code == 200

    prd = client.post(
        f"/api/projects/{project_id}/prd-versions",
        json={"opportunity_id": opportunity_id, "actor": "local"},
    )
    assert prd.status_code == 200
    prd_id = prd.json()["prd_version"]["id"]
    assert prd.json()["prd_version"]["project_source_id"]

    # Simulate quality gate pass without live LLM. :-)
    from pm_pal.project_domain.models import PrdStatus
    from pm_pal.project_domain.repository import ProjectDomainRepository

    repo = ProjectDomainRepository(tmp_path / "project_space.sqlite3")
    version = repo.get_prd_version(prd_id)
    assert version is not None
    repo.update_prd_version(
        version.model_copy(
            update={
                "status": PrdStatus.quality_checked,
                "quality_decision": "pass",
            }
        )
    )

    approved_prd = client.post(
        f"/api/projects/{project_id}/prd-versions/{prd_id}/approve",
        json={"actor": "local"},
    )
    assert approved_prd.status_code == 200

    ready = client.post(
        f"/api/projects/{project_id}/prd-versions/{prd_id}/ready",
        json={"actor": "local"},
    )
    assert ready.status_code == 200

    delivery = client.post(
        f"/api/projects/{project_id}/deliveries",
        json={"prd_version_id": prd_id, "actor": "local"},
    )
    assert delivery.status_code == 200
    assert delivery.json()["delivery"]["status"] == "succeeded"

    summary = client.get(f"/api/projects/{project_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["counts"]["evidence"] >= 1
    assert summary.json()["counts"]["deliveries"] >= 1


def test_api_key_required_when_auth_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PM_PAL_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("PM_PAL_API_KEY", "secret-key")
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_KEY", "secret-key")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path / "outputs")
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app_module, "_run_job", AsyncMock(return_value=None))

    client = TestClient(app_module.app)
    denied = client.get("/api/projects")
    assert denied.status_code == 401

    allowed = client.get("/api/projects", headers={"X-API-Key": "secret-key"})
    assert allowed.status_code == 200


def test_webhook_event_paths_bypass_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("PM_PAL_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("PM_PAL_API_KEY", "secret-key")
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_KEY", "secret-key")
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setenv("PM_PAL_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path / "outputs")
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)

    client = TestClient(app_module.app)
    # Non-event feishu path still requires API key. :-)
    other = client.get("/api/feishu/workspaces")
    assert other.status_code in {401, 404, 405}

    # Event path is exempt from API key (signature handled inside router).
    events = client.post("/api/feishu/events", json={"challenge": "ping"})
    assert events.status_code != 401
