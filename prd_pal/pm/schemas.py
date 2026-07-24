"""PM domain schemas for feedback, insight, opportunity, and PRD draft."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from prd_pal.schemas.base import AgentSchemaModel, SafeStrList

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class PipelineStage(StrEnum):
    """Deterministic PM pipeline stages."""

    capture = "capture"
    cluster = "cluster"
    opportunity = "opportunity"
    prd = "prd"
    review = "review"
    complete = "complete"


class PipelineStatus(StrEnum):
    """Lifecycle status for one PM pipeline run."""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class FeedbackItem(AgentSchemaModel):
    """One captured piece of raw PM input (feedback, notes, or a short request)."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = ""
    product_hint: str = ""
    created_at: str = ""
    source_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InsightCluster(AgentSchemaModel):
    """One clustered insight distilled from multiple feedback items."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = ""
    theme: str = ""
    feedback_ids: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    evidence_quotes: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityBrief(AgentSchemaModel):
    """An evaluable product opportunity derived from one or more insights."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    problem: str = ""
    users: str = ""
    value: str = ""
    constraints: SafeStrList = Field(default_factory=list)
    open_questions: SafeStrList = Field(default_factory=list)
    insight_ids: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    evidence_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PRDDraft(AgentSchemaModel):
    """PRD draft generated from an opportunity, optionally linked to a review run."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    opportunity_id: str = ""
    goals: SafeStrList = Field(default_factory=list)
    in_scope: SafeStrList = Field(default_factory=list)
    out_of_scope: SafeStrList = Field(default_factory=list)
    acceptance_criteria: SafeStrList = Field(default_factory=list)
    risks: SafeStrList = Field(default_factory=list)
    success_metrics: SafeStrList = Field(default_factory=list)
    review_run_id: str = ""
    source_refs: SafeStrList = Field(default_factory=list)
    evidence_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineRunRecord(AgentSchemaModel):
    """Persisted record for one end-to-end PM pipeline execution."""

    id: str = Field(min_length=1)
    status: PipelineStatus = PipelineStatus.pending
    stage: PipelineStage = PipelineStage.capture
    product_hint: str = ""
    feedback_ids: SafeStrList = Field(default_factory=list)
    insight_ids: SafeStrList = Field(default_factory=list)
    opportunity_id: str = ""
    prd_id: str = ""
    review_run_id: str = ""
    error_message: str = ""
    created_at: str = ""
    updated_at: str = ""
    source_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InsightItemOutput(AgentSchemaModel):
    """Single insight row inside LLM clustering output."""

    title: str = Field(min_length=1)
    summary: str = ""
    theme: str = ""
    feedback_ids: SafeStrList = Field(default_factory=list)
    evidence_quotes: SafeStrList = Field(default_factory=list)


class InsightExtractionOutput(AgentSchemaModel):
    """Structured LLM output for feedback clustering."""

    insights: list[InsightItemOutput] = Field(default_factory=list)
    notes: str = ""


class OpportunityBriefOutput(AgentSchemaModel):
    """Structured LLM output for opportunity brief generation."""

    title: str = Field(min_length=1)
    problem: str = ""
    users: str = ""
    value: str = ""
    constraints: SafeStrList = Field(default_factory=list)
    open_questions: SafeStrList = Field(default_factory=list)


class PRDDraftOutput(AgentSchemaModel):
    """Structured LLM output for PRD draft generation."""

    title: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    goals: SafeStrList = Field(default_factory=list)
    in_scope: SafeStrList = Field(default_factory=list)
    out_of_scope: SafeStrList = Field(default_factory=list)
    acceptance_criteria: SafeStrList = Field(default_factory=list)
    risks: SafeStrList = Field(default_factory=list)
    success_metrics: SafeStrList = Field(default_factory=list)


def validate_insight_extraction_output(data: dict[str, Any]) -> InsightExtractionOutput:
    return InsightExtractionOutput.model_validate(data)


def validate_opportunity_brief_output(data: dict[str, Any]) -> OpportunityBriefOutput:
    return OpportunityBriefOutput.model_validate(data)


def validate_prd_draft_output(data: dict[str, Any]) -> PRDDraftOutput:
    return PRDDraftOutput.model_validate(data)
