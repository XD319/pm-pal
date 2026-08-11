"""Project-scoped PM domain models. All entities belong to a project_id. :-)"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from pm_pal.schemas.base import AgentSchemaModel, SafeStrList


class SourceSyncStatus(StrEnum):
    idle = "idle"
    syncing = "syncing"
    succeeded = "succeeded"
    failed = "failed"


class OpportunityStatus(StrEnum):
    proposed = "proposed"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class PrdStatus(StrEnum):
    draft = "draft"
    quality_checked = "quality_checked"
    approved = "approved"
    waived = "waived"
    ready_for_delivery = "ready_for_delivery"


class DeliveryStatus(StrEnum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    degraded = "degraded"


class EvidenceSource(AgentSchemaModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    external_id: str = ""
    source_url: str = ""
    display_name: str = ""
    sync_status: SourceSyncStatus = SourceSyncStatus.idle
    sync_cursor: str = ""
    last_synced_at: str = ""
    last_error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(AgentSchemaModel):
    id: str = ""
    project_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    summary: str = ""
    quote: str = ""
    source_url: str = ""
    author: str = ""
    occurred_at: str = ""
    source_version: str = ""
    confirmed: bool = False
    source_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class InsightRecord(AgentSchemaModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = ""
    theme: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    source_urls: SafeStrList = Field(default_factory=list)
    version: int = 1
    audit_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityRecord(AgentSchemaModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    problem: str = ""
    users: str = ""
    value: str = ""
    status: OpportunityStatus = OpportunityStatus.proposed
    insight_ids: SafeStrList = Field(default_factory=list)
    evidence_refs: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    source_urls: SafeStrList = Field(default_factory=list)
    score: float = 0.0
    score_method: str = ""
    score_details: dict[str, float] = Field(default_factory=dict)
    version: int = 1
    audit_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PrdVersionRecord(AgentSchemaModel):
    id: str = Field(min_length=1)
    prd_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    opportunity_id: str = ""
    version: int = 1
    title: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    status: PrdStatus = PrdStatus.draft
    quality_assessment_id: str = ""
    quality_decision: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    source_urls: SafeStrList = Field(default_factory=list)
    project_source_id: str = ""
    audit_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeliveryRecord(AgentSchemaModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    prd_version_id: str = Field(min_length=1)
    target_kind: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: DeliveryStatus = DeliveryStatus.pending
    external_url: str = ""
    external_id: str = ""
    failure_reason: str = ""
    field_payload: dict[str, Any] = Field(default_factory=dict)
    audit_id: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectAuditEvent(AgentSchemaModel):
    id: str = Field(min_length=1)
    project_id: str = ""
    artifact_type: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    actor: str = ""
    reason: str = ""
    artifact_version: int = 1
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceLink(AgentSchemaModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    from_type: str = Field(min_length=1)
    from_id: str = Field(min_length=1)
    to_type: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    relation: str = "derived_from"
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class WriteReceipt(AgentSchemaModel):
    artifact_id: str = Field(min_length=1)
    version: int = 1
    audit_id: str = Field(min_length=1)
    next_human_action: str = ""
    status: str = ""
