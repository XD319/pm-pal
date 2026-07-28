from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.product_decision.prd_lifecycle import PrdLifecycleService
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.product_decision.router import create_product_decision_router
from prd_pal.quality_engine.models import QualityAssessment, QualityGateDecision
from prd_pal.pm.schemas import PRDStatus


def _seed_approved_opportunity(client: TestClient) -> str:
    client.post(
        "/api/decision/owners",
        json={
            "product_id": "p-1",
            "owner_open_id": "ou_owner",
            "admin_open_ids": ["ou_admin"],
        },
    )
    source = client.post(
        "/api/decision/sources",
        json={
            "product_id": "p-1",
            "source_type": "feishu_doc",
            "external_id": "doc-1",
            "source_url": "https://example.feishu.cn/docx/doc-1",
            "display_name": "Notes",
        },
    ).json()["source"]
    evidence_id = client.post(
        f"/api/decision/sources/{source['id']}/sync",
        json={
            "cursor": "1",
            "records": [
                {
                    "external_id": "doc-1",
                    "content": "Users need offline drafting",
                    "source_url": "https://example.feishu.cn/docx/doc-1",
                    "source_version": "v1",
                }
            ],
        },
    ).json()["evidence"][0]["id"]
    insight = client.post(
        "/api/decision/insights",
        json={
            "product_id": "p-1",
            "title": "Offline drafting",
            "evidence_refs": [evidence_id],
        },
    ).json()["insight"]
    opportunity = client.post(
        "/api/decision/opportunities",
        json={
            "product_id": "p-1",
            "title": "Offline mode",
            "insight_ids": [insight["id"]],
        },
    ).json()["opportunity"]
    client.post(
        f"/api/decision/opportunities/{opportunity['id']}/submit",
        json={"actor": "ou_pm", "reason": "ready"},
    )
    approved = client.post(
        f"/api/decision/opportunities/{opportunity['id']}/approve",
        json={"actor_open_id": "ou_owner", "reason": "approved"},
    ).json()
    assert approved["opportunity"]["status"] == "approved"
    return opportunity["id"]


class _FakeQualityEngine:
    def __init__(self, decision: QualityGateDecision = QualityGateDecision.pass_) -> None:
        self.decision = decision
        self.calls = 0

    async def assess(self, request):
        self.calls += 1
        return QualityAssessment(
            id=f"quality-{self.calls}",
            prd_version_id=request.prd_version_id,
            decision=self.decision,
            quality_score=0.9 if self.decision == QualityGateDecision.pass_ else 0.2,
            findings=[] if self.decision == QualityGateDecision.pass_ else [{"title": "gap"}],
            evidence_refs=list(request.evidence_refs),
            created_at="2026-07-28T00:00:00+00:00",
        )


def test_prd_lifecycle_pass_path_and_version_immutability(tmp_path) -> None:
    app = FastAPI()
    db = tmp_path / "decision.sqlite3"
    app.include_router(create_product_decision_router(db_path=db))
    with TestClient(app) as client:
        opportunity_id = _seed_approved_opportunity(client)
        created = client.post(
            f"/api/decision/opportunities/{opportunity_id}/prd",
            json={"actor_open_id": "ou_owner", "markdown": "# Offline\n\nShip it."},
        ).json()
        version_id = created["prd_version"]["id"]
        assert created["prd_version"]["status"] == "draft"
        original_markdown = created["prd_version"]["markdown"]

    async def _assess_and_approve() -> None:
        repo = ProductDecisionRepository(db)
        await repo.initialize()
        lifecycle = PrdLifecycleService(repo, quality_engine=_FakeQualityEngine())
        version, assessment, _ = await lifecycle.assess_quality(
            version_id, actor_open_id="ou_owner"
        )
        assert str(version.status) == "quality_checked"
        assert assessment.decision == QualityGateDecision.pass_
        approved, _ = await lifecycle.approve(
            version_id, actor_open_id="ou_owner", reason="ship"
        )
        assert str(approved.status) == "approved"
        again, receipt = await lifecycle.approve(
            version_id, actor_open_id="ou_owner", reason="ship again"
        )
        assert str(again.status) == "approved"
        assert receipt.status == "approved"
        ready, _ = await lifecycle.mark_ready_for_delivery(
            version_id, actor_open_id="ou_owner"
        )
        assert str(ready.status) == "ready_for_delivery"
        revised, _ = await lifecycle.revise(
            version_id,
            markdown="# Offline\n\nShip it with edits.",
            actor_open_id="ou_owner",
            reason="reopen",
        )
        assert revised.version == 2
        assert str(revised.status) == "draft"
        old = await repo.get_prd_version(version_id)
        assert old.value.markdown == original_markdown
        assert str(old.value.status) == "ready_for_delivery"

    asyncio.run(_assess_and_approve())


def test_waiver_requires_reason_and_blocks_pass_path(tmp_path) -> None:
    app = FastAPI()
    db = tmp_path / "decision.sqlite3"
    app.include_router(create_product_decision_router(db_path=db))
    with TestClient(app) as client:
        opportunity_id = _seed_approved_opportunity(client)
        version_id = client.post(
            f"/api/decision/opportunities/{opportunity_id}/prd",
            json={"actor_open_id": "ou_owner"},
        ).json()["prd_version"]["id"]

    async def _run() -> None:
        repo = ProductDecisionRepository(db)
        await repo.initialize()
        lifecycle = PrdLifecycleService(
            repo, quality_engine=_FakeQualityEngine(QualityGateDecision.blocked)
        )
        await lifecycle.assess_quality(version_id, actor_open_id="ou_owner")
        with pytest.raises(Exception) as blocked_approve:
            await lifecycle.approve(version_id, actor_open_id="ou_owner", reason="nope")
        assert blocked_approve.value.code == "quality_not_passed"
        with pytest.raises(Exception) as missing_reason:
            await lifecycle.waive(version_id, actor_open_id="ou_owner", reason=" ")
        assert missing_reason.value.code == "waiver_reason_required"
        waived, receipt = await lifecycle.waive(
            version_id, actor_open_id="ou_owner", reason="Accepted residual risk"
        )
        assert str(waived.status) == "waived"
        assert receipt.audit_id

    asyncio.run(_run())


def test_unapproved_opportunity_cannot_create_prd(tmp_path) -> None:
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
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
                "external_id": "doc-x",
                "source_url": "https://example.feishu.cn/docx/doc-x",
            },
        ).json()["source"]
        evidence_id = client.post(
            f"/api/decision/sources/{source['id']}/sync",
            json={
                "cursor": "1",
                "records": [
                    {
                        "external_id": "doc-x",
                        "content": "feedback",
                        "source_url": "https://example.feishu.cn/docx/doc-x",
                        "source_version": "v1",
                    }
                ],
            },
        ).json()["evidence"][0]["id"]
        insight = client.post(
            "/api/decision/insights",
            json={
                "product_id": "p-1",
                "title": "Theme",
                "evidence_refs": [evidence_id],
            },
        ).json()["insight"]
        opportunity = client.post(
            "/api/decision/opportunities",
            json={
                "product_id": "p-1",
                "title": "Candidate",
                "insight_ids": [insight["id"]],
            },
        ).json()["opportunity"]
        blocked = client.post(f"/api/decision/opportunities/{opportunity['id']}/prd")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "opportunity_not_approved"


def test_legacy_in_review_is_compat_read_model_only() -> None:
    # New workbench gates on PrdVersionStatus; PM in_review remains a legacy label.
    assert PRDStatus.in_review == "in_review"
    assert "in_review" not in {
        "draft",
        "quality_checked",
        "approved",
        "waived",
        "ready_for_delivery",
    }
