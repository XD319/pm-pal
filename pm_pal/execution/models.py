"""Core execution orchestration models."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from pm_pal.schemas.base import AgentSchemaModel

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class ExecutionMode(StrEnum):
    """How much autonomy an executor has during execution."""

    agent_auto = "agent_auto"
    agent_assisted = "agent_assisted"
    human_only = "human_only"


class ExecutionTaskStatus(StrEnum):
    """Lifecycle states for one execution task."""

    pending = "pending"
    assigned = "assigned"
    in_progress = "in_progress"
    waiting_review = "waiting_review"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ExecutionEvent(AgentSchemaModel):
    """One state-changing or checkpoint event during task execution."""

    event_id: str
    timestamp: str
    event_type: str
    detail: str = ""
    actor: str = ""


class ExecutionTask(AgentSchemaModel):
    """Persisted execution task derived from one approved delivery bundle."""

    task_id: str
    bundle_id: str
    source_pack_type: str
    executor_type: str
    execution_mode: ExecutionMode
    status: ExecutionTaskStatus = ExecutionTaskStatus.pending
    created_at: str
    updated_at: str
    assigned_to: str = ""
    execution_log: list[ExecutionEvent] = Field(default_factory=list)
    result_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceLink(AgentSchemaModel):
    """One end-to-end traceability edge from requirement to execution."""

    requirement_id: str
    review_item_id: str = ""
    plan_task_id: str = ""
    test_item_id: str = ""
    execution_task_id: str = ""
    link_type: str = "partial"
