"""Versioned template models for prompts and delivery artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ArtifactRenderer = Callable[[dict[str, Any]], str]

BASE_SECTION_ORDER = (
    "Goal",
    "Project Context",
    "Required Changes",
    "Constraints",
    "Acceptance Criteria",
    "Testing Requirements",
    "Output Expectations",
)


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    """Shared metadata carried by every versioned template."""

    template_id: str
    template_type: str
    version: str
    description: str

    def trace_metadata(self) -> dict[str, str]:
        return {
            "template_id": self.template_id,
            "template_type": self.template_type,
            "template_version": self.version,
            "template_description": self.description,
        }

    def prompt_trace_metadata(self) -> dict[str, str]:
        metadata = self.trace_metadata()
        metadata["prompt_version"] = self.version
        return metadata

    def registry_metadata(self, *, is_default: bool, status: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "template_id": self.template_id,
            "template_type": self.template_type,
            "version": self.version,
            "description": self.description,
            "is_default": bool(is_default),
            "status": str(status or "registered").strip() or "registered",
        }
        return metadata


@dataclass(frozen=True, slots=True)
class ReviewPromptTemplate(TemplateDefinition):
    """System/user prompt pair used by one review workflow step."""

    system_prompt: str
    user_prompt: str


@dataclass(frozen=True, slots=True)
class AdapterPromptTemplate(TemplateDefinition):
    """Static metadata used to render a coding-agent handoff prompt."""

    agent_name: str
    role_summary: str
    output_hint: str
    section_order: tuple[str, ...] = BASE_SECTION_ORDER


@dataclass(frozen=True, slots=True)
class DeliveryArtifactTemplate(TemplateDefinition):
    """Renderer-backed markdown template for a delivery artifact."""

    artifact_type: str
    file_name: str
    renderer: ArtifactRenderer
