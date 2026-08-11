from __future__ import annotations

import pytest

from pm_pal.service import selective_rerun_service as selective_rerun_service


def test_build_artifact_diff_has_structured_fields() -> None:
    diff = selective_rerun_service.build_artifact_diff(
        "# Scope\nfoo\n# Risks\nlow",
        "# Scope\nfoo changed\n# Risks\nlow",
    )
    assert diff["schema_version"] == "artifact_diff.v1"
    assert "changed_sections" in diff
    assert "scope" in diff["changed_sections"]
    assert diff["sections"]["modified"]


@pytest.mark.asyncio
async def test_build_rerun_plan_marks_cache_reuse_for_unaffected_node(
    monkeypatch,
) -> None:
    async def fake_analyze_affected_nodes_async(
        *, artifact_diff, baseline_snapshot=None
    ):  # type: ignore[no-untyped-def]
        return {
            "affected_nodes": ["risk"],
            "reasons": {"risk": ["changed risk section"]},
            "confidence": "high",
            "analysis_mode": "test",
        }

    monkeypatch.setattr(
        selective_rerun_service,
        "analyze_affected_nodes_async",
        fake_analyze_affected_nodes_async,
    )
    plan = await selective_rerun_service.build_rerun_plan_async(
        prd_v1="# Scope\nfoo\n# Risks\nlow",
        prd_v2="# Scope\nfoo\n# Risks\nhigh",
        cached_node_outputs={"plan": {"tasks": [{"id": "T1"}]}},
    )
    assert plan["schema_version"] == "rerun_plan.v1"
    planner_step = next(step for step in plan["steps"] if step["node"] == "planner")
    assert planner_step["action"] == "reuse_cache"
    assert planner_step["cache_hit"] is True
