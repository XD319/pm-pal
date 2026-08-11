"""Stable contracts between product decisions and the legacy review kernel."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from pm_pal.schemas.base import AgentSchemaModel, SafeStrList


class QualityGateDecision(StrEnum):
    pass_ = "pass"
    needs_revision = "needs_revision"
    blocked = "blocked"


class QualityAssessmentRequest(AgentSchemaModel):
    """A versioned PRD and its decision context submitted for quality review."""

    prd_version_id: str = Field(min_length=1)
    prd_text: str = Field(min_length=1)
    opportunity_id: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    quality_policy: str = "default"
    metadata: dict[str, Any] = Field(default_factory=dict)


class QualityAssessment(AgentSchemaModel):
    """Quality-engine result stored as an artifact attached to a PRD version."""

    id: str = Field(min_length=1)
    prd_version_id: str = Field(min_length=1)
    review_run_id: str = ""
    decision: QualityGateDecision
    quality_score: float = 0.0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    clarification_items: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: SafeStrList = Field(default_factory=list)
    policy: str = "default"
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
