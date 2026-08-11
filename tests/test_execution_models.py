from __future__ import annotations

import json

from pm_pal.execution import (
    ExecutionEvent,
    ExecutionMode,
    ExecutionTask,
    ExecutionTaskStatus,
    TraceLink,
)


def test_execution_task_model_instantiates_and_serializes() -> None:
    task = ExecutionTask(
        task_id="bundle-1:implementation_pack",
        bundle_id="bundle-1",
        source_pack_type="implementation_pack",
        executor_type="codex",
        execution_mode=ExecutionMode.agent_assisted,
        status=ExecutionTaskStatus.pending,
        created_at="2026-03-07T12:00:00+00:00",
        updated_at="2026-03-07T12:00:00+00:00",
        execution_log=[
            ExecutionEvent(
                event_id="evt-1",
                timestamp="2026-03-07T12:00:00+00:00",
                event_type="created",
                detail="created",
                actor="router",
            )
        ],
    )

    payload = json.loads(task.model_dump_json())
    assert payload["execution_mode"] == "agent_assisted"
    assert payload["status"] == "pending"
    assert payload["execution_log"][0]["event_type"] == "created"


def test_execution_enum_values_are_stable() -> None:
    assert ExecutionMode.agent_auto.value == "agent_auto"
    assert ExecutionMode.agent_assisted.value == "agent_assisted"
    assert ExecutionMode.human_only.value == "human_only"
    assert ExecutionTaskStatus.waiting_review.value == "waiting_review"


def test_trace_link_defaults_are_serializable() -> None:
    link = TraceLink(requirement_id="REQ-001")

    assert link.link_type == "partial"
    assert json.loads(link.model_dump_json())["requirement_id"] == "REQ-001"
