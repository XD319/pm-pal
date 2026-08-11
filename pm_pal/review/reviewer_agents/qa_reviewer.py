"""QA-focused heuristic reviewer."""

from __future__ import annotations

import asyncio

from ..normalizer import NormalizedRequirement
from .base import (
    EvidenceItem,
    ReviewFinding,
    ReviewerConfig,
    ReviewerResult,
    RiskItem,
    ToolCall,
    build_reviewer_result,
)
from .memory_support import build_memory_evidence, build_memory_notes


def _defect_heuristic(
    requirement: NormalizedRequirement,
) -> tuple[tuple[EvidenceItem, ...], ToolCall]:
    snippets: list[EvidenceItem] = []
    if requirement.risk_hints:
        snippets.append(
            EvidenceItem(
                source="qa_defect_heuristic",
                title="Risk-heavy scenarios imply regression expansion",
                snippet="Risk hints outnumber acceptance criteria, so regression and negative-path coverage may be incomplete.",
                ref="qa-defect-heuristic:regression-gap",
            )
        )
    if requirement.scenarios and not requirement.acceptance_criteria:
        snippets.append(
            EvidenceItem(
                source="qa_defect_heuristic",
                title="Scenario coverage lacks pass/fail oracle",
                snippet="User scenarios exist without acceptance criteria, leaving QA without deterministic validation steps.",
                ref="qa-defect-heuristic:test-oracle-gap",
            )
        )
    return (
        tuple(snippets),
        ToolCall(
            tool_name="qa.defect_heuristic",
            status="completed",
            reviewer="qa",
            query=requirement.summary,
            input_summary=requirement.summary,
            output_summary=f"hits={len(snippets)}",
            evidence_count=len(snippets),
            metadata={"adapter": "local_heuristic"},
        ),
    )


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
    heuristic_evidence, tool_call = _defect_heuristic(requirement)
    memory_evidence = build_memory_evidence(memory_context)
    memory_notes = build_memory_notes(memory_context, memory_mode=memory_mode)
    evidence = tuple([*memory_evidence, *heuristic_evidence])

    if not requirement.acceptance_criteria:
        findings.append(
            ReviewFinding(
                title="Test oracle is missing",
                detail="QA cannot derive pass/fail expectations because acceptance criteria are absent.",
                severity="high",
                category="testability",
                reviewer="qa",
                evidence=evidence[:1],
            )
        )

    if requirement.risk_hints and len(requirement.acceptance_criteria) < len(
        requirement.risk_hints
    ):
        findings.append(
            ReviewFinding(
                title="Edge-case coverage looks thin",
                detail="The PRD mentions risk-heavy behavior but acceptance coverage is thinner than the risk surface.",
                severity="medium",
                category="testability",
                reviewer="qa",
                evidence=evidence[:2],
            )
        )

    if requirement.risk_hints:
        risks.append(
            RiskItem(
                title="Regression scope may be underestimated",
                detail="Risk-related scenarios imply extra negative-path and regression testing.",
                severity="medium",
                category="quality",
                mitigation="Add explicit failure-path, rollback, and regression checks to the acceptance criteria.",
                reviewer="qa",
            )
        )

    if requirement.scenarios and not requirement.acceptance_criteria:
        open_questions.append(
            "How should each scenario be validated end-to-end in UAT or regression testing?"
        )

    ambiguity_type = (
        "missing_test_oracle" if not requirement.acceptance_criteria else ""
    )
    clarification_question = (
        "What are the explicit pass/fail checks, rollback expectations, and negative cases for QA validation?"
        if ambiguity_type
        else ""
    )
    reviewer_status_detail = f"QA review completed with {len(findings)} findings, {len(risks)} risks, and {len(evidence)} heuristic evidence hits."

    await asyncio.sleep(0)
    return build_reviewer_result(
        reviewer="qa",
        config=resolved,
        findings=findings,
        open_questions=open_questions,
        risk_items=risks,
        evidence=evidence,
        tool_calls=(tool_call,),
        summary="QA review completed against acceptance and regression coverage.",
        ambiguity_type=ambiguity_type,
        clarification_question=clarification_question,
        reviewer_status_detail=reviewer_status_detail,
        notes=(
            "QA evidence currently comes from local heuristics; external defect systems remain optional stubs.",
            *memory_notes,
        ),
    )
