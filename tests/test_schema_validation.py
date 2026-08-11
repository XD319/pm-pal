"""Schema validation tests for pm_pal Pydantic v2 models.

Covers success / failure / coercion paths for all four agent output schemas,
with mocked llm_structured_call so no real API calls are made.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, patch

from pm_pal.schemas.parser_schema import (
    validate_parser_output,
)
from pm_pal.schemas.planner_schema import (
    validate_planner_output,
)
from pm_pal.schemas.planning_skill_schema import (
    CodingAgentPromptOutput,
    ImplementationPlanOutput,
    QaPlanningOutput,
    validate_coding_agent_prompt_output,
    validate_implementation_plan_output,
    validate_test_plan_generate_output,
)
from pm_pal.schemas.risk_schema import (
    validate_risk_output,
)
from pm_pal.schemas.reviewer_schema import (
    validate_reviewer_output,
)
from pm_pal.state import create_initial_state


# ══════════════════════════════════════════════════════════════════════════�?
# Parser schema
# ══════════════════════════════════════════════════════════════════════════�?


class TestParserValidation:
    """Validate ParserOutput success and failure paths."""

    def test_valid_multi_item(self):
        data = {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "OAuth login",
                    "acceptance_criteria": ["Redirect works", "JWT returned"],
                },
                {
                    "id": "REQ-002",
                    "description": "Dashboard KPIs",
                    "acceptance_criteria": ["Shows revenue", "Shows churn"],
                },
            ]
        }
        out = validate_parser_output(data)
        assert len(out.parsed_items) == 2
        assert out.parsed_items[0].id == "REQ-001"
        assert out.parsed_items[1].acceptance_criteria == [
            "Shows revenue",
            "Shows churn",
        ]

    def test_none_acceptance_criteria_coerced(self):
        data = {"parsed_items": [{"id": "REQ-001", "acceptance_criteria": None}]}
        out = validate_parser_output(data)
        assert out.parsed_items[0].acceptance_criteria == []

    def test_scalar_acceptance_criteria_wrapped(self):
        data = {"parsed_items": [{"id": "REQ-001", "acceptance_criteria": "single"}]}
        out = validate_parser_output(data)
        assert out.parsed_items[0].acceptance_criteria == ["single"]

    def test_empty_dict_yields_empty_list(self):
        out = validate_parser_output({})
        assert out.parsed_items == []

    def test_missing_id_raises_validation_error(self):
        with pytest.raises(ValidationError):
            validate_parser_output({"parsed_items": [{"description": "no id"}]})

    def test_empty_id_raises_validation_error(self):
        with pytest.raises(ValidationError):
            validate_parser_output({"parsed_items": [{"id": ""}]})

    def test_extra_fields_ignored(self):
        data = {"parsed_items": [{"id": "REQ-001", "unknown_field": 42}]}
        out = validate_parser_output(data)
        assert out.parsed_items[0].id == "REQ-001"
        assert not hasattr(out.parsed_items[0], "unknown_field")


# ══════════════════════════════════════════════════════════════════════════�?
# Planner schema
# ══════════════════════════════════════════════════════════════════════════�?


class TestPlannerValidation:
    """Validate PlannerOutput success and failure paths."""

    VALID = {
        "tasks": [
            {
                "id": "T-1",
                "title": "Design API",
                "owner": "BE",
                "requirement_ids": ["REQ-001"],
                "depends_on": [],
                "estimate_days": 3,
            }
        ],
        "milestones": [
            {"id": "M-1", "title": "API Ready", "includes": ["T-1"], "target_days": 5}
        ],
        "dependencies": [{"from": "T-2", "to": "T-1", "type": "blocked_by"}],
        "estimation": {"total_days": 10, "buffer_days": 2},
    }

    def test_valid_full(self):
        out = validate_planner_output(self.VALID)
        assert len(out.tasks) == 1
        assert out.estimation.total_days == 10
        assert out.estimation.buffer_days == 2

    def test_dependency_alias_from(self):
        out = validate_planner_output(self.VALID)
        assert out.dependencies[0].from_task == "T-2"

    def test_empty_input_defaults(self):
        out = validate_planner_output({})
        assert out.tasks == []
        assert out.estimation.total_days == 0

    def test_none_depends_on_coerced(self):
        data = {"tasks": [{"id": "T-1", "depends_on": None}]}
        out = validate_planner_output(data)
        assert out.tasks[0].depends_on == []

    def test_task_missing_id_raises(self):
        with pytest.raises(ValidationError):
            validate_planner_output({"tasks": [{"title": "no id"}]})

    def test_milestone_missing_id_raises(self):
        with pytest.raises(ValidationError):
            validate_planner_output({"milestones": [{"title": "no id"}]})


# ══════════════════════════════════════════════════════════════════════════�?
# Risk schema
# ══════════════════════════════════════════════════════════════════════════�?


class TestRiskValidation:
    """Validate RiskOutput success and failure paths."""

    def test_valid_full(self):
        data = {
            "risks": [
                {
                    "id": "R-1",
                    "description": "Tight buffer",
                    "impact": "high",
                    "mitigation": "Add 2d buffer",
                    "buffer_days": 2,
                    "evidence_ids": ["RC-003"],
                    "evidence_snippets": ["Buffer below 15%"],
                }
            ]
        }
        out = validate_risk_output(data)
        assert len(out.risks) == 1
        assert out.risks[0].impact == "high"
        assert out.risks[0].evidence_ids == ["RC-003"]

    def test_default_impact_is_medium(self):
        out = validate_risk_output({"risks": [{"id": "R-1"}]})
        assert out.risks[0].impact == "medium"

    def test_invalid_impact_raises(self):
        with pytest.raises(ValidationError):
            validate_risk_output({"risks": [{"id": "R-1", "impact": "critical"}]})

    def test_empty_input(self):
        out = validate_risk_output({})
        assert out.risks == []

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            validate_risk_output({"risks": [{"description": "orphan"}]})


# ══════════════════════════════════════════════════════════════════════════�?
# Reviewer schema
# ══════════════════════════════════════════════════════════════════════════�?


class TestReviewerValidation:
    """Validate ReviewerOutput success and failure paths."""

    def test_valid_full(self):
        data = {
            "review_results": [
                {
                    "id": "REQ-001",
                    "is_clear": True,
                    "is_testable": True,
                    "is_ambiguous": False,
                    "issues": ["Vague term: 'fast'"],
                    "suggestions": "Define latency SLA",
                }
            ],
            "plan_review": {
                "coverage": "All covered",
                "milestones": "OK",
                "estimation": "Reasonable",
            },
        }
        out = validate_reviewer_output(data)
        assert out.review_results[0].is_clear is True
        assert out.plan_review.coverage == "All covered"

    def test_string_bools_coerced(self):
        data = {
            "review_results": [
                {
                    "id": "REQ-001",
                    "is_clear": "yes",
                    "is_testable": "0",
                    "is_ambiguous": "TRUE",
                }
            ]
        }
        out = validate_reviewer_output(data)
        r = out.review_results[0]
        assert r.is_clear is True
        assert r.is_testable is False
        assert r.is_ambiguous is True

    def test_none_issues_coerced(self):
        data = {"review_results": [{"id": "REQ-001", "issues": None}]}
        out = validate_reviewer_output(data)
        assert out.review_results[0].issues == []

    def test_empty_input(self):
        out = validate_reviewer_output({})
        assert out.review_results == []
        assert out.plan_review.coverage == ""

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            validate_reviewer_output({"review_results": [{"is_clear": True}]})


# ══════════════════════════════════════════════════════════════════════════�?
# End-to-end with mocked LLM: parser agent returns validated schema
# ══════════════════════════════════════════════════════════════════════════�?


class TestParserAgentMocked:
    """Parser agent with mocked llm_structured_call produces valid schema."""

    @pytest.mark.asyncio
    async def test_parser_returns_valid_output(self):
        mock_llm_output = {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "User login",
                    "acceptance_criteria": ["OAuth redirect"],
                }
            ]
        }
        with (
            patch(
                "pm_pal.agents.structured_runner.llm_structured_call",
                new_callable=AsyncMock,
                return_value=mock_llm_output,
            ),
            patch("pm_pal.agents.structured_runner.Config"),
        ):
            from pm_pal.agents import parser_agent

            state = create_initial_state("# Sample PRD\nUser login via OAuth.")
            result = await parser_agent.run(state)

        assert len(result["parsed_items"]) == 1
        assert result["parsed_items"][0]["id"] == "REQ-001"
        assert result["trace"]["parser"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_parser_schema_failure_returns_empty(self):
        bad_output = {"parsed_items": [{"description": "missing id"}]}
        with (
            patch(
                "pm_pal.agents.structured_runner.llm_structured_call",
                new_callable=AsyncMock,
                return_value=bad_output,
            ),
            patch("pm_pal.agents.structured_runner.Config"),
        ):
            from pm_pal.agents import parser_agent

            state = create_initial_state("# PRD")
            result = await parser_agent.run(state)

        assert result["parsed_items"] == []
        assert "schema validation failed" in result["trace"]["parser"]["error_message"]


class TestDeliveryPlanningSkillValidation:
    """Validate delivery planning skill schemas."""

    def test_valid_implementation_plan_output(self):
        out = validate_implementation_plan_output(
            {
                "implementation_steps": [
                    "Map requirement coverage to backend and frontend changes"
                ],
                "target_modules": ["api.auth", "web.login"],
                "constraints": ["Preserve existing login flow compatibility"],
            }
        )
        assert isinstance(out, ImplementationPlanOutput)
        assert out.target_modules == ["api.auth", "web.login"]

    def test_empty_implementation_plan_defaults(self):
        out = validate_implementation_plan_output({})
        assert out.implementation_steps == []
        assert out.target_modules == []
        assert out.constraints == []

    def test_valid_test_plan_output(self):
        out = validate_test_plan_generate_output(
            {
                "test_scope": ["Authentication API", "Login UI"],
                "edge_cases": ["Expired token refresh", "Concurrent login attempts"],
                "regression_focus": ["Existing password login", "Session persistence"],
            }
        )
        assert isinstance(out, QaPlanningOutput)
        assert out.edge_cases[0] == "Expired token refresh"

    def test_empty_test_plan_defaults(self):
        out = validate_test_plan_generate_output({})
        assert out.test_scope == []
        assert out.edge_cases == []
        assert out.regression_focus == []

    def test_valid_coding_agent_prompt_output(self):
        out = validate_coding_agent_prompt_output(
            {
                "agent_prompt": "Inspect auth modules, implement login changes, then run targeted tests.",
                "recommended_execution_order": [
                    "Inspect auth flow",
                    "Implement backend changes",
                    "Validate login regressions",
                ],
                "non_goals": ["Do not redesign unrelated account settings flows"],
                "validation_checklist": [
                    "Acceptance criteria mapped to tests",
                    "Pytest passes for auth scope",
                ],
            }
        )
        assert isinstance(out, CodingAgentPromptOutput)
        assert out.recommended_execution_order[0] == "Inspect auth flow"

    def test_empty_coding_agent_prompt_defaults(self):
        out = validate_coding_agent_prompt_output({})
        assert out.agent_prompt == ""
        assert out.recommended_execution_order == []
        assert out.non_goals == []
        assert out.validation_checklist == []
