from __future__ import annotations

from typing import Annotated, Any, TypedDict

from .templates.registry import PARSER_REVIEW_PROMPT


class ParsedItemState(TypedDict):
    """State shape aligned with ``schemas.ParsedItem``."""

    id: str
    description: str
    acceptance_criteria: list[str]


class TaskState(TypedDict):
    """State shape aligned with ``schemas.Task``."""

    id: str
    title: str
    owner: str
    requirement_ids: list[str]
    depends_on: list[str]
    estimate_days: float


class MilestoneState(TypedDict):
    """State shape aligned with ``schemas.Milestone``."""

    id: str
    title: str
    includes: list[str]
    target_days: float


DependencyState = TypedDict(
    "DependencyState",
    {"from": str, "to": str, "type": str},
)


class EstimationState(TypedDict):
    """State shape aligned with ``schemas.Estimation``."""

    total_days: float
    buffer_days: float


class PlanState(TypedDict):
    """Aggregated planner output kept under one state key."""

    tasks: list[TaskState]
    milestones: list[MilestoneState]
    dependencies: list[DependencyState]
    estimation: EstimationState


class ImplementationPlanState(TypedDict):
    implementation_steps: list[str]
    target_modules: list[str]
    constraints: list[str]


class TestPlanState(TypedDict):
    test_scope: list[str]
    edge_cases: list[str]
    regression_focus: list[str]


class CodingAgentPromptState(TypedDict):
    agent_prompt: str
    recommended_execution_order: list[str]
    non_goals: list[str]
    validation_checklist: list[str]


class RiskItemState(TypedDict):
    """State shape aligned with ``schemas.RiskItem``."""

    id: str
    description: str
    impact: str
    mitigation: str
    buffer_days: float
    evidence_ids: list[str]
    evidence_snippets: list[str]


class ReviewResultItemState(TypedDict):
    """State shape aligned with ``schemas.ReviewResultItem``."""

    id: str
    is_clear: bool
    is_testable: bool
    is_ambiguous: bool
    issues: list[str]
    suggestions: str


class PlanReviewState(TypedDict):
    """State shape aligned with ``schemas.PlanReview``."""

    coverage: str
    milestones: str
    estimation: str


def merge_state_dicts(
    left: dict[str, Any] | None, right: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge dict fragments produced by parallel branches."""

    merged: dict[str, Any] = {}
    if isinstance(left, dict):
        merged.update(left)
    if isinstance(right, dict):
        merged.update(right)
    return merged


def plan_from_state(state: dict[str, Any]) -> PlanState:
    """Return a normalized plan view from aggregated or legacy fields."""

    raw_plan = state.get("plan")
    if isinstance(raw_plan, dict):
        return PlanState(
            tasks=list(raw_plan.get("tasks", []) or []),
            milestones=list(raw_plan.get("milestones", []) or []),
            dependencies=list(raw_plan.get("dependencies", []) or []),
            estimation=dict(raw_plan.get("estimation", {}) or {}),
        )

    return PlanState(
        tasks=list(state.get("tasks", []) or []),
        milestones=list(state.get("milestones", []) or []),
        dependencies=list(state.get("dependencies", []) or []),
        estimation=dict(state.get("estimation", {}) or {}),
    )


class ReviewState(TypedDict, total=False):
    """LangGraph state for the requirement-review workflow."""

    requirement_doc: str
    run_dir: str
    parsed_items: list[ParsedItemState]
    review_results: list[ReviewResultItemState]
    final_report: str
    trace: Annotated[dict[str, Any], merge_state_dicts]

    plan: PlanState
    tasks: list[TaskState]
    milestones: list[MilestoneState]
    dependencies: list[DependencyState]
    estimation: EstimationState
    implementation_plan: ImplementationPlanState
    test_plan: TestPlanState
    codex_prompt_handoff: CodingAgentPromptState
    claude_code_prompt_handoff: CodingAgentPromptState
    risks: list[RiskItemState]
    evidence: Annotated[dict[str, Any], merge_state_dicts]
    plan_review: PlanReviewState
    metrics: dict[str, Any]
    revision_round: int
    high_risk_ratio: float
    routing_reason: str
    parser_prompt_version: str
    review_mode_override: str
    mode: str
    review_mode: str
    review_open_questions: list[dict[str, Any]]
    review_risk_items: list[dict[str, Any]]
    review_tool_calls: list[dict[str, Any]]
    reviewer_insights: list[dict[str, Any]]
    partial_review: bool
    parallel_review: dict[str, Any]
    parallel_review_meta: dict[str, Any]
    normalized_requirement: dict[str, Any]
    memory_hits: list[dict[str, Any]]
    similar_reviews_referenced: list[str]
    normalizer_cache_hit: bool
    rag_enabled: bool
    review_profile: dict[str, Any]
    review_profile_pack: dict[str, Any]
    canonical_review_request: dict[str, Any]
    memory_config: dict[str, Any]
    memory_retrieval_config: dict[str, Any]
    normalizer_cache_config: dict[str, Any]
    structured_memory_hits: list[dict[str, Any]]
    rejected_memory_candidates: list[dict[str, Any]]
    memory_mode: str
    memory_usage_notes: list[str]
    memory_usage: dict[str, Any]


def create_initial_state(requirement_doc: str) -> ReviewState:
    """Build the seed state that kicks off the graph."""

    return ReviewState(
        requirement_doc=requirement_doc,
        parsed_items=[],
        review_results=[],
        final_report="",
        trace={},
        plan={"tasks": [], "milestones": [], "dependencies": [], "estimation": {}},
        tasks=[],
        milestones=[],
        dependencies=[],
        estimation={},
        implementation_plan={
            "implementation_steps": [],
            "target_modules": [],
            "constraints": [],
        },
        test_plan={"test_scope": [], "edge_cases": [], "regression_focus": []},
        codex_prompt_handoff={
            "agent_prompt": "",
            "recommended_execution_order": [],
            "non_goals": [],
            "validation_checklist": [],
        },
        claude_code_prompt_handoff={
            "agent_prompt": "",
            "recommended_execution_order": [],
            "non_goals": [],
            "validation_checklist": [],
        },
        risks=[],
        evidence={},
        plan_review={},
        metrics={},
        revision_round=0,
        high_risk_ratio=0.0,
        routing_reason="",
        parser_prompt_version=PARSER_REVIEW_PROMPT.version,
        review_mode_override="",
        mode="auto",
        review_mode="quick",
        review_open_questions=[],
        review_risk_items=[],
        review_tool_calls=[],
        reviewer_insights=[],
        partial_review=False,
        parallel_review={},
        parallel_review_meta={},
        normalized_requirement={},
        memory_hits=[],
        similar_reviews_referenced=[],
        normalizer_cache_hit=False,
        rag_enabled=False,
        review_profile={},
        review_profile_pack={},
        canonical_review_request={},
        memory_config={},
        memory_retrieval_config={},
        normalizer_cache_config={},
        structured_memory_hits=[],
        rejected_memory_candidates=[],
        memory_mode="off",
        memory_usage_notes=[],
        memory_usage={},
    )
