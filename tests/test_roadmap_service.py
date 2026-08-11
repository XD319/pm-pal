from __future__ import annotations

import pytest

from pm_pal.service.roadmap_service import (
    build_roadmap_prompt,
    diff_roadmap_versions,
    generate_constrained_roadmap,
    integrate_with_execution_plan,
    validate_roadmap_result,
)


def test_generate_constrained_roadmap_returns_stable_items():
    roadmap = generate_constrained_roadmap(
        tasks=[
            {"id": "T-1", "title": "Core API", "depends_on": [], "estimate_days": 3},
            {
                "id": "T-2",
                "title": "UI Flow",
                "depends_on": ["T-1"],
                "estimate_days": 5,
            },
        ],
        milestones=[{"id": "M-1", "includes": ["T-1", "T-2"]}],
        dependencies=[{"from": "T-2", "to": "T-1", "type": "blocked_by"}],
        risk_items=[
            {
                "id": "R-1",
                "severity": "high",
                "task_ids": ["T-2"],
                "description": "UI regression risk",
            }
        ],
        acceptance_criteria_coverage={"T-1": 0.8, "T-2": 0.4},
        business_priority_hints={"T-2": 0.9, "T-1": 0.4},
        version="v1",
    )

    validated = validate_roadmap_result(roadmap)
    assert validated.version == "v1"
    assert len(validated.roadmap_items) == 2
    assert validated.roadmap_items[0].id == "T-2"
    assert validated.roadmap_items[0].target_window in {"now", "next", "later"}
    assert isinstance(validated.roadmap_items[0].de_scope_candidate, bool)


def test_diff_roadmap_versions_tracks_added_removed_and_changed():
    v1 = {
        "version": "v1",
        "roadmap_items": [
            {
                "id": "T-1",
                "title": "Core API",
                "priority_score": 6.5,
                "effort_score": 5.0,
                "risk_score": 4.0,
                "dependency_ids": [],
                "target_window": "next",
                "why_now": "now",
                "why_later": "later",
                "de_scope_candidate": False,
            }
        ],
    }
    v2 = {
        "version": "v2",
        "roadmap_items": [
            {
                "id": "T-1",
                "title": "Core API",
                "priority_score": 7.2,
                "effort_score": 5.0,
                "risk_score": 4.0,
                "dependency_ids": [],
                "target_window": "now",
                "why_now": "now",
                "why_later": "later",
                "de_scope_candidate": False,
            },
            {
                "id": "T-2",
                "title": "UI Flow",
                "priority_score": 5.2,
                "effort_score": 7.0,
                "risk_score": 6.5,
                "dependency_ids": ["T-1"],
                "target_window": "next",
                "why_now": "now",
                "why_later": "later",
                "de_scope_candidate": False,
            },
        ],
    }

    diff = diff_roadmap_versions(v1, v2)
    assert diff["from_version"] == "v1"
    assert diff["to_version"] == "v2"
    assert len(diff["added"]) == 1
    assert diff["added"][0]["id"] == "T-2"
    assert len(diff["changed"]) == 1
    assert diff["changed"][0]["id"] == "T-1"
    assert "priority_score" in diff["changed"][0]["changed_fields"]


def test_integrate_with_execution_plan_embeds_scores():
    roadmap = generate_constrained_roadmap(
        tasks=[
            {"id": "T-1", "title": "Core API", "depends_on": [], "estimate_days": 2}
        ],
        dependencies=[],
        risk_items=[],
        acceptance_criteria_coverage={"T-1": 0.6},
        business_priority_hints={"T-1": 0.9},
    )
    plan = {"tasks": [{"id": "T-1", "title": "Core API"}], "milestones": []}

    integrated = integrate_with_execution_plan(plan, roadmap)
    assert integrated["execution_order"] == ["T-1"]
    assert integrated["tasks"][0]["roadmap"]["priority_score"] >= 0
    assert integrated["roadmap"]["roadmap_items"][0]["id"] == "T-1"


def test_validate_roadmap_result_rejects_missing_required_fields():
    with pytest.raises(Exception):
        validate_roadmap_result({"version": "v1", "roadmap_items": [{"id": "T-1"}]})


def test_build_roadmap_prompt_contains_schema_and_input():
    prompt = build_roadmap_prompt({"tasks": [{"id": "T-1"}]})
    assert "roadmap_items" in prompt
    assert '"id": "T-1"' in prompt
