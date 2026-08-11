"""Security-focused heuristic reviewer."""

from __future__ import annotations

import asyncio
import re

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


_SECURITY_TOPIC_RE = re.compile(
    r"\b(security|privacy|compliance|audit|pii|payment|encryption|sso|oauth)\b",
    re.IGNORECASE,
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
    memory_evidence = build_memory_evidence(memory_context)
    memory_notes = build_memory_notes(memory_context, memory_mode=memory_mode)

    joined_risk_text = " ".join(requirement.risk_hints)
    sensitive_scope = bool(_SECURITY_TOPIC_RE.search(requirement.source_text)) or bool(
        _SECURITY_TOPIC_RE.search(joined_risk_text)
    )
    query = " ".join(
        [
            requirement.summary,
            joined_risk_text,
            requirement.source_text[:240],
            reviewer_input[:200],
        ]
    ).strip()
    toolbox = get_reviewer_toolbox()
    risk_tool = toolbox.local_risk_catalog.run(reviewer="security", query=query)
    cve_tool = toolbox.cve_lookup.run(reviewer="security", query=query)
    evidence = tuple(
        [*memory_evidence, *risk_tool.evidence[:3], *cve_tool.evidence[:2]]
    )
    tool_calls = tuple(
        item for item in (risk_tool.tool_call, cve_tool.tool_call) if item is not None
    )

    if sensitive_scope and not requirement.risk_hints:
        findings.append(
            ReviewFinding(
                title="Sensitive scope lacks explicit controls",
                detail="The PRD references security-sensitive behavior but does not state concrete security or compliance constraints.",
                severity="high",
                category="security",
                reviewer="security",
                evidence=evidence[:2],
            )
        )

    if sensitive_scope:
        risks.append(
            RiskItem(
                title="Security review gate required",
                detail="Sensitive flows or data handling are implied and should be reviewed before release approval.",
                severity="high",
                category="security",
                mitigation="Add logging, access control, data handling, and rollback expectations to the PRD.",
                reviewer="security",
            )
        )

    if sensitive_scope and not requirement.acceptance_criteria:
        open_questions.append(
            "Which explicit security acceptance checks must pass before release?"
        )

    ambiguity_type = (
        "missing_security_controls"
        if sensitive_scope and not requirement.risk_hints
        else ""
    )
    clarification_question = (
        "Which concrete controls, audit requirements, and release gates are mandatory for this scope?"
        if sensitive_scope
        else ""
    )
    reviewer_status_detail = f"Security review completed with {len(findings)} findings, {len(risks)} risks, and {len(evidence)} evidence hits."

    await asyncio.sleep(0)
    return build_reviewer_result(
        reviewer="security",
        config=resolved,
        findings=findings,
        open_questions=open_questions,
        risk_items=risks,
        evidence=evidence,
        tool_calls=tool_calls,
        summary="Security review completed against sensitive data and release controls.",
        ambiguity_type=ambiguity_type,
        clarification_question=clarification_question,
        reviewer_status_detail=reviewer_status_detail,
        notes=(
            "Security tool hooks include local risk evidence and optional CVE lookup adapter stubs.",
            *memory_notes,
        ),
    )
