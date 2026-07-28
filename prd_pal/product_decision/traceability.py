"""Decision-workspace traceability across evidence → insight → opportunity → PRD → delivery."""

from __future__ import annotations

from typing import Any

from .repository import ProductDecisionRepository


async def build_decision_trace(
    repository: ProductDecisionRepository, root_id: str
) -> dict[str, Any]:
    root_id = str(root_id or "").strip()
    if not root_id:
        raise ValueError("root_id is required")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    evidence = await repository.list_evidence(limit=1000)
    insights = await repository.list_insights()
    opportunities = await repository.list_opportunities()
    prds = await repository.list_prd_versions()
    deliveries = await repository.list_delivery_exports()

    evidence_by_id = {item.id: item for item in (evidence.value or [])}
    insights_by_id = {item.id: item for item in (insights.value or [])}
    opportunities_by_id = {item.id: item for item in (opportunities.value or [])}
    prds_by_id = {item.id: item for item in (prds.value or [])}
    deliveries_list = list(deliveries.value or [])

    seed_type = ""
    seed_ids: set[str] = set()

    if root_id in evidence_by_id:
        seed_type = "evidence"
        seed_ids = {root_id}
    elif root_id in insights_by_id:
        seed_type = "insight"
        seed_ids = {root_id}
    elif root_id in opportunities_by_id:
        seed_type = "opportunity"
        seed_ids = {root_id}
    elif root_id in prds_by_id:
        seed_type = "prd_version"
        seed_ids = {root_id}
    else:
        matched_deliveries = [item for item in deliveries_list if item.id == root_id]
        if matched_deliveries:
            seed_type = "delivery"
            seed_ids = {root_id}
        else:
            # Also allow prd_id without version suffix.
            matched_prds = [item for item in (prds.value or []) if item.prd_id == root_id]
            if matched_prds:
                seed_type = "prd_version"
                seed_ids = {item.id for item in matched_prds}
            else:
                raise ValueError(f"trace root not found: {root_id}")

    selected_evidence: set[str] = set()
    selected_insights: set[str] = set()
    selected_opportunities: set[str] = set()
    selected_prds: set[str] = set()
    selected_deliveries: set[str] = set()

    if seed_type == "evidence":
        selected_evidence |= seed_ids
    elif seed_type == "insight":
        selected_insights |= seed_ids
    elif seed_type == "opportunity":
        selected_opportunities |= seed_ids
    elif seed_type == "prd_version":
        selected_prds |= seed_ids
    elif seed_type == "delivery":
        selected_deliveries |= seed_ids

    # Expand upward and downward from seed.
    changed = True
    while changed:
        changed = False
        for insight in insights.value or []:
            if insight.id in selected_insights or any(
                ref in selected_evidence for ref in insight.evidence_refs
            ):
                if insight.id not in selected_insights:
                    selected_insights.add(insight.id)
                    changed = True
                for ref in insight.evidence_refs:
                    if ref not in selected_evidence and ref in evidence_by_id:
                        selected_evidence.add(ref)
                        changed = True
        for opportunity in opportunities.value or []:
            linked = (
                opportunity.id in selected_opportunities
                or any(item in selected_insights for item in opportunity.insight_ids)
                or any(ref in selected_evidence for ref in opportunity.evidence_refs)
            )
            if linked:
                if opportunity.id not in selected_opportunities:
                    selected_opportunities.add(opportunity.id)
                    changed = True
                selected_insights.update(
                    item for item in opportunity.insight_ids if item in insights_by_id
                )
                selected_evidence.update(
                    ref for ref in opportunity.evidence_refs if ref in evidence_by_id
                )
        for prd in prds.value or []:
            linked = prd.id in selected_prds or prd.opportunity_id in selected_opportunities
            if linked:
                if prd.id not in selected_prds:
                    selected_prds.add(prd.id)
                    changed = True
                if prd.opportunity_id:
                    selected_opportunities.add(prd.opportunity_id)
                selected_evidence.update(
                    ref for ref in prd.evidence_refs if ref in evidence_by_id
                )
        for delivery in deliveries_list:
            linked = (
                delivery.id in selected_deliveries
                or delivery.prd_version_id in selected_prds
            )
            if linked:
                if delivery.id not in selected_deliveries:
                    selected_deliveries.add(delivery.id)
                    changed = True
                if delivery.prd_version_id:
                    selected_prds.add(delivery.prd_version_id)

    for evidence_id in sorted(selected_evidence):
        item = evidence_by_id[evidence_id]
        nodes.append(
            {
                "type": "evidence",
                "id": item.id,
                "label": item.summary or item.content[:80],
                "source_url": item.source_url,
                "product_id": item.product_id,
            }
        )
    for insight_id in sorted(selected_insights):
        item = insights_by_id[insight_id]
        nodes.append(
            {
                "type": "insight",
                "id": item.id,
                "label": item.title,
                "source_urls": list(item.source_urls),
                "product_id": item.product_id,
                "version": item.version,
                "audit_id": item.audit_id,
            }
        )
        for ref in item.evidence_refs:
            if ref in selected_evidence:
                edges.append({"from": ref, "to": item.id, "relation": "supports"})
    for opportunity_id in sorted(selected_opportunities):
        item = opportunities_by_id[opportunity_id]
        nodes.append(
            {
                "type": "opportunity",
                "id": item.id,
                "label": item.title,
                "status": str(item.status),
                "source_urls": list(item.source_urls),
                "product_id": item.product_id,
                "version": item.version,
                "audit_id": item.audit_id,
            }
        )
        for insight_id in item.insight_ids:
            if insight_id in selected_insights:
                edges.append(
                    {"from": insight_id, "to": item.id, "relation": "derives"}
                )
    for prd_id in sorted(selected_prds):
        item = prds_by_id[prd_id]
        nodes.append(
            {
                "type": "prd_version",
                "id": item.id,
                "label": item.title,
                "status": str(item.status),
                "quality_decision": item.quality_decision,
                "product_id": item.product_id,
                "version": item.version,
                "audit_id": item.audit_id,
            }
        )
        if item.opportunity_id in selected_opportunities:
            edges.append(
                {
                    "from": item.opportunity_id,
                    "to": item.id,
                    "relation": "formalizes",
                }
            )
    for delivery_id in sorted(selected_deliveries):
        item = next(entry for entry in deliveries_list if entry.id == delivery_id)
        nodes.append(
            {
                "type": "delivery",
                "id": item.id,
                "label": item.target_kind,
                "status": str(item.status),
                "external_url": item.external_url,
                "product_id": item.product_id,
                "audit_id": item.audit_id,
            }
        )
        if item.prd_version_id in selected_prds:
            edges.append(
                {
                    "from": item.prd_version_id,
                    "to": item.id,
                    "relation": "exports",
                }
            )

    return {
        "root_id": root_id,
        "root_type": seed_type,
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "evidence": len(selected_evidence),
            "insights": len(selected_insights),
            "opportunities": len(selected_opportunities),
            "prd_versions": len(selected_prds),
            "deliveries": len(selected_deliveries),
        },
    }
