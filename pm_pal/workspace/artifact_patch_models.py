"""Structured artifact patch models for clarification-driven version updates."""

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


class ArtifactPatchAction(StrEnum):
    """Whitelisted patch actions that stay stable for weaker LLMs."""

    set_field = "set_field"
    replace_text = "replace_text"
    insert_block_after = "insert_block_after"
    insert_block_before = "insert_block_before"
    append_text = "append_text"
    delete_block = "delete_block"


class PatchFailureCode(StrEnum):
    """Machine-readable reasons for patch rejection or downgrade."""

    schema_invalid = "schema_invalid"
    artifact_mismatch = "artifact_mismatch"
    base_version_mismatch = "base_version_mismatch"
    target_not_found = "target_not_found"
    old_value_mismatch = "old_value_mismatch"
    unsupported_field = "unsupported_field"
    duplicate_block_id = "duplicate_block_id"
    empty_ops = "empty_ops"


class PatchApplyStatus(StrEnum):
    """Top-level execution result for one patch application attempt."""

    applied = "applied"
    rejected = "rejected"
    needs_review = "needs_review"
    proposed_not_applied = "proposed_not_applied"
    clarification_required = "clarification_required"


class PatchFailureMode(StrEnum):
    """Configured downgrade path when apply-time validation fails."""

    reject = "reject"
    needs_review = "needs_review"
    proposed_not_applied = "proposed_not_applied"
    clarification_required = "clarification_required"


class ArtifactBlock(AgentSchemaModel):
    """One stable, addressable block inside an artifact document."""

    block_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    title: str = ""
    content: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class StructuredArtifactDocument(AgentSchemaModel):
    """Block-based artifact document that can be patched and replayed."""

    artifact_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    blocks: list[ArtifactBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_block_ids(self) -> StructuredArtifactDocument:
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block_id values must be unique")
        return self


class ArtifactPatchAuthor(AgentSchemaModel):
    """Simple author descriptor for audit trails."""

    type: str = Field(min_length=1)
    model: str = ""


class ArtifactPatchTarget(AgentSchemaModel):
    """Pointer to the block and field being modified."""

    block_id: str = Field(min_length=1)
    field: str = ""


class ArtifactPatchOp(AgentSchemaModel):
    """One minimal patch operation."""

    op_id: str = Field(min_length=1)
    action: ArtifactPatchAction
    target: ArtifactPatchTarget
    old_value: Any = None
    new_value: Any = None
    new_block: ArtifactBlock | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def validate_payload(self) -> ArtifactPatchOp:
        if self.action in {
            ArtifactPatchAction.set_field,
            ArtifactPatchAction.replace_text,
        }:
            if not self.target.field:
                raise ValueError("field is required for set_field and replace_text")
            if self.old_value is None:
                raise ValueError("old_value is required for set_field and replace_text")
            if self.new_value is None:
                raise ValueError("new_value is required for set_field and replace_text")
        elif self.action == ArtifactPatchAction.append_text:
            if not self.target.field:
                raise ValueError("field is required for append_text")
            if self.new_value is None:
                raise ValueError("new_value is required for append_text")
        elif self.action in {
            ArtifactPatchAction.insert_block_after,
            ArtifactPatchAction.insert_block_before,
        }:
            if self.new_block is None:
                raise ValueError("new_block is required for block insertion")
        elif self.action == ArtifactPatchAction.delete_block:
            if self.old_value is None:
                raise ValueError("old_value is required for delete_block")
        return self


class ArtifactPatch(AgentSchemaModel):
    """Auditable patch envelope produced from clarification answers."""

    schema_version: str = "1.0"
    patch_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    base_version: int = Field(ge=1)
    clarification_id: str = ""
    author: ArtifactPatchAuthor
    ops: list[ArtifactPatchOp] = Field(default_factory=list)
    summary: str = ""


class PatchApplyIssue(AgentSchemaModel):
    """One validation or execution issue raised while applying a patch."""

    code: PatchFailureCode
    message: str
    op_id: str = ""
    target_block_id: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class PatchApplyOpResult(AgentSchemaModel):
    """Per-op execution trace for replay and debugging."""

    op_id: str
    action: ArtifactPatchAction
    status: str
    target_block_id: str = ""
    field: str = ""
    message: str = ""


class ArtifactPatchApplyResult(AgentSchemaModel):
    """Structured result for patch application, downgrade, or rejection."""

    patch_id: str
    artifact_id: str
    base_version: int
    status: PatchApplyStatus
    failure_mode: PatchFailureMode = PatchFailureMode.reject
    issues: list[PatchApplyIssue] = Field(default_factory=list)
    applied_ops: list[PatchApplyOpResult] = Field(default_factory=list)
    next_version_id: str = ""
    next_version_number: int | None = None
    content_path: str = ""
    patch_path: str = ""
    diff_path: str = ""
    message: str = ""
