"""Delivery bundle schemas for standardized review handoff artifacts."""

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


class BundleStatus(StrEnum):
    """Lifecycle status for a delivery bundle."""

    draft = "draft"
    need_more_info = "need_more_info"
    approved = "approved"
    blocked_by_risk = "blocked_by_risk"


class ArtifactRef(AgentSchemaModel):
    """Reference to one generated artifact."""

    artifact_type: str
    path: str
    content_hash: str = ""
    generated_at: str = ""


class ApprovalEvent(AgentSchemaModel):
    """One approval transition on the bundle lifecycle."""

    event_id: str
    timestamp: str
    from_status: BundleStatus
    to_status: BundleStatus
    reviewer: str = ""
    comment: str = ""


class DeliveryArtifacts(AgentSchemaModel):
    """Canonical artifact list carried by a delivery bundle."""

    prd_review_report: ArtifactRef
    open_questions: ArtifactRef
    scope_boundary: ArtifactRef
    tech_design_draft: ArtifactRef
    test_checklist: ArtifactRef
    implementation_pack: ArtifactRef
    test_pack: ArtifactRef
    execution_pack: ArtifactRef


class DeliveryBundle(AgentSchemaModel):
    """Unified source of truth for v5 delivery handoff."""

    bundle_id: str
    bundle_version: str = "1.0"
    created_at: str
    status: BundleStatus = BundleStatus.draft
    source_run_id: str
    artifacts: DeliveryArtifacts
    approval_history: list[ApprovalEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
