"""Schema for the **planner** agent output.

Field names match the JSON contract defined in ``prompts.PLANNER_SYSTEM_PROMPT``
and consumed by ``agents/planner_agent.py``.
"""

from typing import Any

from pydantic import ConfigDict, Field

from .base import AgentSchemaModel, ID, SafeStrList


# ── sub-models ────────────────────────────────────────────────────────────


class Task(AgentSchemaModel):
    """An atomic work item in the delivery plan."""

    id: ID
    title: str = ""
    owner: str = ""
    requirement_ids: list[ID] = Field(default_factory=list)
    depends_on: SafeStrList = Field(default_factory=list)
    estimate_days: float = 0


class Milestone(AgentSchemaModel):
    """A delivery checkpoint grouping related tasks."""

    id: ID
    title: str = ""
    includes: SafeStrList = Field(default_factory=list)
    target_days: float = 0


class Dependency(AgentSchemaModel):
    """An explicit edge between two tasks (``from`` → ``to``)."""

    model_config = ConfigDict(populate_by_name=True)

    from_task: ID = Field(alias="from")
    to: ID
    type: str = "blocked_by"


class Estimation(AgentSchemaModel):
    """Overall effort summary."""

    total_days: float = 0
    buffer_days: float = 0


# ── top-level output ──────────────────────────────────────────────────────


class PlannerOutput(AgentSchemaModel):
    """Wrapper returned by the planner LLM call.

    ``{"tasks": [...], "milestones": [...], "dependencies": [...], "estimation": {...}}``
    """

    tasks: list[Task] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    estimation: Estimation = Field(default_factory=Estimation)


def validate_planner_output(data: dict[str, Any]) -> PlannerOutput:
    """Validate and coerce a raw dict into a :class:`PlannerOutput`."""
    return PlannerOutput.model_validate(data)


# ── minimal example ───────────────────────────────────────────────────────
# validate_planner_output({
#     "tasks": [
#         {"id": "T-1", "title": "Design API", "owner": "BE",
#          "requirement_ids": ["REQ-001"], "depends_on": [], "estimate_days": 3}
#     ],
#     "milestones": [
#         {"id": "M-1", "title": "API Ready", "includes": ["T-1"],
#          "target_days": 5}
#     ],
#     "dependencies": [
#         {"from": "T-2", "to": "T-1", "type": "blocked_by"}
#     ],
#     "estimation": {"total_days": 10, "buffer_days": 2}
# })
