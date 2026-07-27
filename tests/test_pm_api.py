"""Tests for /api/pm endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.pm.schemas import (
    InsightCluster,
    OpportunityBrief,
    PRDDraft,
    PipelineStatus,
)
from prd_pal.server.pm_router import create_pm_router


def _make_client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(create_pm_router(db_path=tmp_path / "pm.sqlite3"))
    return TestClient(app)


def test_create_feedback_endpoint(tmp_path) -> None:
    with _make_client(tmp_path) as client:
        response = client.post(
            "/api/pm/feedback",
            json={
                "texts": ["Login is confusing", "Checkout crashes"],
                "product_hint": "commerce",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["feedback_ids"]) == 2


def test_pipeline_run_and_get(tmp_path) -> None:
    opportunity = OpportunityBrief(
        id="opp-1",
        title="Simplify auth",
        insight_ids=["ins-1"],
        source_refs=["insight:ins-1"],
    )
    prd = PRDDraft(
        id="prd-1",
        title="Auth PRD",
        markdown="# Goals\n- Reduce drop-off",
        opportunity_id="opp-1",
        review_run_id="run-1",
    )

    async def _fake_cluster(feedback_items, **kwargs):
        return [
            InsightCluster(
                id="ins-1",
                title="Auth friction",
                feedback_ids=[feedback_items[0].id],
                source_refs=[f"feedback:{feedback_items[0].id}"],
            )
        ]

    with (
        patch(
            "prd_pal.pm.workflow.cluster_feedback",
            new=AsyncMock(side_effect=_fake_cluster),
        ),
        patch(
            "prd_pal.pm.workflow.build_opportunity",
            new=AsyncMock(return_value=opportunity),
        ),
        patch(
            "prd_pal.pm.workflow.draft_prd_from_opportunity",
            new=AsyncMock(return_value=(prd, {"run_id": "run-1"})),
        ),
        _make_client(tmp_path) as client,
    ):
        run_response = client.post(
            "/api/pm/pipeline/run",
            json={
                "feedback_texts": ["Login is confusing"],
                "product_hint": "commerce",
                "run_quality_gate": False,
            },
        )
        assert run_response.status_code == 200
        run_payload = run_response.json()
        pipeline_id = run_payload["pipeline_id"]
        get_response = client.get(f"/api/pm/pipeline/{pipeline_id}")

    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["pipeline"]["status"] == PipelineStatus.completed
    assert get_payload["prd"]["id"] == "prd-1"


def test_get_pipeline_not_found(tmp_path) -> None:
    with _make_client(tmp_path) as client:
        response = client.get("/api/pm/pipeline/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "pipeline_not_found"


def test_product_workspace_endpoints(tmp_path) -> None:
    with _make_client(tmp_path) as client:
        created = client.post("/api/pm/products", json={"name": "Commerce", "target_users": "buyers"})
        assert created.status_code == 200
        product_id = created.json()["product_id"]
        listed = client.get("/api/pm/products")
        workspace = client.get(f"/api/pm/products/{product_id}/workspace")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert workspace.status_code == 200
    assert workspace.json()["product"]["name"] == "Commerce"
    assert workspace.json()["counts"] == {"feedback": 0, "roadmap": 0}


def test_feishu_feedback_inbox_preserves_evidence(tmp_path) -> None:
    with _make_client(tmp_path) as client:
        response = client.post("/api/pm/feishu/feedback", json={"texts": ["Activation is blocked"], "product_id": "p-1", "source_url": "https://example.feishu.cn/docx/abc", "open_id": "ou-1", "tenant_key": "t-1"})
        inbox = client.get("/api/pm/feedback?product_id=p-1")
    assert response.status_code == 200
    assert inbox.json()["count"] == 1
    item = inbox.json()["feedback"][0]
    assert item["source_refs"] == ["https://example.feishu.cn/docx/abc"]
    assert item["metadata"]["open_id"] == "ou-1"


def test_opportunity_decision_workflow(tmp_path) -> None:
    with _make_client(tmp_path) as client:
        from prd_pal.pm.schemas import OpportunityBrief
        from prd_pal.pm.repository import PmRepository
        import asyncio
        repo = PmRepository(tmp_path / "pm.sqlite3")
        asyncio.run(repo.initialize())
        asyncio.run(repo.upsert_artifact(artifact_type="opportunity", artifact_id="opp-1", payload=OpportunityBrief(id="opp-1", title="Fix login", problem="Users abandon login", product_id="p-1")))
        response = client.post("/api/pm/opportunities/opp-1/decision", json={"status": "approved", "rationale": "High impact", "product_id": "p-1"})
        decisions = client.get("/api/pm/decisions?product_id=p-1")
    assert response.status_code == 200
    assert decisions.json()["decisions"][0]["status"] == "approved"
