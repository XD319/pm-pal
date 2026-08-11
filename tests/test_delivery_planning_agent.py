from __future__ import annotations

from unittest.mock import patch

import pytest

from pm_pal.agents import delivery_planning_agent
from pm_pal.skills.executor import SkillExecutor
from pm_pal.skills.registry import get_skill_spec
from pm_pal.utils.llm_structured_call import StructuredCallError


@pytest.fixture(autouse=True)
def clear_skill_cache():
    SkillExecutor.clear_cache()
    yield
    SkillExecutor.clear_cache()


@pytest.mark.asyncio
async def test_delivery_planning_agent_populates_outputs_and_trace():
    async def fake_llm_structured_call(*, prompt, schema, metadata):
        if metadata["agent_name"] == "implementation.plan":
            return {
                "implementation_steps": [
                    "Implement backend auth endpoint",
                    "Wire login UI to API",
                ],
                "target_modules": ["backend.auth", "frontend.login"],
                "constraints": ["Keep existing session cookie contract stable"],
            }
        if metadata["agent_name"] == "test.plan.generate":
            return {
                "test_scope": ["Auth API", "Login UI"],
                "edge_cases": ["Expired session", "Invalid OAuth callback"],
                "regression_focus": ["Password login", "Session persistence"],
            }
        if metadata["agent_name"] == "codex.prompt.generate":
            return {
                "agent_prompt": "Inspect backend.auth and frontend.login, implement login flow changes, then run auth-focused tests.",
                "recommended_execution_order": [
                    "Inspect auth modules",
                    "Implement API and UI changes",
                    "Run focused auth regression tests",
                ],
                "non_goals": ["Do not redesign unrelated profile settings flows"],
                "validation_checklist": [
                    "Acceptance criteria mapped to tests",
                    "Auth API and login UI regressions covered",
                ],
            }
        if metadata["agent_name"] == "claude_code.prompt.generate":
            return {
                "agent_prompt": "Review current auth flow, patch backend.auth and frontend.login with minimal scope, then validate session stability.",
                "recommended_execution_order": [
                    "Inspect current auth flow",
                    "Patch backend and UI",
                    "Verify session persistence",
                ],
                "non_goals": ["Do not refactor authentication architecture"],
                "validation_checklist": [
                    "Session cookie contract unchanged",
                    "Targeted auth tests pass",
                ],
            }
        raise AssertionError(f"unexpected skill: {metadata['agent_name']}")

    state = {
        "parsed_items": [
            {
                "id": "REQ-001",
                "description": "Support login",
                "acceptance_criteria": ["OAuth works"],
            }
        ],
        "plan": {
            "tasks": [
                {
                    "id": "T-1",
                    "title": "Build login",
                    "owner": "BE",
                    "requirement_ids": ["REQ-001"],
                    "depends_on": [],
                    "estimate_days": 2,
                }
            ],
            "milestones": [],
            "dependencies": [],
            "estimation": {"total_days": 2, "buffer_days": 1},
        },
        "risks": [
            {
                "id": "R-1",
                "description": "Auth integration risk",
                "impact": "medium",
                "mitigation": "Stage rollout",
                "buffer_days": 1,
            }
        ],
        "trace": {},
    }

    with patch(
        "pm_pal.skills.delivery_planning.llm_structured_call",
        side_effect=fake_llm_structured_call,
    ):
        result = await delivery_planning_agent.run(state)

    assert result["implementation_plan"]["target_modules"] == [
        "backend.auth",
        "frontend.login",
    ]
    assert result["test_plan"]["regression_focus"] == [
        "Password login",
        "Session persistence",
    ]
    assert "backend.auth" in result["codex_prompt_handoff"]["agent_prompt"]
    assert result["claude_code_prompt_handoff"]["validation_checklist"] == [
        "Session cookie contract unchanged",
        "Targeted auth tests pass",
    ]
    assert result["trace"]["implementation.plan"]["status"] == "ok"
    assert result["trace"]["test.plan.generate"]["status"] == "ok"
    assert result["trace"]["codex.prompt.generate"]["status"] == "ok"
    assert result["trace"]["claude_code.prompt.generate"]["status"] == "ok"
    assert result["trace"]["delivery_planning"]["status"] == "ok"
    assert get_skill_spec("implementation.plan").name == "implementation.plan"
    assert get_skill_spec("test.plan.generate").name == "test.plan.generate"
    assert get_skill_spec("codex.prompt.generate").name == "codex.prompt.generate"
    assert (
        get_skill_spec("claude_code.prompt.generate").name
        == "claude_code.prompt.generate"
    )


@pytest.mark.asyncio
async def test_delivery_planning_agent_returns_minimal_fallback_when_skill_fails():
    async def fake_llm_structured_call(*, prompt, schema, metadata):
        if metadata["agent_name"] == "implementation.plan":
            raise StructuredCallError(
                "structured call failed", raw_output="", structured_mode="fallback"
            )
        if metadata["agent_name"] == "test.plan.generate":
            return {
                "test_scope": ["Auth API"],
                "edge_cases": ["Timeout while exchanging token"],
                "regression_focus": ["Session renewal"],
            }
        if metadata["agent_name"] == "codex.prompt.generate":
            return {
                "agent_prompt": "Use the available test plan and acceptance criteria to implement only the missing auth work.",
                "recommended_execution_order": [
                    "Inspect affected auth flow",
                    "Implement minimal changes",
                    "Run auth tests",
                ],
                "non_goals": ["Do not broaden the task beyond login support"],
                "validation_checklist": ["Auth API test scope covered"],
            }
        if metadata["agent_name"] == "claude_code.prompt.generate":
            return {
                "agent_prompt": "Audit current auth flow and apply the smallest valid patch set for login support.",
                "recommended_execution_order": [
                    "Inspect code",
                    "Patch auth flow",
                    "Validate regression scope",
                ],
                "non_goals": ["Do not refactor unrelated identity code"],
                "validation_checklist": ["Regression focus checked"],
            }
        raise AssertionError(f"unexpected skill: {metadata['agent_name']}")

    state = {
        "parsed_items": [
            {
                "id": "REQ-001",
                "description": "Support login",
                "acceptance_criteria": ["OAuth works"],
            }
        ],
        "plan": {
            "tasks": [
                {
                    "id": "T-1",
                    "title": "Build login",
                    "owner": "BE",
                    "requirement_ids": ["REQ-001"],
                    "depends_on": [],
                    "estimate_days": 2,
                }
            ],
            "milestones": [],
            "dependencies": [],
            "estimation": {"total_days": 2, "buffer_days": 1},
        },
        "risks": [
            {
                "id": "R-1",
                "description": "Auth integration risk",
                "impact": "medium",
                "mitigation": "Stage rollout",
                "buffer_days": 1,
            }
        ],
        "trace": {},
    }

    with patch(
        "pm_pal.skills.delivery_planning.llm_structured_call",
        side_effect=fake_llm_structured_call,
    ):
        result = await delivery_planning_agent.run(state)

    assert result["implementation_plan"] == {
        "implementation_steps": [],
        "target_modules": [],
        "constraints": [],
    }
    assert result["test_plan"]["test_scope"] == ["Auth API"]
    assert result["codex_prompt_handoff"]["recommended_execution_order"] == [
        "Inspect affected auth flow",
        "Implement minimal changes",
        "Run auth tests",
    ]
    assert result["trace"]["implementation.plan"]["status"] == "error"
    assert result["trace"]["test.plan.generate"]["status"] == "ok"
    assert result["trace"]["codex.prompt.generate"]["status"] == "ok"
    assert result["trace"]["claude_code.prompt.generate"]["status"] == "ok"
    assert result["trace"]["delivery_planning"]["status"] == "ok"
