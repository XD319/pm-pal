"""Schemas for constrained roadmap generation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import AgentSchemaModel


class RoadmapItem(AgentSchemaModel):
    """One prioritized roadmap unit derived from tasks and constraints."""

    id: str
    title: str = ""
    priority_score: float = 0.0
    effort_score: float = 0.0
    risk_score: float = 0.0
    dependency_ids: list[str] = Field(default_factory=list)
    target_window: str = "later"
    why_now: str = ""
    why_later: str = ""
    de_scope_candidate: bool = False


class RoadmapOutput(AgentSchemaModel):
    """Stable output contract for roadmap generation."""

    version: str = "v1"
    roadmap_items: list[RoadmapItem] = Field(default_factory=list)


class RoadmapDiffItem(AgentSchemaModel):
    """One diff record between roadmap versions."""

    id: str
    change_type: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    changed_fields: list[str] = Field(default_factory=list)


class RoadmapDiffOutput(AgentSchemaModel):
    """Structured comparison result for roadmap versions."""

    from_version: str = "v1"
    to_version: str = "v2"
    added: list[RoadmapDiffItem] = Field(default_factory=list)
    removed: list[RoadmapDiffItem] = Field(default_factory=list)
    changed: list[RoadmapDiffItem] = Field(default_factory=list)
    unchanged_count: int = 0
