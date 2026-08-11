"""Shared service APIs for pm_pal entrypoints."""

from pm_pal.memory.service import (
    DEFAULT_MEMORY_DB_PATH,
    MemoryService,
    MemoryServiceError,
)

from .artifact_patch_service import (
    apply_artifact_patch_async,
    build_clarification_to_patch_prompt,
)
from .artifact_service import ArtifactReviewSummary, review_artifact_version_async
from .execution_service import (
    append_execution_event,
    get_execution_status_for_mcp,
    get_traceability_for_mcp,
    handoff_to_executor_for_mcp,
    list_execution_tasks_for_mcp,
    prepare_agent_handoff_for_run_for_mcp,
    update_execution_task_for_mcp,
)
from .review_service import (
    ReviewResultSummary,
    answer_review_clarification,
    answer_review_clarification_async,
    answer_review_clarification_for_mcp,
    get_review_workspace_for_mcp,
    prepare_agent_handoff_for_mcp_async,
    review_prd_for_mcp,
    review_prd_for_mcp_async,
    review_prd_text,
    review_prd_text_async,
    review_requirement_for_mcp_async,
)
from .revision_service import generate_revision_for_run, generate_revision_for_run_async
from .roadmap_service import (
    build_roadmap_prompt,
    diff_roadmap_versions,
    generate_constrained_roadmap,
    integrate_with_execution_plan,
    validate_roadmap_result,
)

__all__ = [
    "DEFAULT_MEMORY_DB_PATH",
    "ArtifactReviewSummary",
    "MemoryService",
    "MemoryServiceError",
    "ReviewResultSummary",
    "answer_review_clarification",
    "answer_review_clarification_async",
    "answer_review_clarification_for_mcp",
    "append_execution_event",
    "apply_artifact_patch_async",
    "build_clarification_to_patch_prompt",
    "build_roadmap_prompt",
    "diff_roadmap_versions",
    "generate_constrained_roadmap",
    "generate_revision_for_run",
    "generate_revision_for_run_async",
    "get_execution_status_for_mcp",
    "get_review_workspace_for_mcp",
    "get_traceability_for_mcp",
    "handoff_to_executor_for_mcp",
    "integrate_with_execution_plan",
    "list_execution_tasks_for_mcp",
    "prepare_agent_handoff_for_mcp_async",
    "prepare_agent_handoff_for_run_for_mcp",
    "review_artifact_version_async",
    "review_prd_for_mcp",
    "review_prd_for_mcp_async",
    "review_prd_text",
    "review_prd_text_async",
    "review_requirement_for_mcp_async",
    "update_execution_task_for_mcp",
    "validate_roadmap_result",
]
