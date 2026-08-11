from __future__ import annotations

import pytest

from pm_pal.execution import (
    ExecutionMode,
    ExecutionTask,
    ExecutionTaskStatus,
    InvalidExecutionTaskTransitionError,
    assign_task,
    cancel_task,
    complete_task,
    fail_task,
    request_review,
    start_task,
)


def _task(
    *,
    mode: ExecutionMode = ExecutionMode.agent_assisted,
    status: ExecutionTaskStatus = ExecutionTaskStatus.pending,
) -> ExecutionTask:
    return ExecutionTask(
        task_id="bundle-1:implementation_pack",
        bundle_id="bundle-1",
        source_pack_type="implementation_pack",
        executor_type="codex",
        execution_mode=mode,
        status=status,
        created_at="2026-03-07T12:00:00+00:00",
        updated_at="2026-03-07T12:00:00+00:00",
    )


def test_task_lifecycle_supports_happy_path() -> None:
    task = assign_task(_task(), executor="codex-worker-1", actor="router")
    task = start_task(task, actor="codex-worker-1")
    task = request_review(task, actor="codex-worker-1", detail="checkpoint ready")
    task = start_task(task, actor="reviewer")
    task = complete_task(
        task, actor="codex-worker-1", result_summary="implemented and validated"
    )

    assert task.status == "completed"
    assert task.assigned_to == "codex-worker-1"
    assert task.result_summary == "implemented and validated"
    assert [event.event_type for event in task.execution_log] == [
        "assigned",
        "started",
        "checkpoint",
        "started",
        "completed",
    ]


def test_task_lifecycle_supports_failure_and_cancel_paths() -> None:
    failed = fail_task(
        start_task(
            assign_task(_task(), executor="codex", actor="router"), actor="codex"
        ),
        actor="codex",
        reason="lint failed",
    )
    cancelled = cancel_task(
        assign_task(_task(), executor="codex", actor="router"),
        actor="lead",
        reason="scope changed",
    )

    assert failed.status == "failed"
    assert failed.result_summary == "lint failed"
    assert cancelled.status == "cancelled"


def test_task_lifecycle_rejects_invalid_transition() -> None:
    with pytest.raises(InvalidExecutionTaskTransitionError):
        start_task(_task(), actor="codex")


def test_request_review_requires_agent_assisted_mode() -> None:
    task = start_task(
        assign_task(
            _task(mode=ExecutionMode.agent_auto), executor="codex", actor="router"
        ),
        actor="codex",
    )

    with pytest.raises(InvalidExecutionTaskTransitionError):
        request_review(task, actor="codex", detail="manual gate")


def test_assign_task_records_executor_in_event_detail() -> None:
    task = assign_task(_task(), executor="codex-worker-1", actor="router")

    assert task.execution_log[-1].event_type == "assigned"
    assert task.execution_log[-1].detail == "executor=codex-worker-1"
    assert task.execution_log[-1].actor == "router"
