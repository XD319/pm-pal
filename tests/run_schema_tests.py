"""Standalone test runner for schema validation — no pytest required."""

import sys
import traceback

from pydantic import ValidationError

sys.path.insert(0, ".")

from pm_pal.schemas.base import normalize_bool, safe_list
from pm_pal.schemas.parser_schema import validate_parser_output
from pm_pal.schemas.planner_schema import validate_planner_output
from pm_pal.schemas.reviewer_schema import validate_reviewer_output
from pm_pal.schemas.risk_schema import validate_risk_output

passed = 0
failed = 0
errors: list[str] = []


def test(name: str):
    """Decorator to register and run a test function."""

    def decorator(fn):
        global passed, failed
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:
            print(f"  FAIL  {name}")
            traceback.print_exc()
            errors.append(name)
            failed += 1
        return fn

    return decorator


# ═══════════════════════════════════════════════════════════════════════════
print("\n=== base helpers ===")
# ═══════════════════════════════════════════════════════════════════════════


@test("normalize_bool: passthrough True")
def _():
    assert normalize_bool(True) is True


@test("normalize_bool: passthrough False")
def _():
    assert normalize_bool(False) is False


@test("normalize_bool: 'true' -> True")
def _():
    assert normalize_bool("true") is True


@test("normalize_bool: 'True' -> True")
def _():
    assert normalize_bool("True") is True


@test("normalize_bool: '1' -> True")
def _():
    assert normalize_bool("1") is True


@test("normalize_bool: 'yes' -> True")
def _():
    assert normalize_bool("yes") is True


@test("normalize_bool: 'false' -> False")
def _():
    assert normalize_bool("false") is False


@test("normalize_bool: '0' -> False")
def _():
    assert normalize_bool("0") is False


@test("normalize_bool: '' -> False")
def _():
    assert normalize_bool("") is False


@test("normalize_bool: 'random' -> False")
def _():
    assert normalize_bool("random") is False


@test("normalize_bool: int 1 -> True")
def _():
    assert normalize_bool(1) is True


@test("normalize_bool: int 0 -> False")
def _():
    assert normalize_bool(0) is False


@test("normalize_bool: ' true ' with whitespace -> True")
def _():
    assert normalize_bool("  true  ") is True


@test("safe_list: None -> []")
def _():
    assert safe_list(None) == []


@test("safe_list: list passthrough")
def _():
    assert safe_list(["a", "b"]) == ["a", "b"]


@test("safe_list: scalar -> [scalar]")
def _():
    assert safe_list("single") == ["single"]


@test("safe_list: empty list passthrough")
def _():
    assert safe_list([]) == []


# ═══════════════════════════════════════════════════════════════════════════
print("\n=== parser schema ===")
# ═══════════════════════════════════════════════════════════════════════════


@test("parser: valid full input")
def _():
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


@test("parser: None acceptance_criteria coerced to []")
def _():
    data = {
        "parsed_items": [
            {"id": "REQ-002", "description": "Dashboard", "acceptance_criteria": None}
        ]
    }
    out = validate_parser_output(data)
    assert out.parsed_items[0].acceptance_criteria == []


@test("parser: empty input -> empty list")
def _():
    out = validate_parser_output({})
    assert out.parsed_items == []


@test("parser: missing id raises ValidationError")
def _():
    try:
        validate_parser_output({"parsed_items": [{"description": "no id"}]})
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


@test("parser: scalar acceptance_criteria coerced to list")
def _():
    data = {
        "parsed_items": [{"id": "REQ-003", "acceptance_criteria": "single criterion"}]
    }
    out = validate_parser_output(data)
    assert out.parsed_items[0].acceptance_criteria == ["single criterion"]


# ═══════════════════════════════════════════════════════════════════════════
print("\n=== planner schema ===")
# ═══════════════════════════════════════════════════════════════════════════

PLANNER_VALID = {
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


@test("planner: valid full input")
def _():
    out = validate_planner_output(PLANNER_VALID)
    assert len(out.tasks) == 1
    assert out.tasks[0].id == "T-1"
    assert out.tasks[0].requirement_ids == ["REQ-001"]
    assert out.estimation.total_days == 10


@test("planner: dependency alias 'from' -> from_task")
def _():
    out = validate_planner_output(PLANNER_VALID)
    assert out.dependencies[0].from_task == "T-2"
    assert out.dependencies[0].to == "T-1"


@test("planner: empty input -> all defaults")
def _():
    out = validate_planner_output({})
    assert out.tasks == []
    assert out.milestones == []
    assert out.dependencies == []
    assert out.estimation.total_days == 0


@test("planner: None depends_on coerced to []")
def _():
    data = {"tasks": [{"id": "T-1", "depends_on": None}]}
    out = validate_planner_output(data)
    assert out.tasks[0].depends_on == []


@test("planner: task missing id raises ValidationError")
def _():
    try:
        validate_planner_output({"tasks": [{"title": "no id"}]})
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


@test("planner: milestone missing id raises ValidationError")
def _():
    try:
        validate_planner_output({"milestones": [{"title": "no id"}]})
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
print("\n=== risk schema ===")
# ═══════════════════════════════════════════════════════════════════════════


@test("risk: valid full input")
def _():
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


@test("risk: empty input -> empty list")
def _():
    out = validate_risk_output({})
    assert out.risks == []


@test("risk: invalid impact raises ValidationError")
def _():
    try:
        validate_risk_output({"risks": [{"id": "R-1", "impact": "critical"}]})
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


@test("risk: default impact is 'medium'")
def _():
    out = validate_risk_output({"risks": [{"id": "R-1"}]})
    assert out.risks[0].impact == "medium"


@test("risk: missing id raises ValidationError")
def _():
    try:
        validate_risk_output({"risks": [{"description": "no id"}]})
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
print("\n=== reviewer schema ===")
# ═══════════════════════════════════════════════════════════════════════════


@test("reviewer: valid full input with string bools")
def _():
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


@test("reviewer: string bools coerced correctly")
def _():
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


@test("reviewer: None issues coerced to []")
def _():
    data = {"review_results": [{"id": "REQ-001", "issues": None}]}
    out = validate_reviewer_output(data)
    assert out.review_results[0].issues == []


@test("reviewer: empty input -> defaults")
def _():
    out = validate_reviewer_output({})
    assert out.review_results == []
    assert out.plan_review.coverage == ""


@test("reviewer: missing id raises ValidationError")
def _():
    try:
        validate_reviewer_output({"review_results": [{"is_clear": True}]})
        assert False, "Should have raised ValidationError"
    except ValidationError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
print("\n=== __init__ re-exports ===")
# ═══════════════════════════════════════════════════════════════════════════


@test("__init__: __all__ contains all expected exports")
def _():
    from pm_pal.schemas import __all__

    expected = {
        "ParserOutput",
        "PlannerOutput",
        "RiskOutput",
        "ReviewerOutput",
        "validate_parser_output",
        "validate_planner_output",
        "validate_risk_output",
        "validate_reviewer_output",
    }
    assert set(__all__) == expected


@test("__init__: all validate functions are callable")
def _():
    from pm_pal.schemas import (
        validate_parser_output,
        validate_planner_output,
        validate_reviewer_output,
        validate_risk_output,
    )

    assert all(
        callable(f)
        for f in [
            validate_parser_output,
            validate_planner_output,
            validate_reviewer_output,
            validate_risk_output,
        ]
    )


# ═══════════════════════════════════════════════════════════════════════════
# summary
# ═══════════════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}")
print(f"  Results: {passed} passed, {failed} failed, {passed + failed} total")
if errors:
    print(f"  Failed tests: {', '.join(errors)}")
print(f"{'=' * 60}\n")

sys.exit(1 if failed else 0)
