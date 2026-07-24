"""Tests for PM MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prd_pal.mcp_server import server as mcp_server
from prd_pal.pm.schemas import (
    InsightCluster,
    OpportunityBrief,
    PRDDraft,
    PipelineStatus,
)


@pytest.mark.asyncio
async def test_capture_feedback_mcp_tool(tmp_path) -> None:
    result = await mcp_server.capture_feedback(
        texts=["Login is confusing"],
        product_hint="commerce",
        options={"db_path": str(tmp_path / "pm.sqlite3")},
    )

    assert "error" not in result
    assert result["count"] == 1
    assert len(result["feedback_ids"]) == 1


@pytest.mark.asyncio
async def test_run_pm_pipeline_mcp_tool(tmp_path) -> None:
    opportunity = OpportunityBrief(
        id="opp-1",
        title="Simplify auth",
        insight_ids=["ins-1"],
        source_refs=["insight:ins-1"],
    )
    prd = PRDDraft(
        id="prd-1",
        title="Auth PRD",
        markdown="# Goals\n- Reduce drop-off",
        opportunity_id="opp-1",
        review_run_id="run-1",
    )

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
            new=AsyncMock(return_value=(prd, {"run_id": "run-1"})),
        ),
    ):
        result = await mcp_server.run_pm_pipeline(
            feedback_texts=["Login is confusing"],
            product_hint="commerce",
            run_quality_gate=False,
            options={"db_path": str(tmp_path / "pm.sqlite3")},
        )

    assert "error" not in result
    assert result["status"] == PipelineStatus.completed
    assert result["prd_id"] == "prd-1"


@pytest.mark.asyncio
async def test_capture_feedback_rejects_empty_texts(tmp_path) -> None:
    result = await mcp_server.capture_feedback(
        texts=[],
        options={"db_path": str(tmp_path / "pm.sqlite3")},
    )

    assert result["error"]["code"] == "invalid_input"
