"""Notification event and dispatch models."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from pm_pal.schemas.base import AgentSchemaModel

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        def __str__(self) -> str:
            return str(self.value)


class NotificationType(StrEnum):
    approval_requested = "approval_requested"
    blocked_by_risk = "blocked_by_risk"
    executor_handoff_created = "executor_handoff_created"
    execution_completed = "execution_completed"
    execution_failed = "execution_failed"
    review_submitted = "review_submitted"
    review_running = "review_running"
    review_completed = "review_completed"
    review_failed = "review_failed"
    clarification_required = "clarification_required"


class DispatchStatus(StrEnum):
    dispatched = "dispatched"
    failed = "failed"


NotificationStatus = DispatchStatus


class NotificationEvent(AgentSchemaModel):
    notification_id: str
    event_type: NotificationType
    run_id: str = ""
    bundle_id: str = ""
    task_id: str = ""
    title: str
    summary: str = ""
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True

    @property
    def notification_type(self) -> NotificationType:
        return self.event_type


class NotificationDispatchRecord(AgentSchemaModel):
    notification_id: str
    event_type: NotificationType
    channel: str
    payload: dict[str, Any] = Field(default_factory=dict)
    dispatch_status: DispatchStatus
    created_at: str
    dispatched_at: str = ""
    error_message: str = ""
    run_id: str = ""
    bundle_id: str = ""
    task_id: str = ""
    title: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    delivery_metadata: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True

    @property
    def notification_type(self) -> NotificationType:
        return self.event_type


class NotificationDispatchResult(AgentSchemaModel):
    notification_id: str
    event_type: NotificationType
    created_at: str
    dispatched_at: str = ""
    status: str
    dispatches: list[NotificationDispatchRecord] = Field(default_factory=list)

    @property
    def notification_type(self) -> NotificationType:
        return self.event_type


class NotificationDeliveryResult(AgentSchemaModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery_metadata: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True
