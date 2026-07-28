from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.product_decision.bot import build_h5_deep_link, handle_decision_bot_command
from prd_pal.product_decision.models import (
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    OpportunityCandidate,
    OpportunityCandidateStatus,
    PrdVersion,
    PrdVersionStatus,
    ProductOwnerConfig,
)
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.product_decision.router import create_product_decision_router
from prd_pal.utils.time import utc_now_iso


@pytest.mark.asyncio
async def test_bot_supports_submit_summary_pending_query_and_deeplink(tmp_path):
    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    await repo.initialize()
    submitted = await handle_decision_bot_command(
        repository=repo,
        command="submit",
        product_id="p-pilot",
        open_id="ou_pm",
        text="Need offline drafting",
        h5_base_url="https://app.example",
    )
    assert submitted["ok"] is True
    assert "h5_link" in submitted

    summary = await handle_decision_bot_command(
        repository=repo,
        command="summary",
        product_id="p-pilot",
        h5_base_url="https://app.example",
    )
    assert summary["ok"] is True

    pending = await handle_decision_bot_command(
        repository=repo,
        command="pending",
        product_id="p-pilot",
        h5_base_url="https://app.example",
    )
    assert pending["count"] == 0

    queried = await handle_decision_bot_command(
        repository=repo,
        command="query",
        product_id="p-pilot",
        text="offline",
        h5_base_url="https://app.example",
    )
    assert queried["ok"] is True

    link = build_h5_deep_link(
        base_url="https://app.example",
        view="opportunities",
        product_id="p-pilot",
        open_id="ou_owner",
    )
    assert "view=opportunities" in link
    assert "open_id=ou_owner" in link


@pytest.mark.asyncio
async def test_pilot_closed_loop_acceptance(tmp_path):
    """来源同步 → 证据审阅 → owner 批准机会 → PRD 质量评估/豁免 → 导出 → 回跳。"""
    from prd_pal.product_decision.delivery import DeliveryService, FeishuBitableDeliveryTarget
    from prd_pal.product_decision.metrics import compute_pilot_metrics
    from prd_pal.product_decision.prd_lifecycle import ApprovalService, PrdLifecycleService
    from prd_pal.product_decision.services import InsightService, OpportunityService
    from prd_pal.quality_engine.models import QualityAssessment, QualityGateDecision

    class _PassEngine:
        async def assess(self, request):
            return QualityAssessment(
                id="quality-1",
                prd_version_id=request.prd_version_id,
                decision=QualityGateDecision.pass_,
                quality_score=0.95,
                evidence_refs=list(request.evidence_refs),
                created_at=utc_now_iso(),
            )

    class _FakeBitable(FeishuBitableDeliveryTarget):
        def export(self, package):
            from prd_pal.product_decision.delivery import DeliveryResult
            from prd_pal.product_decision.models import DeliveryExportStatus

            return DeliveryResult(
                target_kind="feishu_bitable",
                status=DeliveryExportStatus.succeeded,
                external_url="https://example.feishu.cn/base/app?table=tbl&record=rec-1",
                external_id="rec-1",
            )

    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    await repo.initialize()
    await repo.upsert_product_owner(
        ProductOwnerConfig(product_id="p-pilot", owner_open_id="ou_owner")
    )
    await repo.upsert_source(
        EvidenceSource(
            id="source-1",
            product_id="p-pilot",
            source_type=EvidenceSourceType.feishu_doc,
            external_id="doc-1",
            source_url="https://example.feishu.cn/docx/doc-1",
            display_name="Pilot",
        )
    )
    synced = await repo.sync_evidence(
        "source-1",
        [
            EvidenceRecord(
                id="evidence-1",
                source_id="source-1",
                external_id="doc-1",
                product_id="p-pilot",
                content="Need offline drafting",
                source_url="https://example.feishu.cn/docx/doc-1",
                source_version="v1",
            )
        ],
        cursor="1",
    )
    evidence_id = synced.value[0].id
    insight, _ = await InsightService(repo).create_insight(
        product_id="p-pilot",
        title="Offline drafting",
        evidence_refs=[evidence_id],
    )
    opportunity, _ = await OpportunityService(repo).create_candidate(
        product_id="p-pilot",
        title="Offline mode",
        insight_ids=[insight.id],
    )
    await OpportunityService(repo).submit_for_approval(opportunity.id, actor="ou_pm")
    approved, _ = await ApprovalService(repo).approve_opportunity(
        opportunity.id, actor_open_id="ou_owner", reason="ship"
    )
    assert str(approved.status) == "approved"

    lifecycle = PrdLifecycleService(repo, quality_engine=_PassEngine())
    prd, _ = await lifecycle.create_from_approved_opportunity(
        opportunity.id, actor_open_id="ou_owner", markdown="# Offline\n\nShip"
    )
    checked, _, _ = await lifecycle.assess_quality(prd.id, actor_open_id="ou_owner")
    assert str(checked.status) == "quality_checked"
    released, _ = await lifecycle.approve(prd.id, actor_open_id="ou_owner", reason="pass")
    ready, _ = await lifecycle.mark_ready_for_delivery(prd.id, actor_open_id="ou_owner")
    assert str(ready.status) == "ready_for_delivery"

    export, receipt = await DeliveryService(repo).export_prd(
        ready.id,
        target=_FakeBitable(app_token="app", table_id="tbl"),
        actor_open_id="ou_owner",
    )
    assert export.external_url.endswith("record=rec-1")
    assert receipt.audit_id

    metrics = await compute_pilot_metrics(repo, product_id="p-pilot")
    assert metrics["sync_success_rate"] == 1.0
    assert metrics["insight_with_evidence_rate"] == 1.0
    assert metrics["opportunity_approval_rate"] == 1.0
    assert metrics["quality_pass_rate"] == 1.0
    assert metrics["delivery_completion_rate"] == 1.0


def test_pilot_metrics_api(tmp_path):
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
    with TestClient(app) as client:
        metrics = client.get("/api/decision/pilot/metrics?product_id=p-pilot").json()["metrics"]
        assert "sync_success_rate" in metrics
        bot = client.post(
            "/api/decision/bot/command",
            json={
                "command": "link",
                "product_id": "p-pilot",
                "text": "delivery",
                "h5_base_url": "https://app.example",
            },
        ).json()
        assert bot["ok"] is True
        assert "view=delivery" in bot["h5_link"]
