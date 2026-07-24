"""Cluster raw feedback into explainable insights."""

from __future__ import annotations

import uuid
from typing import Any

from prd_pal.utils.llm_structured_call import llm_structured_call

from .schemas import FeedbackItem, InsightCluster, InsightExtractionOutput


def _new_insight_id() -> str:
    return f"ins-{uuid.uuid4().hex[:12]}"


def _build_cluster_prompt(
    feedback_items: list[FeedbackItem], *, product_hint: str = ""
) -> str:
    lines = [
        "You are a product manager assistant.",
        "Cluster the following user feedback into 3-5 explainable insights.",
        "Each insight MUST reference the feedback ids it came from.",
        "Prefer concrete themes over vague summaries.",
    ]
    if product_hint.strip():
        lines.append(f"Product context: {product_hint.strip()}")
    lines.append("")
    lines.append("Feedback items:")
    for item in feedback_items:
        lines.append(f"- id={item.id}: {item.text}")
    return "\n".join(lines)


async def cluster_feedback(
    feedback_items: list[FeedbackItem],
    *,
    product_hint: str = "",
    run_id: str = "",
) -> list[InsightCluster]:
    """Cluster feedback into insights with source_refs pointing at feedback ids."""

    if not feedback_items:
        raise ValueError("feedback_items must not be empty")

    known_ids = {item.id for item in feedback_items}
    metadata: dict[str, Any] = {
        "agent_name": "pm_insight_cluster",
        "run_id": run_id or "pm-insight",
    }
    raw = await llm_structured_call(
        prompt=_build_cluster_prompt(feedback_items, product_hint=product_hint),
        schema=InsightExtractionOutput,
        metadata=metadata,
    )
    parsed = InsightExtractionOutput.model_validate(raw)
    insights: list[InsightCluster] = []
    for item in parsed.insights:
        feedback_ids = [fid for fid in item.feedback_ids if fid in known_ids]
        if not feedback_ids and known_ids:
            # Fall back to all known ids only when the model omitted refs.
            feedback_ids = sorted(known_ids)[:3]
        source_refs = [f"feedback:{fid}" for fid in feedback_ids]
        insights.append(
            InsightCluster(
                id=_new_insight_id(),
                title=item.title,
                summary=item.summary,
                theme=item.theme,
                feedback_ids=feedback_ids,
                source_refs=source_refs,
                evidence_quotes=list(item.evidence_quotes),
                metadata={"notes": parsed.notes} if parsed.notes else {},
            )
        )
    if not insights:
        raise ValueError("insight clustering returned no insights")
    return insights
