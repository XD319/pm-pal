"""Shared result models for heuristic reviewer agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source: str
    title: str
    snippet: str = ""
    ref: str = ""
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool_name: str
    status: str
    reviewer: str = ""
    query: str = ""
    input_summary: str = ""
    output_summary: str = ""
    evidence_count: int = 0
    error_message: str = ""
    degraded_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    title: str
    detail: str
    severity: str = "medium"
    category: str = "general"
    requirement_refs: tuple[str, ...] = ()
    reviewer: str = ""
    suggested_action: str = ""
    assignee: str = ""
    evidence: tuple[EvidenceItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True, slots=True)
class RiskItem:
    title: str
    detail: str
    severity: str = "medium"
    category: str = "delivery"
    mitigation: str = ""
    reviewer: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReviewerResult:
    reviewer: str
    findings: tuple[ReviewFinding, ...] = ()
    open_questions: tuple[str, ...] = ()
    risk_items: tuple[RiskItem, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    summary: str = ""
    status: str = "completed"
    error_message: str = ""
    ambiguity_type: str = ""
    clarification_question: str = ""
    reviewer_status_detail: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewer": self.reviewer,
            "findings": [item.to_dict() for item in self.findings],
            "open_questions": list(self.open_questions),
            "risk_items": [item.to_dict() for item in self.risk_items],
            "evidence": [item.to_dict() for item in self.evidence],
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "summary": self.summary,
            "status": self.status,
            "error_message": self.error_message,
            "ambiguity_type": self.ambiguity_type,
            "clarification_question": self.clarification_question,
            "reviewer_status_detail": self.reviewer_status_detail,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ReviewerConfig:
    top_n_findings: int = 3
    top_n_questions: int = 3
    top_n_risks: int = 3


def normalize_severity(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"critical", "blocker", "sev1", "high"}:
        return "high"
    if normalized in {"low", "minor", "sev3"}:
        return "low"
    return "medium"


def limit_items(items: list[Any], limit: int) -> tuple[Any, ...]:
    return tuple(items[: max(int(limit or 0), 0)])


def build_reviewer_result(
    *,
    reviewer: str,
    config: ReviewerConfig,
    findings: list[ReviewFinding] | tuple[ReviewFinding, ...] = (),
    open_questions: list[str] | tuple[str, ...] = (),
    risk_items: list[RiskItem] | tuple[RiskItem, ...] = (),
    evidence: list[EvidenceItem] | tuple[EvidenceItem, ...] = (),
    tool_calls: list[ToolCall] | tuple[ToolCall, ...] = (),
    summary: str = "",
    ambiguity_type: str = "",
    clarification_question: str = "",
    reviewer_status_detail: str = "",
    notes: list[str] | tuple[str, ...] = (),
) -> ReviewerResult:
    """Build a reviewer result with consistent limits and tuple coercion."""

    return ReviewerResult(
        reviewer=reviewer,
        findings=limit_items(list(findings), config.top_n_findings),
        open_questions=limit_items(list(open_questions), config.top_n_questions),
        risk_items=limit_items(list(risk_items), config.top_n_risks),
        evidence=tuple(evidence),
        tool_calls=tuple(tool_calls),
        summary=summary,
        ambiguity_type=ambiguity_type,
        clarification_question=clarification_question,
        reviewer_status_detail=reviewer_status_detail,
        notes=tuple(notes),
    )
