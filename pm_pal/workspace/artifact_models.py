"""Artifact / workspace / version domain models for review workflows."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from pm_pal.schemas.base import AgentSchemaModel

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class ArtifactVersionStatus(StrEnum):
    """Lifecycle status for one concrete artifact version."""

    draft = "draft"
    ready = "ready"
    active = "active"
    superseded = "superseded"
    archived = "archived"
    failed = "failed"


class WorkspaceStateStatus(StrEnum):
    """High-level state for one review workspace."""

    active = "active"
    awaiting_review = "awaiting_review"
    approved = "approved"
    rerun_required = "rerun_required"
    synced = "synced"
    archived = "archived"


class DecisionStatus(StrEnum):
    """Execution state for a workspace decision."""

    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    applied = "applied"
    cancelled = "cancelled"


class TraceLinkStatus(StrEnum):
    """Health of one trace link between artifacts, decisions, and runs."""

    active = "active"
    stale = "stale"
    broken = "broken"


class TraceLink(AgentSchemaModel):
    """A readable traceability edge between two domain objects."""

    trace_id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    link_type: str = "references"
    status: TraceLinkStatus = TraceLinkStatus.active
    source_run_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactVersion(AgentSchemaModel):
    """A persisted artifact snapshot that supports version-chain diff and patch."""

    version_id: str
    workspace_id: str
    artifact_key: str
    artifact_type: str
    status: ArtifactVersionStatus = ArtifactVersionStatus.draft
    version_number: int = Field(default=1, ge=1)
    title: str = ""
    parent_version_id: str | None = None
    source_run_id: str = ""
    created_at: str
    updated_at: str = ""
    content_path: str = ""
    content_checksum: str = ""
    diff_from_parent_path: str = ""
    patch_from_parent_path: str = ""
    change_summary: str = ""
    trace_links: list[TraceLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_version_chain(self) -> ArtifactVersion:
        if self.parent_version_id and self.parent_version_id == self.version_id:
            raise ValueError("parent_version_id must not equal version_id")
        if self.parent_version_id is None and (
            self.diff_from_parent_path or self.patch_from_parent_path
        ):
            raise ValueError("diff/patch paths require parent_version_id")
        if self.parent_version_id and self.version_number <= 1:
            raise ValueError(
                "version_number must be greater than 1 when parent_version_id is set"
            )
        return self


class DecisionRecord(AgentSchemaModel):
    """An auditable product or reviewer decision tied to one workspace version."""

    decision_id: str
    workspace_id: str
    decision_type: str
    status: DecisionStatus = DecisionStatus.proposed
    summary: str
    reason: str = ""
    artifact_key: str = ""
    artifact_version_id: str = ""
    parent_version_id: str | None = None
    source_run_id: str = ""
    actor: str = ""
    created_at: str
    trace_links: list[TraceLink] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision_reference(self) -> DecisionRecord:
        if (
            self.parent_version_id
            and self.parent_version_id == self.artifact_version_id
        ):
            raise ValueError("parent_version_id must not equal artifact_version_id")
        return self


class WorkspaceState(AgentSchemaModel):
    """Aggregated workspace state used by LangGraph nodes and persistence layers."""

    workspace_id: str
    name: str = ""
    status: WorkspaceStateStatus = WorkspaceStateStatus.active
    source_run_id: str = ""
    current_run_id: str = ""
    created_at: str
    updated_at: str = ""
    current_version_ids: dict[str, str] = Field(default_factory=dict)
    versions: list[ArtifactVersion] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    trace_links: list[TraceLink] = Field(default_factory=list)
    rerun_targets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_current_version_ids(self) -> WorkspaceState:
        known_version_ids = {version.version_id for version in self.versions}
        missing_version_ids = [
            version_id
            for version_id in self.current_version_ids.values()
            if version_id not in known_version_ids
        ]
        if missing_version_ids:
            missing_display = ", ".join(sorted(missing_version_ids))
            raise ValueError(
                f"current_version_ids contains unknown version ids: {missing_display}"
            )
        return self

    def get_current_version(self, artifact_key: str) -> ArtifactVersion | None:
        """Return the active version for one artifact key when it exists."""

        version_id = self.current_version_ids.get(artifact_key)
        if not version_id:
            return None
        for version in self.versions:
            if version.version_id == version_id:
                return version
        return None

    def list_versions(self, artifact_key: str) -> list[ArtifactVersion]:
        """Return all versions that belong to one logical artifact."""

        return [
            version for version in self.versions if version.artifact_key == artifact_key
        ]

    def register_version(
        self, version: ArtifactVersion, *, make_current: bool = True
    ) -> None:
        """Append a new version and optionally move the active pointer to it."""

        self.versions.append(version)
        if make_current:
            self.current_version_ids[version.artifact_key] = version.version_id
        self.updated_at = version.updated_at or version.created_at

    def mark_for_selective_rerun(self, artifact_key: str) -> None:
        """Track which artifact branches should be recalculated on the next run."""

        if artifact_key not in self.rerun_targets:
            self.rerun_targets.append(artifact_key)
