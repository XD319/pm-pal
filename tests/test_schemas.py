"""Tests for pm_pal.schemas — Pydantic v2 validation layer."""

import pytest
from pydantic import ValidationError

from pm_pal.schemas.base import normalize_bool, safe_list
from pm_pal.schemas.parser_schema import (
    validate_parser_output,
)
from pm_pal.schemas.planner_schema import (
    validate_planner_output,
)
from pm_pal.schemas.reviewer_schema import (
    validate_reviewer_output,
)
from pm_pal.schemas.risk_schema import (
    validate_risk_output,
)

# ═══════════════════════════════════════════════════════════════════════════
# base helpers
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeBool:
    @pytest.mark.parametrize("val", [True, False])
    def test_passthrough_bool(self, val):
        assert normalize_bool(val) is val

    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "YES"])
    def test_truthy_strings(self, val):
        assert normalize_bool(val) is True

    @pytest.mark.parametrize("val", ["false", "False", "0", "no", "", "random"])
    def test_falsy_strings(self, val):
        assert normalize_bool(val) is False

    def test_non_string_non_bool(self):
        assert normalize_bool(1) is True
        assert normalize_bool(0) is False

    def test_whitespace_padding(self):
        assert normalize_bool("  true  ") is True


class TestSafeList:
    def test_none_returns_empty(self):
        assert safe_list(None) == []

    def test_list_passthrough(self):
        assert safe_list(["a", "b"]) == ["a", "b"]

    def test_scalar_wrapped(self):
        assert safe_list("single") == ["single"]

    def test_empty_list_passthrough(self):
        assert safe_list([]) == []


# ═══════════════════════════════════════════════════════════════════════════
# parser schema
# ═══════════════════════════════════════════════════════════════════════════


class TestParserSchema:
    def test_valid_full(self):
        data = {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "User login via OAuth",
                    "acceptance_criteria": ["Redirects to provider", "Returns JWT"],
                }
            ]
        }
        out = validate_parser_output(data)
        assert len(out.parsed_items) == 1
        assert out.parsed_items[0].id == "REQ-001"
        assert len(out.parsed_items[0].acceptance_criteria) == 2

    def test_none_acceptance_criteria_coerced(self):
        data = {
            "parsed_items": [
                {
                    "id": "REQ-002",
                    "description": "Dashboard",
                    "acceptance_criteria": None,
                }
            ]
        }
        out = validate_parser_output(data)
        assert out.parsed_items[0].acceptance_criteria == []

    def test_empty_input(self):
        out = validate_parser_output({})
        assert out.parsed_items == []

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            validate_parser_output({"parsed_items": [{"description": "no id"}]})

    def test_scalar_acceptance_criteria_coerced(self):
        data = {
            "parsed_items": [
                {"id": "REQ-003", "acceptance_criteria": "single criterion"}
            ]
        }
        out = validate_parser_output(data)
        assert out.parsed_items[0].acceptance_criteria == ["single criterion"]


# ═══════════════════════════════════════════════════════════════════════════
# planner schema
# ═══════════════════════════════════════════════════════════════════════════


class TestPlannerSchema:
    VALID_DATA = {
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
        out = validate_planner_output(self.VALID_DATA)
        assert len(out.tasks) == 1
        assert out.tasks[0].id == "T-1"
        assert out.tasks[0].requirement_ids == ["REQ-001"]
        assert out.estimation.total_days == 10

    def test_dependency_alias_from(self):
        out = validate_planner_output(self.VALID_DATA)
        assert out.dependencies[0].from_task == "T-2"
        assert out.dependencies[0].to == "T-1"

    def test_empty_input_defaults(self):
        out = validate_planner_output({})
        assert out.tasks == []
        assert out.milestones == []
        assert out.dependencies == []
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


# ═══════════════════════════════════════════════════════════════════════════
# risk schema
# ═══════════════════════════════════════════════════════════════════════════


class TestRiskSchema:
    def test_valid_full(self):
        data = {
            "risks": [
                {
                    "id": "R-1",
                    "description": "Tight buffer",
                    "impact": "high",
                    "mitigation": "Add buffer",
                    "buffer_days": 2,
                }
            ]
        }
        out = validate_risk_output(data)
        assert len(out.risks) == 1
        assert out.risks[0].impact == "high"

    def test_empty_input(self):
        out = validate_risk_output({})
        assert out.risks == []

    def test_invalid_impact_raises(self):
        with pytest.raises(ValidationError):
            validate_risk_output({"risks": [{"id": "R-1", "impact": "critical"}]})

    def test_default_impact_is_medium(self):
        out = validate_risk_output({"risks": [{"id": "R-1"}]})
        assert out.risks[0].impact == "medium"

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError):
            validate_risk_output({"risks": [{"description": "no id"}]})


# ═══════════════════════════════════════════════════════════════════════════
# reviewer schema
# ═══════════════════════════════════════════════════════════════════════════


class TestReviewerSchema:
    def test_valid_full(self):
        data = {
            "review_results": [
                {
                    "id": "REQ-001",
                    "is_clear": "true",
                    "is_testable": True,
                    "is_ambiguous": "false",
                    "issues": ["Vague term"],
                    "suggestions": "Be specific",
                }
            ],
            "plan_review": {
                "coverage": "OK",
                "milestones": "All covered",
                "estimation": "Reasonable",
            },
        }
        out = validate_reviewer_output(data)
        assert out.review_results[0].is_clear is True
        assert out.review_results[0].is_ambiguous is False

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


# ═══════════════════════════════════════════════════════════════════════════
# __init__ re-exports
# ═══════════════════════════════════════════════════════════════════════════


class TestInitReExports:
    def test_all_exports(self):
        from pm_pal.schemas import __all__

        expected = {
            "ParserOutput",
            "PlannerOutput",
            "RevisionAgentOutput",
            "RiskOutput",
            "RoadmapDiffOutput",
            "RoadmapItem",
            "RoadmapOutput",
            "ReviewerOutput",
            "validate_parser_output",
            "validate_planner_output",
            "validate_revision_output",
            "validate_risk_output",
            "validate_reviewer_output",
        }
        assert set(__all__) == expected

    def test_imports_work(self):
        from pm_pal.schemas import (
            ParserOutput,
            PlannerOutput,
            ReviewerOutput,
            RevisionAgentOutput,
            RiskOutput,
            RoadmapDiffOutput,
            RoadmapItem,
            RoadmapOutput,
            validate_parser_output,
            validate_planner_output,
            validate_reviewer_output,
            validate_revision_output,
            validate_risk_output,
        )

        assert all(
            cls is not None
            for cls in [
                ParserOutput,
                PlannerOutput,
                RevisionAgentOutput,
                ReviewerOutput,
                RoadmapDiffOutput,
                RoadmapItem,
                RoadmapOutput,
                RiskOutput,
            ]
        )
        assert all(
            callable(f)
            for f in [
                validate_parser_output,
                validate_planner_output,
                validate_revision_output,
                validate_reviewer_output,
                validate_risk_output,
            ]
        )
