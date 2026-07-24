"""Tests for prd_pal.pm.workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prd_pal.pm.repository import PmRepository
from prd_pal.pm.schemas import (
    InsightCluster,
    OpportunityBrief,
    PRDDraft,
    PipelineStatus,
)
from prd_pal.pm.workflow import capture_feedback, run_pm_pipeline


@pytest.mark.asyncio
async def test_capture_feedback_persists_items(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()

    items = await capture_feedback(
        ["Login is confusing", "Checkout crashes"],
        product_hint="commerce",
        repository=repository,
    )

    assert len(items) == 2
    listed = await repository.list_feedback(product_hint="commerce")
    assert listed.ok is True
    assert len(listed.value or []) == 2


@pytest.mark.asyncio
async def test_run_pm_pipeline_end_to_end_with_mocks(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()

    insights = [
        InsightCluster(
            id="ins-1",
            title="Auth friction",
            feedback_ids=["will-be-replaced"],
            source_refs=["feedback:will-be-replaced"],
        )
    ]
    opportunity = OpportunityBrief(
        id="opp-1",
        title="Simplify auth",
        insight_ids=["ins-1"],
        source_refs=["insight:ins-1"],
        evidence_refs=["feedback:fb-1"],
    )
    prd = PRDDraft(
        id="prd-1",
        title="Auth PRD",
        markdown="# Goals\n- Reduce drop-off",
        opportunity_id="opp-1",
        review_run_id="20260724T120000Z",
    )
    review_payload = {"run_id": "20260724T120000Z", "findings": []}

    async def _fake_cluster(feedback_items, **kwargs):
        return [
            InsightCluster(
                id="ins-1",
                title="Auth friction",
                feedback_ids=[feedback_items[0].id],
                source_refs=[f"feedback:{feedback_items[0].id}"],
            )
        ]

    with (
        patch(
            "prd_pal.pm.workflow.cluster_feedback",
            new=AsyncMock(side_effect=_fake_cluster),
        ),
        patch(
            "prd_pal.pm.workflow.build_opportunity",
            new=AsyncMock(return_value=opportunity),
        ),
        patch(
            "prd_pal.pm.workflow.draft_prd_from_opportunity",
            new=AsyncMock(return_value=(prd, review_payload)),
        ),
    ):
        result = await run_pm_pipeline(
            ["Login is confusing", "MFA is painful"],
            product_hint="commerce",
            repository=repository,
        )

    assert result["status"] == PipelineStatus.completed
    assert result["opportunity_id"] == "opp-1"
    assert result["prd_id"] == "prd-1"
    assert result["review_run_id"] == "20260724T120000Z"
    assert len(result["feedback_ids"]) == 2
    assert result["insight_ids"] == ["ins-1"]

    stored = await repository.get_pipeline_run(result["pipeline_id"])
    assert stored.ok is True
    assert stored.value is not None
    assert stored.value.status == PipelineStatus.completed
    assert stored.value.prd_id == "prd-1"

    insight_result = await repository.get_insight("ins-1")
    assert insight_result.ok is True


@pytest.mark.asyncio
async def test_run_pm_pipeline_marks_failed_on_error(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()

    with patch(
        "prd_pal.pm.workflow.cluster_feedback",
        new=AsyncMock(side_effect=RuntimeError("llm down")),
    ):
        with pytest.raises(RuntimeError, match="llm down"):
            await run_pm_pipeline(
                ["Something broke"],
                repository=repository,
            )

    listed = await repository.list_feedback()
    assert listed.ok is True
    # feedback was captured before clustering failed
    assert len(listed.value or []) == 1
