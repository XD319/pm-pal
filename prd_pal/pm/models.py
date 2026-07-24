"""Long-lived PM context models beyond the MVP pipeline objects."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from prd_pal.schemas.base import AgentSchemaModel, SafeStrList

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class RoadmapHorizon(StrEnum):
    now = "now"
    next = "next"
    later = "later"


class DecisionStatus(StrEnum):
    proposed = "proposed"
    approved = "approved"
    deferred = "deferred"
    rejected = "rejected"


class ProductContext(AgentSchemaModel):
    """Persistent product memory used across PM sessions."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    module: str = ""
    target_users: str = ""
    business_goals: SafeStrList = Field(default_factory=list)
    constraints: SafeStrList = Field(default_factory=list)
    summary: str = ""
    created_at: str = ""
    updated_at: str = ""
    source_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Decision(AgentSchemaModel):
    """A durable product decision with evidence references."""

    id: str = Field(min_length=1)
    product_id: str = ""
    title: str = Field(min_length=1)
    status: DecisionStatus = DecisionStatus.proposed
    summary: str = ""
    rationale: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoadmapItem(AgentSchemaModel):
    """Minimal Now/Next/Later roadmap entry."""

    id: str = Field(min_length=1)
    product_id: str = ""
    title: str = Field(min_length=1)
    horizon: RoadmapHorizon = RoadmapHorizon.next
    opportunity_id: str = ""
    prd_id: str = ""
    score: float = 0.0
    summary: str = ""
    source_refs: SafeStrList = Field(default_factory=list)
    evidence_refs: SafeStrList = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceLink(AgentSchemaModel):
    """Directed evidence link between PM objects."""

    id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    relation: str = "derived_from"
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
