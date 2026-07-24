"""Generate PRD drafts from opportunities and run the review quality gate."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Awaitable

from prd_pal.utils.llm_structured_call import llm_structured_call

from .schemas import OpportunityBrief, PRDDraft, PRDDraftOutput

ReviewCallable = Callable[..., Awaitable[dict[str, Any]]]


def _new_prd_id() -> str:
    return f"prd-{uuid.uuid4().hex[:12]}"


def _build_prd_prompt(
    opportunity: OpportunityBrief, *, product_hint: str = ""
) -> str:
    lines = [
        "You are a product manager assistant.",
        "Write a concise PRD markdown draft from the opportunity brief.",
        "Include goals, in-scope, out-of-scope, acceptance criteria, risks, and success metrics.",
        "Return structured fields plus a complete markdown body.",
    ]
    if product_hint.strip():
        lines.append(f"Product context: {product_hint.strip()}")
    lines.extend(
        [
            "",
            f"Title hint: {opportunity.title}",
            f"Problem: {opportunity.problem}",
            f"Users: {opportunity.users}",
            f"Value: {opportunity.value}",
            f"Constraints: {', '.join(opportunity.constraints) or 'none'}",
            f"Open questions: {', '.join(opportunity.open_questions) or 'none'}",
        ]
    )
    return "\n".join(lines)


async def draft_prd_from_opportunity(
    opportunity: OpportunityBrief,
    *,
    product_hint: str = "",
    run_id: str = "",
    review_callable: ReviewCallable | None = None,
    run_quality_gate: bool = True,
) -> tuple[PRDDraft, dict[str, Any] | None]:
    """Generate a PRD draft and optionally run the existing review quality gate.

    Returns:
        (prd_draft, review_payload_or_none)
    """

    metadata: dict[str, Any] = {
        "agent_name": "pm_prd_draft",
        "run_id": run_id or "pm-prd",
    }
    raw = await llm_structured_call(
        prompt=_build_prd_prompt(opportunity, product_hint=product_hint),
        schema=PRDDraftOutput,
        metadata=metadata,
    )
    parsed = PRDDraftOutput.model_validate(raw)
    draft = PRDDraft(
        id=_new_prd_id(),
        title=parsed.title,
        markdown=parsed.markdown,
        opportunity_id=opportunity.id,
        goals=list(parsed.goals),
        in_scope=list(parsed.in_scope),
        out_of_scope=list(parsed.out_of_scope),
        acceptance_criteria=list(parsed.acceptance_criteria),
        risks=list(parsed.risks),
        success_metrics=list(parsed.success_metrics),
        source_refs=[f"opportunity:{opportunity.id}"],
        evidence_refs=list(
            dict.fromkeys([*opportunity.source_refs, *opportunity.evidence_refs])
        ),
    )

    review_payload: dict[str, Any] | None = None
    if run_quality_gate:
        reviewer = review_callable
        if reviewer is None:
            from prd_pal.service.review_service import review_requirement_for_mcp_async

            async def _default_review(**kwargs: Any) -> dict[str, Any]:
                return await review_requirement_for_mcp_async(**kwargs)

            reviewer = _default_review

        review_payload = await reviewer(
            prd_text=draft.markdown,
            prd_path=None,
            source=None,
            options={"mode": "quick"},
            invocation_meta={"source": "pm_prd_service"},
        )
        if isinstance(review_payload, dict):
            review_run_id = str(
                review_payload.get("run_id") or review_payload.get("review_id") or ""
            ).strip()
            if review_run_id:
                draft = draft.model_copy(update={"review_run_id": review_run_id})

    return draft, review_payload
