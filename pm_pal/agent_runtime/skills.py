from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SkillKind(StrEnum):
    insight = "insight"
    authoring = "authoring"
    execution = "execution"


class Skill(BaseModel):
    id: str
    name: str
    kind: SkillKind
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_connectors: list[str] = Field(default_factory=list)
    may_write_external: bool = False


def default_skill_registry() -> dict[str, Skill]:
    skills = [
        Skill(
            id="synthesize_evidence",
            name="Synthesize evidence",
            kind=SkillKind.insight,
            description="Cluster authorised local, Feishu, or Notion materials into evidence-backed findings.",
        ),
        Skill(
            id="analyze_competition",
            name="Analyze competitors",
            kind=SkillKind.insight,
            description="Compare supplied competitor material and identify product implications.",
        ),
        Skill(
            id="draft_prd",
            name="Draft PRD",
            kind=SkillKind.authoring,
            description="Create a traceable PRD draft from authorised evidence.",
        ),
        Skill(
            id="draft_briefing",
            name="Draft update",
            kind=SkillKind.authoring,
            description="Write a concise product update, proposal, or retrospective.",
        ),
        Skill(
            id="plan_work",
            name="Plan work",
            kind=SkillKind.execution,
            description="Break an objective into an owned, ordered task plan.",
        ),
        Skill(
            id="propose_external_write",
            name="Propose external write",
            kind=SkillKind.execution,
            description="Prepare a Feishu or Notion write as a user-confirmed action proposal.",
            required_connectors=["feishu|notion"],
            may_write_external=True,
        ),
    ]
    return {skill.id: skill for skill in skills}
