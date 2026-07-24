"""Tests for prd_pal.pm.opportunity_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prd_pal.pm.opportunity_service import build_opportunity
from prd_pal.pm.schemas import InsightCluster


@pytest.mark.asyncio
async def test_build_opportunity_links_insights_and_evidence() -> None:
    insights = [
        InsightCluster(
            id="ins-1",
            title="Auth friction",
            summary="Login drop-off",
            feedback_ids=["fb-1", "fb-2"],
            source_refs=["feedback:fb-1", "feedback:fb-2"],
            evidence_quotes=["Login is confusing"],
        )
    ]
    llm_payload = {
        "title": "Simplify authentication",
        "problem": "Users abandon login",
        "users": "New enterprise users",
        "value": "Higher activation",
        "constraints": ["No full SSO rewrite"],
        "open_questions": ["Which IdP first?"],
    }

    with patch(
        "prd_pal.pm.opportunity_service.llm_structured_call",
        new=AsyncMock(return_value=llm_payload),
    ):
        brief = await build_opportunity(insights, product_hint="commerce")

    assert brief.title == "Simplify authentication"
    assert brief.insight_ids == ["ins-1"]
    assert brief.source_refs == ["insight:ins-1"]
    assert "feedback:fb-1" in brief.evidence_refs
    assert brief.open_questions == ["Which IdP first?"]


@pytest.mark.asyncio
async def test_build_opportunity_rejects_empty_insights() -> None:
    with pytest.raises(ValueError, match="empty"):
        await build_opportunity([])
