import json

from pm_pal.packs import (
    ExecutionPack,
    ExecutionPackBuilder,
    ImplementationPack,
    ImplementationPackBuilder,
    TestPack as HandoffTestPack,
    TestPackBuilder,
)
from pm_pal.schemas.planning_skill_schema import (
    CodingAgentPromptOutput,
    ImplementationPlanOutput,
    QaPlanningOutput,
)


SAMPLE_REQUIREMENTS = [
    {
        "id": "REQ-001",
        "description": "Support OAuth login for campus recruiters",
        "acceptance_criteria": ["OAuth callback succeeds", "Session is persisted"],
    }
]

SAMPLE_TASKS = [
    {
        "id": "TASK-001",
        "title": "Implement OAuth login flow",
        "owner": "BE",
        "requirement_ids": ["REQ-001"],
    }
]

SAMPLE_RISKS = [
    {
        "id": "RISK-001",
        "description": "Existing login flow may regress",
        "impact": "high",
        "mitigation": "Run focused auth regression tests",
        "owner": "qa",
    }
]

IMPLEMENTATION_PLAN = ImplementationPlanOutput(
    implementation_steps=[
        "Inspect auth entrypoints",
        "Implement OAuth callback",
        "Persist recruiter session",
    ],
    target_modules=["pm_pal/server/app.py", "frontend/src/login.ts"],
    constraints=["Preserve password login behavior"],
)

TEST_PLAN = QaPlanningOutput(
    test_scope=["OAuth callback API", "Recruiter login page"],
    edge_cases=["Expired OAuth state", "Duplicate callback delivery"],
    regression_focus=["Password login", "Session refresh"],
)

CODEX_PROMPT = CodingAgentPromptOutput(
    agent_prompt="Implement the auth changes, then run focused backend and frontend tests.",
    recommended_execution_order=[
        "Review auth flow",
        "Apply backend changes",
        "Validate recruiter login",
    ],
    non_goals=["Do not redesign account settings"],
    validation_checklist=[
        "Acceptance criteria mapped to tests",
        "Pytest auth scope passes",
    ],
)

CLAUDE_PROMPT = CodingAgentPromptOutput(
    agent_prompt="Verify the implementation with edge-case and regression coverage.",
    recommended_execution_order=[
        "Review changed files",
        "Add edge-case coverage",
        "Run regression suite",
    ],
    non_goals=["Do not broaden test scope beyond auth"],
    validation_checklist=["OAuth edge cases covered", "Regression suite stays green"],
)


def test_implementation_pack_builder_builds_serializable_pack() -> None:
    pack = ImplementationPackBuilder().build(
        requirements=SAMPLE_REQUIREMENTS,
        tasks=SAMPLE_TASKS,
        risks=SAMPLE_RISKS,
        implementation_plan_output=IMPLEMENTATION_PLAN,
        test_plan_output=TEST_PLAN,
        codex_prompt_output=CODEX_PROMPT,
        claude_code_prompt_output=CLAUDE_PROMPT,
    )

    assert isinstance(pack, ImplementationPack)
    assert pack.pack_type == "implementation_pack"
    assert pack.task_id == "TASK-001"
    assert pack.target_modules == ["pm_pal/server/app.py", "frontend/src/login.ts"]
    assert pack.agent_handoff.primary_agent == "codex"
    assert "Requirements:" in pack.context
    assert json.loads(pack.model_dump_json())["title"] == "Implement OAuth login flow"


def test_test_pack_builder_merges_validation_checklists() -> None:
    pack = TestPackBuilder().build(
        requirements=SAMPLE_REQUIREMENTS,
        tasks=SAMPLE_TASKS,
        risks=SAMPLE_RISKS,
        implementation_plan_output=IMPLEMENTATION_PLAN,
        test_plan_output=TEST_PLAN,
        codex_prompt_output=CODEX_PROMPT,
        claude_code_prompt_output=CLAUDE_PROMPT,
    )

    assert isinstance(pack, HandoffTestPack)
    assert pack.pack_type == "test_pack"
    assert pack.test_scope == ["OAuth callback API", "Recruiter login page"]
    assert "Password login" in pack.edge_cases
    assert "Acceptance criteria mapped to tests" in pack.acceptance_criteria
    assert "Regression suite stays green" in pack.acceptance_criteria
    assert (
        json.loads(pack.model_dump_json())["agent_handoff"]["primary_agent"]
        == "claude_code"
    )


def test_execution_pack_builder_assembles_nested_packs_and_risks() -> None:
    pack = ExecutionPackBuilder().build(
        requirements=SAMPLE_REQUIREMENTS,
        tasks=SAMPLE_TASKS,
        risks=SAMPLE_RISKS,
        implementation_plan_output=IMPLEMENTATION_PLAN,
        test_plan_output=TEST_PLAN,
        codex_prompt_output=CODEX_PROMPT,
        claude_code_prompt_output=CLAUDE_PROMPT,
    )

    assert isinstance(pack, ExecutionPack)
    assert isinstance(pack.implementation_pack, ImplementationPack)
    assert isinstance(pack.test_pack, HandoffTestPack)
    assert pack.risk_pack[0].summary == "Existing login flow may regress"
    assert pack.risk_pack[0].level == "high"

    payload = json.loads(pack.model_dump_json())
    assert payload["implementation_pack"]["task_id"] == "TASK-001"
    assert payload["test_pack"]["acceptance_criteria"]
    assert payload["risk_pack"][0]["mitigation"] == "Run focused auth regression tests"
