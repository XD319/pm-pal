"""Structured memory models for PRD review reuse."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from pm_pal.schemas.base import AgentSchemaModel, SafeStrList


class MemoryType(str, Enum):
    team_rule = "team_rule"
    risk_pattern = "risk_pattern"
    clarification_fact = "clarification_fact"
    review_case = "review_case"


class MemoryScopeLevel(str, Enum):
    project = "project"
    team = "team"
    global_ = "global"


class MemoryEvidence(AgentSchemaModel):
    kind: str = ""
    reference: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryApplicability(AgentSchemaModel):
    summary: str = ""
    conditions: SafeStrList = Field(default_factory=list)
    signals: SafeStrList = Field(default_factory=list)


class MemoryScope(AgentSchemaModel):
    level: MemoryScopeLevel
    team_id: str = ""
    project_id: str = ""
    requirement_type: SafeStrList = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self) -> "MemoryScope":
        if self.level == MemoryScopeLevel.team and not self.team_id.strip():
            raise ValueError("team scope requires team_id")
        if self.level == MemoryScopeLevel.project and not self.project_id.strip():
            raise ValueError("project scope requires project_id")
        if self.level == MemoryScopeLevel.global_:
            self.team_id = ""
            self.project_id = ""
        return self


class MemoryRecord(AgentSchemaModel):
    memory_id: str
    memory_type: MemoryType
    title: str
    summary: str
    content: str
    scope: MemoryScope
    applicability: MemoryApplicability = Field(default_factory=MemoryApplicability)
    evidence: list[MemoryEvidence] = Field(default_factory=list)
    confidence: float = 0.0
    reuse_score: float = 0.0
    expiry_hint: str = ""
    tags: SafeStrList = Field(default_factory=list)
    do_not_overapply: str = ""
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""


class MemoryQuery(AgentSchemaModel):
    scope_level: MemoryScopeLevel | None = None
    team_id: str = ""
    project_id: str = ""
    requirement_type: str = ""
    memory_type: MemoryType | None = None
    tag: str = ""
