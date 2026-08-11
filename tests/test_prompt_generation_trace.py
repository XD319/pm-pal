from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pm_pal.run_review import run_review
from pm_pal.skills.executor import SkillExecutor


class _FakeConfig:
    llm_model = "test-model"
    llm_provider = "test-provider"

    def resolve_llm(self):
        return self.llm_provider, self.llm_model


@pytest.fixture(autouse=True)
def clear_skill_cache():
    SkillExecutor.clear_cache()
    yield
    SkillExecutor.clear_cache()


@pytest.mark.asyncio
async def test_sample_prd_trace_includes_prompt_generation_skills(
    tmp_path, monkeypatch, sample_prd_text: str
):
    async def fake_parser_call(*, prompt, schema, metadata):
        return {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "User registration and login must work with email and password.",
                    "acceptance_criteria": [
                        "Passwords require one uppercase letter and one digit",
                        "Confirmation email is sent within 30 seconds",
                    ],
                },
                {
                    "id": "REQ-002",
                    "description": "Admins can deactivate accounts immediately.",
                    "acceptance_criteria": ["Deactivation takes effect immediately"],
                },
            ]
        }

    async def fake_planner_call(*, prompt, schema, metadata):
        return {
            "tasks": [
                {
                    "id": "T-1",
                    "title": "Implement registration and login flows",
                    "owner": "BE",
                    "requirement_ids": ["REQ-001"],
                    "depends_on": [],
                    "estimate_days": 3,
                },
                {
                    "id": "T-2",
                    "title": "Implement account deactivation controls",
                    "owner": "BE",
                    "requirement_ids": ["REQ-002"],
                    "depends_on": ["T-1"],
                    "estimate_days": 2,
                },
            ],
            "milestones": [
                {
                    "id": "M-1",
                    "title": "Auth ready",
                    "includes": ["T-1", "T-2"],
                    "target_days": 5,
                }
            ],
            "dependencies": [{"from": "T-2", "to": "T-1", "type": "blocked_by"}],
            "estimation": {"total_days": 5, "buffer_days": 1},
        }

    async def fake_risk_call(*, prompt, schema, metadata):
        return {
            "risks": [
                {
                    "id": "R-1",
                    "description": "Authentication changes may break existing login behavior.",
                    "impact": "medium",
                    "mitigation": "Add focused auth regression coverage before release.",
                    "buffer_days": 1,
                    "evidence_ids": [],
                    "evidence_snippets": [],
                }
            ]
        }

    async def fake_reviewer_call(*, prompt, schema, metadata):
        return {
            "review_results": [
                {
                    "id": "REQ-001",
                    "is_clear": True,
                    "is_testable": True,
                    "is_ambiguous": False,
                    "issues": [],
                    "suggestions": "",
                },
                {
                    "id": "REQ-002",
                    "is_clear": True,
                    "is_testable": True,
                    "is_ambiguous": False,
                    "issues": [],
                    "suggestions": "",
                },
            ],
            "plan_review": {
                "coverage": "All parsed requirements are covered by planned tasks.",
                "milestones": "Auth milestone is coherent.",
                "estimation": "Estimate is reasonable for the scoped work.",
            },
        }

    async def fake_agent_call(*, prompt, schema, metadata):
        agent_name = metadata["agent_name"]
        if agent_name == "parser":
            return await fake_parser_call(
                prompt=prompt, schema=schema, metadata=metadata
            )
        if agent_name == "planner":
            return await fake_planner_call(
                prompt=prompt, schema=schema, metadata=metadata
            )
        if agent_name == "risk":
            return await fake_risk_call(prompt=prompt, schema=schema, metadata=metadata)
        if agent_name == "reviewer":
            return await fake_reviewer_call(
                prompt=prompt, schema=schema, metadata=metadata
            )
        raise AssertionError(f"unexpected agent: {agent_name}")

    async def fake_delivery_skill_call(*, prompt, schema, metadata):
        if metadata["agent_name"] == "implementation.plan":
            return {
                "implementation_steps": [
                    "Update registration validation and confirmation email handling.",
                    "Implement admin-triggered deactivation in the auth service and admin surface.",
                ],
                "target_modules": [
                    "backend.auth",
                    "backend.notifications",
                    "frontend.admin.users",
                ],
                "constraints": [
                    "Preserve existing authentication behavior for unaffected flows."
                ],
            }
        if metadata["agent_name"] == "test.plan.generate":
            return {
                "test_scope": [
                    "Registration API",
                    "Login flow",
                    "Admin deactivation flow",
                ],
                "edge_cases": [
                    "Weak password rejected",
                    "Email delivery timeout",
                    "Deactivated user login attempt",
                ],
                "regression_focus": [
                    "Existing login behavior",
                    "Audit logging of auth events",
                ],
            }
        if metadata["agent_name"] == "codex.prompt.generate":
            return {
                "agent_prompt": "Inspect backend.auth, backend.notifications, and frontend.admin.users; implement only the auth changes required by the plan; preserve existing login behavior; validate registration, login, and deactivation flows.",
                "recommended_execution_order": [
                    "Inspect target modules",
                    "Implement auth changes",
                    "Run focused registration/login/deactivation validation",
                ],
                "non_goals": ["Do not redesign the full user management module"],
                "validation_checklist": [
                    "Acceptance criteria covered",
                    "Auth regression scope validated",
                ],
            }
        if metadata["agent_name"] == "claude_code.prompt.generate":
            return {
                "agent_prompt": "Audit the current auth and admin account flows, patch the listed modules with minimal scope, and prove completion with targeted validation tied to the acceptance criteria.",
                "recommended_execution_order": [
                    "Inspect current flows",
                    "Patch target modules",
                    "Validate acceptance criteria and regressions",
                ],
                "non_goals": ["Do not expand into unrelated profile or analytics work"],
                "validation_checklist": [
                    "Immediate deactivation verified",
                    "Confirmation email timing covered",
                ],
            }
        raise AssertionError(f"unexpected delivery skill: {metadata['agent_name']}")

    monkeypatch.setenv("RISK_AGENT_ENABLE_CATALOG_TOOL", "false")

    with (
        patch("pm_pal.agents.structured_runner.Config", _FakeConfig),
        patch("pm_pal.agents.structured_runner.llm_structured_call", fake_agent_call),
        patch(
            "pm_pal.skills.delivery_planning.llm_structured_call",
            side_effect=fake_delivery_skill_call,
        ),
    ):
        result = await run_review(
            requirement_doc=sample_prd_text, outputs_root=tmp_path
        )

    trace_path = Path(result["report_paths"]["run_trace"])
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    assert trace["codex.prompt.generate"]["status"] == "ok"
    assert trace["claude_code.prompt.generate"]["status"] == "ok"
    assert (
        trace["codex.prompt.generate"]["template_id"]
        == "review.delivery_planning.codex_prompt"
    )
    assert trace["codex.prompt.generate"]["template_version"] == "v1"
    assert (
        trace["claude_code.prompt.generate"]["template_id"]
        == "review.delivery_planning.claude_code_prompt"
    )
    assert trace["claude_code.prompt.generate"]["template_version"] == "v1"
    assert result["result"]["codex_prompt_handoff"]["agent_prompt"]
    assert result["result"]["claude_code_prompt_handoff"]["validation_checklist"] == [
        "Immediate deactivation verified",
        "Confirmation email timing covered",
    ]
