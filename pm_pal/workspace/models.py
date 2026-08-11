"""Review workspace state models and persisted record schemas."""

from __future__ import annotations

from pydantic import Field

from pm_pal.packs.delivery_bundle import ApprovalEvent, BundleStatus
from pm_pal.schemas.base import AgentSchemaModel

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class WorkspaceStatus(StrEnum):
    """Human review workspace status derived from bundle lifecycle state."""

    confirmed = "confirmed"
    need_more_info = "need_more_info"
    deferred = "deferred"
    out_of_scope = "out_of_scope"
    blocked_by_risk = "blocked_by_risk"


class ApprovalRecord(AgentSchemaModel):
    """Persisted approval event enriched with workspace status context."""

    record_id: str
    run_id: str
    bundle_id: str
    timestamp: str
    action: str = ""
    from_bundle_status: BundleStatus
    to_bundle_status: BundleStatus
    workspace_status: WorkspaceStatus
    reviewer: str = ""
    comment: str = ""


class StatusSnapshot(AgentSchemaModel):
    """Latest persisted workspace status for one review run."""

    run_id: str
    bundle_id: str
    bundle_status: BundleStatus
    workspace_status: WorkspaceStatus
    updated_at: str


class ReviewWorkspaceRecord(AgentSchemaModel):
    """Aggregated workspace view assembled from bundle and persisted files."""

    run_id: str
    bundle_id: str = ""
    bundle_status: BundleStatus | None = None
    workspace_status: WorkspaceStatus = WorkspaceStatus.deferred
    approval_history: list[ApprovalEvent] = Field(default_factory=list)
    approval_records: list[ApprovalRecord] = Field(default_factory=list)
    status_snapshot: StatusSnapshot | None = None
