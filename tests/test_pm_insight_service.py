"""Tests for prd_pal.pm.insight_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prd_pal.pm.insight_service import cluster_feedback
from prd_pal.pm.schemas import FeedbackItem


@pytest.mark.asyncio
async def test_cluster_feedback_attaches_source_refs() -> None:
    feedback = [
        FeedbackItem(id="fb-1", text="Login is confusing"),
        FeedbackItem(id="fb-2", text="MFA resets too often"),
        FeedbackItem(id="fb-3", text="Checkout crashes on mobile"),
    ]
    llm_payload = {
        "insights": [
            {
                "title": "Auth friction",
                "summary": "Users struggle with login and MFA",
                "theme": "auth",
                "feedback_ids": ["fb-1", "fb-2"],
                "evidence_quotes": ["Login is confusing"],
            },
            {
                "title": "Mobile checkout instability",
                "summary": "Checkout fails on phones",
                "theme": "checkout",
                "feedback_ids": ["fb-3"],
                "evidence_quotes": ["Checkout crashes on mobile"],
            },
        ],
        "notes": "2 themes",
    }

    with patch(
        "prd_pal.pm.insight_service.llm_structured_call",
        new=AsyncMock(return_value=llm_payload),
    ) as mock_call:
        insights = await cluster_feedback(feedback, product_hint="commerce")

    assert len(insights) == 2
    assert insights[0].source_refs == ["feedback:fb-1", "feedback:fb-2"]
    assert insights[1].feedback_ids == ["fb-3"]
    assert mock_call.await_count == 1
    prompt = mock_call.await_args.kwargs["prompt"]
    assert "fb-1" in prompt
    assert "commerce" in prompt


@pytest.mark.asyncio
async def test_cluster_feedback_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="empty"):
        await cluster_feedback([])


@pytest.mark.asyncio
async def test_cluster_feedback_filters_unknown_feedback_ids() -> None:
    feedback = [FeedbackItem(id="fb-1", text="Slow search")]
    llm_payload = {
        "insights": [
            {
                "title": "Search latency",
                "summary": "Search is slow",
                "theme": "search",
                "feedback_ids": ["fb-1", "fb-missing"],
                "evidence_quotes": [],
            }
        ],
        "notes": "",
    }

    with patch(
        "prd_pal.pm.insight_service.llm_structured_call",
        new=AsyncMock(return_value=llm_payload),
    ):
        insights = await cluster_feedback(feedback)

    assert insights[0].feedback_ids == ["fb-1"]
    assert insights[0].source_refs == ["feedback:fb-1"]
