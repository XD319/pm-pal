"""Engineering-focused heuristic reviewer."""

from __future__ import annotations

import asyncio

from ..normalizer import NormalizedRequirement
from .base import (
    ReviewFinding,
    ReviewerConfig,
    ReviewerResult,
    RiskItem,
    build_reviewer_result,
)
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
    risks: list[RiskItem] = []
    open_questions: list[str] = []
    memory_evidence = build_memory_evidence(memory_context)
    memory_notes = build_memory_notes(memory_context, memory_mode=memory_mode)

    query = " ".join(
        [
            requirement.summary,
            *requirement.modules[:4],
            *requirement.dependency_hints[:4],
            reviewer_input[:200],
        ]
    ).strip()
    tool_result = get_reviewer_toolbox().local_risk_catalog.run(
        reviewer="engineering", query=query
    )
    evidence = tuple([*memory_evidence, *tool_result.evidence])
    tool_calls = (tool_result.tool_call,) if tool_result.tool_call else ()

    if requirement.dependency_hints and not requirement.modules:
        findings.append(
            ReviewFinding(
                title="Dependencies lack implementation boundaries",
                detail="The PRD implies integrations or shared dependencies but does not name impacted modules.",
                severity="medium",
                category="architecture",
                reviewer="engineering",
                evidence=evidence[:2],
            )
        )

    if len(requirement.dependency_hints) >= 2:
        risks.append(
            RiskItem(
                title="Cross-system sequencing risk",
                detail="Multiple dependency hints suggest implementation order and ownership could drift.",
                severity="high",
                category="integration",
                mitigation="Define system boundaries, contract owners, and rollout order before build starts.",
                reviewer="engineering",
            )
        )

    if requirement.roles and len(requirement.roles) >= 4:
        open_questions.append(
            "Which engineering team owns each module and integration boundary?"
        )

    ambiguity_type = (
        "missing_implementation_boundaries"
        if requirement.dependency_hints and not requirement.modules
        else ""
    )
    clarification_question = (
        "Which modules, service boundaries, and owners are impacted by this requirement?"
        if ambiguity_type
        else ""
    )
    reviewer_status_detail = f"Engineering review completed with {len(findings)} findings, {len(risks)} risks, and {len(evidence)} evidence hits."

    await asyncio.sleep(0)
    return build_reviewer_result(
        reviewer="engineering",
        config=resolved,
        findings=findings,
        open_questions=open_questions,
        risk_items=risks,
        evidence=evidence,
        tool_calls=tool_calls,
        summary="Engineering review completed against module and dependency complexity.",
        ambiguity_type=ambiguity_type,
        clarification_question=clarification_question,
        reviewer_status_detail=reviewer_status_detail,
        notes=(
            "Evidence prioritized from local risk catalog for dependency and architecture signals.",
            *memory_notes,
        ),
    )
