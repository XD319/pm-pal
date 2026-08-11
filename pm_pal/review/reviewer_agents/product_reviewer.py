"""Product-focused heuristic reviewer."""

from __future__ import annotations

import asyncio

from ..normalizer import NormalizedRequirement
from .base import ReviewFinding, ReviewerConfig, ReviewerResult, build_reviewer_result
from .memory_support import build_memory_evidence, build_memory_notes
from .tooling import get_reviewer_toolbox


async def review(
    requirement: NormalizedRequirement,
    config: ReviewerConfig | None = None,
    *,
    reviewer_input: str = "",
    memory_context: tuple[dict, ...] = (),
    memory_mode: str = "off",
) -> ReviewerResult:
    resolved = config or ReviewerConfig()
    findings: list[ReviewFinding] = []
    open_questions: list[str] = []
    memory_evidence = build_memory_evidence(memory_context)
    memory_notes = build_memory_notes(memory_context, memory_mode=memory_mode)

    query = " ".join(
        part for part in (requirement.summary, reviewer_input[:200]) if part
    ).strip()
    web_tool = get_reviewer_toolbox().web_search.run(
        reviewer="product", query=query or requirement.summary
    )
    tool_calls = (web_tool.tool_call,) if web_tool.tool_call else ()

    if not requirement.scenarios:
        findings.append(
            ReviewFinding(
                title="User scenarios are missing",
                detail="The PRD does not describe concrete user scenarios or business flows.",
                severity="high",
                category="scope",
                reviewer="product",
            )
        )

    if not requirement.acceptance_criteria:
        findings.append(
            ReviewFinding(
                title="Acceptance criteria are incomplete",
                detail="The PRD lacks testable acceptance criteria for product sign-off.",
                severity="high",
                category="acceptance",
                reviewer="product",
            )
        )

    if requirement.summary == "Requirement summary unavailable.":
        open_questions.append(
            "What is the concise product goal and success outcome for this PRD?"
        )

    if requirement.scenarios and not requirement.roles:
        open_questions.append(
            "Which user roles or operators own the described scenarios?"
        )

    ambiguity_type = (
        "missing_product_goal"
        if requirement.summary == "Requirement summary unavailable."
        else ""
    )
    clarification_question = (
        "What user outcome, target persona, and success metric should this requirement optimize for?"
        if ambiguity_type
        else ""
    )
    reviewer_status_detail = f"Product review completed with {len(findings)} findings. Competitive-search hook is {'configured' if web_tool.evidence else 'available as an optional stub'}. Memory assists={len(memory_evidence)}."

    await asyncio.sleep(0)
    return build_reviewer_result(
        reviewer="product",
        config=resolved,
        findings=findings,
        open_questions=open_questions,
        evidence=[*memory_evidence, *web_tool.evidence],
        tool_calls=tool_calls,
        summary="Product review completed against scenarios and acceptance coverage.",
        ambiguity_type=ambiguity_type,
        clarification_question=clarification_question,
        reviewer_status_detail=reviewer_status_detail,
        notes=(
            "Product keeps an optional web-search hook for future competitive evidence without requiring live search now.",
            *memory_notes,
        ),
    )
