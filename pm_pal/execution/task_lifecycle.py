"""Execution task lifecycle state machine."""

from __future__ import annotations

from pm_pal.execution.models import (
    ExecutionEvent,
    ExecutionMode,
    ExecutionTask,
    ExecutionTaskStatus,
)
from pm_pal.utils.time import utc_now_iso as _utc_now_iso

VALID_TASK_TRANSITIONS: dict[ExecutionTaskStatus, set[ExecutionTaskStatus]] = {
    ExecutionTaskStatus.pending: {
        ExecutionTaskStatus.assigned,
        ExecutionTaskStatus.cancelled,
    },
    ExecutionTaskStatus.assigned: {
        ExecutionTaskStatus.in_progress,
        ExecutionTaskStatus.cancelled,
    },
    ExecutionTaskStatus.in_progress: {
        ExecutionTaskStatus.waiting_review,
        ExecutionTaskStatus.completed,
        ExecutionTaskStatus.failed,
        ExecutionTaskStatus.cancelled,
    },
    ExecutionTaskStatus.waiting_review: {
        ExecutionTaskStatus.in_progress,
        ExecutionTaskStatus.failed,
        ExecutionTaskStatus.cancelled,
    },
    ExecutionTaskStatus.completed: set(),
    ExecutionTaskStatus.failed: set(),
    ExecutionTaskStatus.cancelled: set(),
}


class InvalidExecutionTaskTransitionError(ValueError):
    """Raised when a state transition is not allowed."""


def _transition(
    task: ExecutionTask,
    *,
    to_status: ExecutionTaskStatus,
    actor: str,
    event_type: str,
    detail: str = "",
    assigned_to: str | None = None,
    result_summary: str | None = None,
) -> ExecutionTask:
    if to_status not in VALID_TASK_TRANSITIONS.get(task.status, set()):
        raise InvalidExecutionTaskTransitionError(
            f"cannot transition from {task.status} to {to_status}"
        )

    now = _utc_now_iso()
    log = list(task.execution_log)
    log.append(
        ExecutionEvent(
            event_id=f"{task.task_id}:{event_type}:{len(log) + 1}",
            timestamp=now,
            event_type=event_type,
            detail=detail,
            actor=actor,
        )
    )
    payload = task.model_dump(mode="python")
    payload["status"] = to_status
    payload["updated_at"] = now
    payload["execution_log"] = log
    if assigned_to is not None:
        payload["assigned_to"] = assigned_to
    if result_summary is not None:
        payload["result_summary"] = result_summary
    return ExecutionTask.model_validate(payload)


def assign_task(task: ExecutionTask, executor: str, actor: str) -> ExecutionTask:
    return _transition(
        task,
        to_status=ExecutionTaskStatus.assigned,
        actor=actor,
        event_type="assigned",
        detail=f"executor={executor}",
        assigned_to=executor,
    )


def start_task(task: ExecutionTask, actor: str) -> ExecutionTask:
    return _transition(
        task,
        to_status=ExecutionTaskStatus.in_progress,
        actor=actor,
        event_type="started",
        detail="execution started",
    )


def request_review(task: ExecutionTask, actor: str, detail: str) -> ExecutionTask:
    if task.execution_mode != ExecutionMode.agent_assisted:
        raise InvalidExecutionTaskTransitionError(
            "request_review is only valid for agent_assisted tasks"
        )
    return _transition(
        task,
        to_status=ExecutionTaskStatus.waiting_review,
        actor=actor,
        event_type="checkpoint",
        detail=detail,
    )


def complete_task(
    task: ExecutionTask, actor: str, result_summary: str
) -> ExecutionTask:
    return _transition(
        task,
        to_status=ExecutionTaskStatus.completed,
        actor=actor,
        event_type="completed",
        detail=result_summary,
        result_summary=result_summary,
    )


def fail_task(task: ExecutionTask, actor: str, reason: str) -> ExecutionTask:
    return _transition(
        task,
        to_status=ExecutionTaskStatus.failed,
        actor=actor,
        event_type="failed",
        detail=reason,
        result_summary=reason,
    )


def cancel_task(task: ExecutionTask, actor: str, reason: str) -> ExecutionTask:
    return _transition(
        task,
        to_status=ExecutionTaskStatus.cancelled,
        actor=actor,
        event_type="cancelled",
        detail=reason,
    )
