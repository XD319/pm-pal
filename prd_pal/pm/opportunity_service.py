"""Turn insights into evaluable opportunity briefs."""

from __future__ import annotations

import uuid
from typing import Any

from prd_pal.utils.llm_structured_call import llm_structured_call

from .schemas import InsightCluster, OpportunityBrief, OpportunityBriefOutput


def _new_opportunity_id() -> str:
    return f"opp-{uuid.uuid4().hex[:12]}"


def _build_opportunity_prompt(
    insights: list[InsightCluster], *, product_hint: str = ""
) -> str:
    lines = [
        "You are a product manager assistant.",
        "Turn the insights below into one opportunity brief.",
        "Cover problem, users, value, constraints, and open questions.",
        "Be concrete and decision-oriented.",
    ]
    if product_hint.strip():
        lines.append(f"Product context: {product_hint.strip()}")
    lines.append("")
    lines.append("Insights:")
    for insight in insights:
        lines.append(
            f"- id={insight.id} title={insight.title}: {insight.summary or insight.theme}"
        )
        if insight.evidence_quotes:
            lines.append(f"  evidence: {'; '.join(insight.evidence_quotes[:3])}")
    return "\n".join(lines)


async def build_opportunity(
    insights: list[InsightCluster],
    *,
    product_hint: str = "",
    run_id: str = "",
) -> OpportunityBrief:
    """Build one opportunity brief from one or more insights."""

    if not insights:
        raise ValueError("insights must not be empty")

    metadata: dict[str, Any] = {
        "agent_name": "pm_opportunity_brief",
        "run_id": run_id or "pm-opportunity",
    }
    raw = await llm_structured_call(
        prompt=_build_opportunity_prompt(insights, product_hint=product_hint),
        schema=OpportunityBriefOutput,
        metadata=metadata,
    )
    parsed = OpportunityBriefOutput.model_validate(raw)
    insight_ids = [insight.id for insight in insights]
    source_refs = [f"insight:{insight_id}" for insight_id in insight_ids]
    evidence_refs: list[str] = []
    for insight in insights:
        evidence_refs.extend(insight.source_refs)
        evidence_refs.extend(f"feedback:{fid}" for fid in insight.feedback_ids)
    # Preserve order while deduplicating.
    evidence_refs = list(dict.fromkeys(evidence_refs))

    return OpportunityBrief(
        id=_new_opportunity_id(),
        title=parsed.title,
        problem=parsed.problem,
        users=parsed.users,
        value=parsed.value,
        constraints=list(parsed.constraints),
        open_questions=list(parsed.open_questions),
        insight_ids=insight_ids,
        source_refs=source_refs,
        evidence_refs=evidence_refs,
    )
