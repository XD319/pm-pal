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


class EvidenceSource(AgentSchemaModel):
    id: str = Field(min_length=1)
    product_id: str = ""
    source_type: str = Field(min_length=1)
    source_url: str = ""
    display_name: str = ""
    sync_status: SourceSyncStatus = SourceSyncStatus.idle
    sync_cursor: str = ""
    last_synced_at: str = ""
    last_error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(AgentSchemaModel):
    id: str = Field(min_length=1)
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
    source_refs: SafeStrList = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
