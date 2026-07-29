"""Deterministic, idempotent demo data for the decision workbench."""

from __future__ import annotations

from .models import EvidenceRecord, EvidenceSource, EvidenceSourceType, ProductOwnerConfig
from .prd_lifecycle import ApprovalService, PrdLifecycleService
from .repository import ProductDecisionRepository
from .services import InsightService, OpportunityService

DEMO_PRODUCT_ID = "demo-mobile-commerce"
DEMO_OWNER_ID = "demo-owner"
DEMO_SOURCE_ID = "demo-feedback-source"


async def seed_demo(repository: ProductDecisionRepository) -> dict[str, object]:
    """Create a small, realistic product decision chain without calling an LLM.

    The source/evidence ids are stable. Re-running the command preserves user data
    and only updates the dedicated demo source.
    """
    await repository.initialize()
    await repository.upsert_product_owner(
        ProductOwnerConfig(product_id=DEMO_PRODUCT_ID, owner_open_id=DEMO_OWNER_ID)
    )
    await repository.upsert_source(
        EvidenceSource(
            id=DEMO_SOURCE_ID,
            product_id=DEMO_PRODUCT_ID,
            source_type=EvidenceSourceType.feishu_bitable,
            external_id="demo-feedback-board",
            display_name="Demo: mobile checkout feedback",
            source_url="https://example.feishu.cn/base/demo-feedback",
            metadata={"demo": True},
        )
    )
    records = [
        EvidenceRecord(id="demo-evidence-1", source_id=DEMO_SOURCE_ID, external_id="fb-1", product_id=DEMO_PRODUCT_ID, content="iPhone Safari users cannot see the coupon button at checkout.", author="Demo user", source_version="1", metadata={"demo": True}),
        EvidenceRecord(id="demo-evidence-2", source_id=DEMO_SOURCE_ID, external_id="fb-2", product_id=DEMO_PRODUCT_ID, content="Checkout often freezes after selecting a shipping address on mobile.", author="Support", source_version="1", metadata={"demo": True}),
        EvidenceRecord(id="demo-evidence-3", source_id=DEMO_SOURCE_ID, external_id="fb-3", product_id=DEMO_PRODUCT_ID, content="Customers abandon the order when payment confirmation takes too long.", author="Sales", source_version="1", metadata={"demo": True}),
    ]
    saved = await repository.sync_evidence(DEMO_SOURCE_ID, records, cursor="demo-v1")
    evidence_ids = [item.id for item in (saved.value or [])]
    for evidence_id in evidence_ids:
        await repository.mark_evidence_confirmed(evidence_id)

    insights = await repository.list_insights(product_id=DEMO_PRODUCT_ID)
    if not (insights.value or []):
        insight, _ = await InsightService(repository).create_insight(
            product_id=DEMO_PRODUCT_ID,
            title="Mobile checkout confidence is breaking",
            summary="Multiple confirmed feedback items point to a fragile mobile checkout path.",
            theme="checkout reliability",
            evidence_refs=evidence_ids,
            actor="demo-seed",
            metadata={"agent": "demo-evidence-synthesizer", "confidence": 0.86, "demo": True},
        )
        opportunity, _ = await OpportunityService(repository).create_candidate(
            product_id=DEMO_PRODUCT_ID,
            title="Stabilize mobile checkout",
            problem=insight.summary,
            users="Mobile customers",
            value="Reduce checkout abandonment and support tickets.",
            insight_ids=[insight.id],
            actor="demo-seed",
            metadata={"agent": "demo-opportunity-planner", "confidence": 0.82, "demo": True},
        )
        opportunity, _ = await OpportunityService(repository).submit_for_approval(
            opportunity.id, actor="demo-pm", reason="Demo decision ready for owner review"
        )
        opportunity, _ = await ApprovalService(repository).approve_opportunity(
            opportunity.id, actor_open_id=DEMO_OWNER_ID, reason="Validated demo opportunity"
        )
        prd, _ = await PrdLifecycleService(repository).create_from_approved_opportunity(
            opportunity.id, actor_open_id=DEMO_OWNER_ID
        )
        await PrdLifecycleService(repository).assess_quality(prd.id, actor_open_id=DEMO_OWNER_ID)

    return {"product_id": DEMO_PRODUCT_ID, "source_id": DEMO_SOURCE_ID, "evidence_count": len(evidence_ids)}
