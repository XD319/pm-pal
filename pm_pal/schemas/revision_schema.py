"""Schema for revision agent outputs."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import AgentSchemaModel, SafeStrList


class RevisionAgentOutput(AgentSchemaModel):
    """Structured output returned by the revision generation agent."""

    revised_prd_markdown: str = ""
    sources_used: SafeStrList = Field(default_factory=list)
    major_changes: SafeStrList = Field(default_factory=list)
    rationale: str = ""
    unadopted_review_suggestions: SafeStrList = Field(default_factory=list)
    pending_questions: SafeStrList = Field(default_factory=list)
    user_direct_requirements_applied: SafeStrList = Field(default_factory=list)
    meeting_notes_resolutions: SafeStrList = Field(default_factory=list)
    meeting_notes_change_points: SafeStrList = Field(default_factory=list)
    meeting_notes_pending_confirmations: SafeStrList = Field(default_factory=list)


def validate_revision_output(data: dict[str, Any]) -> RevisionAgentOutput:
    return RevisionAgentOutput.model_validate(data)
