"""Pilot operating metrics for the single-team decision workbench."""

from __future__ import annotations

from typing import Any

from .repository import ProductDecisionRepository


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


async def compute_pilot_metrics(
    repository: ProductDecisionRepository, *, product_id: str = ""
) -> dict[str, Any]:
    sources = await repository.list_sources(product_id)
    insights = await repository.list_insights(product_id=product_id)
    opportunities = await repository.list_opportunities(product_id=product_id)
    prds = await repository.list_prd_versions(product_id=product_id)
    deliveries = await repository.list_delivery_exports(product_id=product_id)

    source_items = list(sources.value or [])
    insight_items = list(insights.value or [])
    opportunity_items = list(opportunities.value or [])
    prd_items = list(prds.value or [])
    delivery_items = list(deliveries.value or [])

    sync_success = sum(1 for item in source_items if str(item.sync_status) == "succeeded")
    insights_with_evidence = sum(1 for item in insight_items if item.evidence_refs)
    approved_opportunities = sum(
        1 for item in opportunity_items if str(item.status) == "approved"
    )
    quality_pass = sum(1 for item in prd_items if item.quality_decision == "pass")
    quality_checked = sum(
        1
        for item in prd_items
        if str(item.status) in {"quality_checked", "approved", "waived", "ready_for_delivery"}
        or item.quality_decision
    )
    ready_or_exported = sum(
        1 for item in prd_items if str(item.status) == "ready_for_delivery"
    )
    delivered = sum(
        1 for item in delivery_items if str(item.status) in {"succeeded", "degraded"}
    )

    revision_reasons = [
        str(item.metadata.get("reason") or "").strip()
        for item in prd_items
        if item.metadata.get("revised_from") and str(item.metadata.get("reason") or "").strip()
    ]

    return {
        "product_id": product_id,
        "sync_success_rate": _rate(sync_success, len(source_items)),
        "insight_with_evidence_rate": _rate(insights_with_evidence, len(insight_items)),
        "opportunity_approval_rate": _rate(approved_opportunities, len(opportunity_items)),
        "quality_pass_rate": _rate(quality_pass, quality_checked),
        "delivery_completion_rate": _rate(delivered, max(ready_or_exported, delivered)),
        "revision_reasons": revision_reasons[:20],
        "counts": {
            "sources": len(source_items),
            "insights": len(insight_items),
            "opportunities": len(opportunity_items),
            "prd_versions": len(prd_items),
            "deliveries": len(delivery_items),
            "pending_approvals": sum(
                1 for item in opportunity_items if str(item.status) == "pending_approval"
            ),
        },
    }
