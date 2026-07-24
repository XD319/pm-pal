"""Tests for prd_pal.pm.prd_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prd_pal.pm.prd_service import draft_prd_from_opportunity
from prd_pal.pm.schemas import OpportunityBrief


@pytest.mark.asyncio
async def test_draft_prd_runs_quality_gate() -> None:
    opportunity = OpportunityBrief(
        id="opp-1",
        title="Simplify authentication",
        problem="Users abandon login",
        users="New users",
        value="Activation",
        source_refs=["insight:ins-1"],
        evidence_refs=["feedback:fb-1"],
    )
    llm_payload = {
        "title": "Auth redesign PRD",
        "markdown": "# Goals\n- Reduce login drop-off\n\n## Acceptance Criteria\n- Drop-off < 10%",
        "goals": ["Reduce login drop-off"],
        "in_scope": ["Password login UX"],
        "out_of_scope": ["SSO rewrite"],
        "acceptance_criteria": ["Drop-off < 10%"],
        "risks": ["Legacy auth"],
        "success_metrics": ["Activation +8%"],
    }
    review_payload = {
        "run_id": "20260724T120000Z",
        "findings": [{"id": "F1", "summary": "Missing edge cases"}],
        "open_questions": [],
        "risk_items": [],
    }
    review_callable = AsyncMock(return_value=review_payload)

    with patch(
        "prd_pal.pm.prd_service.llm_structured_call",
        new=AsyncMock(return_value=llm_payload),
    ):
        draft, review = await draft_prd_from_opportunity(
            opportunity,
            review_callable=review_callable,
        )

    assert draft.title == "Auth redesign PRD"
    assert draft.opportunity_id == "opp-1"
    assert draft.review_run_id == "20260724T120000Z"
    assert draft.source_refs == ["opportunity:opp-1"]
    assert "feedback:fb-1" in draft.evidence_refs
    assert review == review_payload
    review_callable.assert_awaited_once()
    assert "Reduce login drop-off" in review_callable.await_args.kwargs["prd_text"]


@pytest.mark.asyncio
async def test_draft_prd_can_skip_quality_gate() -> None:
    opportunity = OpportunityBrief(id="opp-2", title="Search latency")
    llm_payload = {
        "title": "Search PRD",
        "markdown": "# Goals\n- Faster search",
        "goals": ["Faster search"],
        "in_scope": ["Query UX"],
        "out_of_scope": ["Ranking model"],
        "acceptance_criteria": ["P95 < 200ms"],
        "risks": [],
        "success_metrics": ["Search success +5%"],
    }

    with patch(
        "prd_pal.pm.prd_service.llm_structured_call",
        new=AsyncMock(return_value=llm_payload),
    ):
        draft, review = await draft_prd_from_opportunity(
            opportunity, run_quality_gate=False
        )

    assert draft.review_run_id == ""
    assert review is None
