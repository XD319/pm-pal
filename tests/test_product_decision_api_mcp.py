from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.mcp_server import server as mcp_server
from prd_pal.platform import LocalJobQueue
from prd_pal.product_decision.mcp_tools import (
    create_opportunity_candidate_for_mcp,
    search_decision_evidence_for_mcp,
    submit_opportunity_decision_for_mcp,
)
from prd_pal.product_decision.models import (
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    ProductOwnerConfig,
)
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.product_decision.router import create_product_decision_router
from prd_pal.product_decision.services import InsightService


@pytest.mark.asyncio
async def test_persistent_job_queue_recovers_unfinished_jobs(tmp_path):
    db = tmp_path / "jobs.sqlite3"
    queue = LocalJobQueue(db, max_attempts=3)
    await queue.initialize()

    async def boom(payload):
        raise RuntimeError(f"source failed:{payload['n']}")

    failed = await queue.enqueue(key="job-1", kind="evidence_sync", payload={"n": 1}, handler=boom)
    assert failed["status"] == "failed"
    assert failed["source_error"]
    assert failed["audit_events"]
    assert failed["notification_events"]

    # Simulate crash mid-flight by persisting a running job.
    queue._jobs["job-2"] = {
        "key": "job-2",
        "kind": "evidence_sync",
        "status": "running",
        "payload": {"n": 2},
        "attempts": 1,
        "retry_count": 0,
        "source_error": "",
        "audit_events": [],
        "notification_events": [],
        "created_at": "t0",
        "updated_at": "t0",
    }
    queue._persist(queue._jobs["job-2"])

    calls: list[int] = []

    async def ok(payload):
        calls.append(payload["n"])
        return {"ok": payload["n"]}

    restarted = LocalJobQueue(db, max_attempts=3)
    await restarted.initialize()
    assert (await restarted.get("job-2"))["status"] == "running"
    resumed = await restarted.recover({"evidence_sync": ok})
    assert any(item["key"] == "job-2" and item["status"] == "completed" for item in resumed)
    assert 2 in calls

    # Duplicate enqueue is idempotent for completed jobs.
    again = await restarted.enqueue(
        key="job-2", kind="evidence_sync", payload={"n": 99}, handler=ok
    )
    assert again["result"] == {"ok": 2}


@pytest.mark.asyncio
async def test_decision_mcp_write_receipts_and_permission_denial(tmp_path):
    db = str(tmp_path / "decision.sqlite3")
    repo = ProductDecisionRepository(db)
    await repo.initialize()
    await repo.upsert_source(
        EvidenceSource(
            id="source-1",
            product_id="p-1",
            source_type=EvidenceSourceType.feishu_doc,
            external_id="doc-1",
            source_url="https://example.feishu.cn/docx/doc-1",
        )
    )
    await repo.sync_evidence(
        "source-1",
        [
            EvidenceRecord(
                id="evidence-1",
                source_id="source-1",
                external_id="doc-1",
                product_id="p-1",
                content="Need offline drafting",
                source_url="https://example.feishu.cn/docx/doc-1",
                source_version="v1",
            )
        ],
        cursor="1",
    )
    await repo.upsert_product_owner(
        ProductOwnerConfig(product_id="p-1", owner_open_id="ou_owner")
    )
    insight, _ = await InsightService(repo).create_insight(
        product_id="p-1",
        title="Offline drafting",
        evidence_refs=["evidence-1"],
    )

    searched = await search_decision_evidence_for_mcp(
        product_id="p-1", query="offline", options={"db_path": db}
    )
    assert searched["count"] == 1

    created = await create_opportunity_candidate_for_mcp(
        product_id="p-1",
        title="Offline mode",
        insight_ids=[insight.id],
        options={"db_path": db},
    )
    assert created["artifact_id"]
    assert created["audit_id"]
    assert created["next_human_action"]
    assert created["version"] == 1

    denied = await submit_opportunity_decision_for_mcp(
        opportunity_id=created["artifact_id"],
        action="approve",
        actor_open_id="ou_stranger",
        options={"db_path": db},
    )
    assert denied["error"]["code"] == "permission_denied"

    submitted = await submit_opportunity_decision_for_mcp(
        opportunity_id=created["artifact_id"],
        action="submit",
        actor_open_id="ou_pm",
        options={"db_path": db},
    )
    assert submitted["status"] == "pending_approval"
    assert submitted["audit_id"]

    duplicate = await create_opportunity_candidate_for_mcp(
        product_id="p-1",
        title="Offline mode again",
        insight_ids=[insight.id],
        evidence_refs=["evidence-1"],
        options={"db_path": db},
    )
    assert duplicate["artifact_id"] != created["artifact_id"]


def test_decision_api_sync_status_quality_and_trace(tmp_path):
    app = FastAPI()
    db = tmp_path / "decision.sqlite3"
    app.include_router(create_product_decision_router(db_path=db))
    with TestClient(app) as client:
        client.post(
            "/api/decision/owners",
            json={"product_id": "p-1", "owner_open_id": "ou_owner"},
        )
        source = client.post(
            "/api/decision/sources",
            json={
                "product_id": "p-1",
                "source_type": "feishu_doc",
                "external_id": "doc-1",
                "source_url": "https://example.feishu.cn/docx/doc-1",
            },
        ).json()["source"]
        evidence_id = client.post(
            f"/api/decision/sources/{source['id']}/sync",
            json={
                "cursor": "1",
                "records": [
                    {
                        "external_id": "doc-1",
                        "content": "Need offline drafting",
                        "source_url": "https://example.feishu.cn/docx/doc-1",
                        "source_version": "v1",
                    }
                ],
            },
        ).json()["evidence"][0]["id"]
        status = client.get(f"/api/decision/sources/{source['id']}/sync-status").json()
        assert status["sync_status"] == "succeeded"
        insight = client.post(
            "/api/decision/insights",
            json={
                "product_id": "p-1",
                "title": "Offline",
                "evidence_refs": [evidence_id],
            },
        ).json()
        opportunity = client.post(
            "/api/decision/opportunities",
            json={
                "product_id": "p-1",
                "title": "Offline opp",
                "insight_ids": [insight["artifact_id"]],
            },
        ).json()
        trace = client.get(f"/api/decision/trace/{opportunity['artifact_id']}").json()
        assert trace["counts"]["evidence"] >= 1
        assert trace["counts"]["opportunities"] == 1
        assert any(node["type"] == "evidence" for node in trace["nodes"])


@pytest.mark.asyncio
async def test_existing_review_mcp_tools_still_work(monkeypatch):
    async def fake_review_prd_for_mcp_async(**kwargs):
        return {"run_id": "run-1", "status": "completed"}

    async def fake_review_requirement_for_mcp_async(**kwargs):
        return {"review_id": "rev-1", "run_id": "run-1", "findings": []}

    monkeypatch.setattr(
        mcp_server, "review_prd_for_mcp_async", fake_review_prd_for_mcp_async
    )
    monkeypatch.setattr(
        mcp_server,
        "review_requirement_for_mcp_async",
        fake_review_requirement_for_mcp_async,
    )
    prd = await mcp_server.review_prd(prd_text="# Goals\n- ship")
    req = await mcp_server.review_requirement(prd_text="# Goals\n- ship")
    assert prd["status"] == "completed"
    assert req["review_id"] == "rev-1"
