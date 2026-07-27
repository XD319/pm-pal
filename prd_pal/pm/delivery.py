"""Delivery sync and post-launch learning stubs for the PM Agent."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import Field

from prd_pal.schemas.base import AgentSchemaModel, SafeStrList

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class DeliverySystem(StrEnum):
    github = "github"
    linear = "linear"
    jira = "jira"
    local = "local"
    feishu = "feishu"


class DeliveryIssue(AgentSchemaModel):
    """One-way created delivery issue linked back to a PM artifact."""

    id: str = Field(min_length=1)
    system: DeliverySystem = DeliverySystem.local
    external_id: str = ""
    title: str = Field(min_length=1)
    url: str = ""
    status: str = "open"
    prd_id: str = ""
    opportunity_id: str = ""
    pipeline_id: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryStatusUpdate(AgentSchemaModel):
    """Inbound delivery status writeback from an external system."""

    issue_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    actor: str = ""
    detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class LaunchReview(AgentSchemaModel):
    """Minimal post-launch review record for learning loop."""

    id: str = Field(min_length=1)
    prd_id: str = ""
    pipeline_id: str = ""
    outcome: Literal["win", "mixed", "miss"] = "mixed"
    metrics: dict[str, float] = Field(default_factory=dict)
    learnings: SafeStrList = Field(default_factory=list)
    follow_ups: SafeStrList = Field(default_factory=list)
    evidence_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _new_issue_id() -> str:
    return f"issue-{uuid.uuid4().hex[:12]}"


def _new_launch_id() -> str:
    return f"launch-{uuid.uuid4().hex[:12]}"


def create_delivery_issue(
    *,
    title: str,
    system: DeliverySystem | str = DeliverySystem.local,
    prd_id: str = "",
    opportunity_id: str = "",
    pipeline_id: str = "",
    evidence_refs: list[str] | None = None,
) -> DeliveryIssue:
    """Create a one-way delivery issue payload (no remote network call in MVP)."""

    normalized_system = DeliverySystem(system)
    external_id = f"{normalized_system.value}-{uuid.uuid4().hex[:8]}"
    return DeliveryIssue(
        id=_new_issue_id(),
        system=normalized_system,
        external_id=external_id,
        title=title,
        url=f"local://{normalized_system.value}/{external_id}",
        status="open",
        prd_id=prd_id,
        opportunity_id=opportunity_id,
        pipeline_id=pipeline_id,
        evidence_refs=list(evidence_refs or []),
        metadata={"sync_mode": "one_way_create"},
    )


def apply_delivery_status_update(
    issue: DeliveryIssue, update: DeliveryStatusUpdate
) -> DeliveryIssue:
    """Apply a limited status writeback onto a delivery issue."""

    if update.issue_id != issue.id and update.issue_id != issue.external_id:
        raise ValueError("status update does not match issue id")
    metadata = dict(issue.metadata or {})
    history = list(metadata.get("status_history") or [])
    history.append(
        {
            "from": issue.status,
            "to": update.status,
            "actor": update.actor,
            "detail": update.detail,
        }
    )
    metadata["status_history"] = history
    return issue.model_copy(update={"status": update.status, "metadata": metadata})


def build_launch_review(
    *,
    prd_id: str = "",
    pipeline_id: str = "",
    outcome: Literal["win", "mixed", "miss"] = "mixed",
    metrics: dict[str, float] | None = None,
    learnings: list[str] | None = None,
    follow_ups: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> LaunchReview:
    """Create a post-launch review record for metrics/learning feedback."""

    return LaunchReview(
        id=_new_launch_id(),
        prd_id=prd_id,
        pipeline_id=pipeline_id,
        outcome=outcome,
        metrics=dict(metrics or {}),
        learnings=list(learnings or []),
        follow_ups=list(follow_ups or []),
        evidence_refs=list(evidence_refs or []),
    )


def build_delivery_bundle(*, prd_id: str, title: str, acceptance_criteria: list[str], risks: list[str], evidence_refs: list[str], system: DeliverySystem | str = DeliverySystem.local) -> dict[str, Any]:
    """Create an idempotency-keyed handoff bundle; remote sync is opt-in."""
    target = DeliverySystem(system)
    tasks = [
        {"type": "engineering", "title": f"Implement: {title}", "description": "\n".join(acceptance_criteria)},
        {"type": "qa", "title": f"Validate: {title}", "description": "\n".join(acceptance_criteria + risks)},
        {"type": "launch", "title": f"Launch check: {title}", "description": "Review risks and release checklist."},
    ]
    return {"prd_id": prd_id, "system": target.value, "sync_status": "pending_configuration" if target == DeliverySystem.feishu else "local_only", "idempotency_key": f"handoff:{prd_id}", "tasks": tasks, "evidence_refs": evidence_refs}
