from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from prd_pal.schemas.base import AgentSchemaModel, SafeStrList


class SourceSyncStatus(StrEnum):
    idle = "idle"
    syncing = "syncing"
    succeeded = "succeeded"
    failed = "failed"


class EvidenceSourceType(StrEnum):
    feishu_doc = "feishu_doc"
    feishu_meeting_notes = "feishu_meeting_notes"
    feishu_bitable = "feishu_bitable"


class EvidenceSource(AgentSchemaModel):
    id: str = Field(min_length=1)
    product_id: str = ""
    source_type: EvidenceSourceType | str = Field(min_length=1)
    external_id: str = ""
    source_url: str = ""
    display_name: str = ""
    field_mapping: dict[str, str] = Field(default_factory=dict)
    sync_status: SourceSyncStatus = SourceSyncStatus.idle
    sync_cursor: str = ""
    last_synced_at: str = ""
    last_error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(AgentSchemaModel):
    id: str = ""
    source_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    product_id: str = ""
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


class SyncTrigger(StrEnum):
    scheduled = "scheduled"
    manual = "manual"


class OpportunityCandidateStatus(StrEnum):
    proposed = "proposed"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class DecisionInsight(AgentSchemaModel):
    id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
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


class OpportunityCandidate(AgentSchemaModel):
    id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    problem: str = ""
    users: str = ""
    value: str = ""
    status: OpportunityCandidateStatus = OpportunityCandidateStatus.proposed
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


class DecisionAuditEvent(AgentSchemaModel):
    id: str = Field(min_length=1)
    product_id: str = ""
    artifact_type: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    actor: str = ""
    reason: str = ""
    artifact_version: int = 1
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class WriteReceipt(AgentSchemaModel):
    artifact_id: str = Field(min_length=1)
    version: int = 1
    audit_id: str = Field(min_length=1)
    next_human_action: str = ""
    status: str = ""


class PrdVersionStatus(StrEnum):
    draft = "draft"
    quality_checked = "quality_checked"
    approved = "approved"
    waived = "waived"
    ready_for_delivery = "ready_for_delivery"


class PrdVersion(AgentSchemaModel):
    id: str = Field(min_length=1)
    prd_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    opportunity_id: str = ""
    version: int = 1
    title: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    status: PrdVersionStatus = PrdVersionStatus.draft
    quality_assessment_id: str = ""
    quality_decision: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    source_refs: SafeStrList = Field(default_factory=list)
    source_urls: SafeStrList = Field(default_factory=list)
    audit_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductOwnerConfig(AgentSchemaModel):
    product_id: str = Field(min_length=1)
    owner_open_id: str = Field(min_length=1)
    admin_open_ids: SafeStrList = Field(default_factory=list)


class DeliveryExportStatus(StrEnum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    degraded = "degraded"


class DeliveryExport(AgentSchemaModel):
    id: str = Field(min_length=1)
    prd_version_id: str = Field(min_length=1)
    product_id: str = ""
    target_kind: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: DeliveryExportStatus = DeliveryExportStatus.pending
    external_url: str = ""
    external_id: str = ""
    failure_reason: str = ""
    degraded_from: str = ""
    field_payload: dict[str, Any] = Field(default_factory=dict)
    audit_id: str = ""
    evidence_refs: SafeStrList = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
