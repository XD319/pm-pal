"""Delivery conflict arbitration for high-severity review conflicts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DeliveryReviewerResolution:
    recommendation: str
    reasoning: str
    decided_by: str = "delivery_reviewer.rules"
    needs_human: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def arbitrate_conflict(
    conflict: dict[str, Any],
    *,
    product_summary: str = "",
    engineering_summary: str = "",
    qa_summary: str = "",
    security_summary: str = "",
) -> DeliveryReviewerResolution:
    conflict_type = str(conflict.get("type", "") or "").strip().lower()
    topic = str(conflict.get("topic", "") or "the shared issue").strip()
    finding_severity = str(conflict.get("finding_severity", "") or "").strip().lower()
    risk_severity = str(conflict.get("risk_severity", "") or "").strip().lower()

    perspectives = {
        "product": product_summary,
        "engineering": engineering_summary,
        "qa": qa_summary,
        "security": security_summary,
    }
    active_perspectives = [
        f"{name}: {summary.strip()}"
        for name, summary in perspectives.items()
        if isinstance(summary, str) and summary.strip()
    ]
    perspective_note = " ".join(active_perspectives).strip()

    if conflict_type == "severity_mismatch":
        target_severity = (
            "high"
            if "high" in {finding_severity, risk_severity}
            else (risk_severity or finding_severity or "medium")
        )
        reasoning = (
            f"Conflicting severity labels were detected for {topic}. "
            f"Delivery arbitration keeps the higher severity of {target_severity} so downstream planning does not understate release risk."
        )
        if perspective_note:
            reasoning = f"{reasoning} Reviewer context: {perspective_note}"
        return DeliveryReviewerResolution(
            recommendation=f"Treat '{topic}' as {target_severity} severity until reviewers align on one shared label.",
            reasoning=reasoning,
            needs_human=False,
        )

    if conflict_type == "release_ok_vs_approval_blocker":
        reasoning = (
            "Security and release-readiness signals disagree. "
            "Delivery arbitration keeps the approval gate in place because release blockers take precedence over optimistic readiness signals."
        )
        if perspective_note:
            reasoning = f"{reasoning} Reviewer context: {perspective_note}"
        return DeliveryReviewerResolution(
            recommendation="Keep the release blocked until the required approval gate is explicitly satisfied and recorded.",
            reasoning=reasoning,
            needs_human=False,
        )

    if conflict_type == "acceptance_complete_vs_testability_gap":
        reasoning = (
            "Product and QA disagree on whether the requirement is test-ready. "
            "A delivery decision cannot safely clear this without updated acceptance criteria and QA verification."
        )
        if perspective_note:
            reasoning = f"{reasoning} Reviewer context: {perspective_note}"
        return DeliveryReviewerResolution(
            recommendation="Route the conflict back to product and QA to add pass/fail criteria before implementation proceeds.",
            reasoning=reasoning,
            needs_human=True,
        )

    if conflict_type == "scope_inclusion_vs_dependency_blocker":
        reasoning = (
            "Product scope confidence conflicts with engineering dependency risk. "
            "Delivery arbitration cannot infer sequencing, ownership, or resourcing tradeoffs from summaries alone."
        )
        if perspective_note:
            reasoning = f"{reasoning} Reviewer context: {perspective_note}"
        return DeliveryReviewerResolution(
            recommendation="Escalate to product and engineering leads to confirm scope ownership, dependency sequencing, and release impact.",
            reasoning=reasoning,
            needs_human=True,
        )

    reasoning = (
        "The conflict does not match a rule with enough confidence to auto-resolve it."
    )
    if perspective_note:
        reasoning = f"{reasoning} Reviewer context: {perspective_note}"
    return DeliveryReviewerResolution(
        recommendation="Route this conflict to a human delivery reviewer for a final decision.",
        reasoning=reasoning,
        needs_human=True,
    )
