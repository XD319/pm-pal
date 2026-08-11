"""Shared source connector schemas and normalized document contract."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import Field

from pm_pal.schemas.base import AgentSchemaModel


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for connector fetch events."""
    return datetime.now(timezone.utc)


class SourceType(str, Enum):
    """Supported source origins for requirement ingestion."""

    local_file = "local_file"
    url = "url"
    feishu = "feishu"
    github = "github"
    notion = "notion"


class SourceMetadata(AgentSchemaModel):
    """Connector metadata shared across all normalized sources."""

    mime_type: str = ""
    encoding: str = "utf-8"
    size_bytes: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SourceDocument(AgentSchemaModel):
    """Normalized document returned by all source connectors."""

    source_type: SourceType
    source: str = Field(min_length=1)
    title: str = ""
    content_markdown: str = ""
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    fetched_at: datetime = Field(default_factory=utc_now)
