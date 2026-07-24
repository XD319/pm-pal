"""PM pipeline eval cases: structural quality gates with mocked LLM stages."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prd_pal.pm.repository import PmRepository
from prd_pal.pm.schemas import InsightCluster, OpportunityBrief, PRDDraft
from prd_pal.pm.workflow import run_pm_pipeline

EVAL_CASES = [
    {
        "case_id": "pm_auth_friction",
        "product_hint": "commerce",
        "feedback": [
            "Login is confusing for first-time users",
            "MFA resets fail too often",
            "Password reset emails arrive late",
        ],
        "insight_title": "Authentication friction",
        "opportunity_title": "Simplify authentication",
        "prd_sections": ["Goals", "Acceptance Criteria", "Risks"],
    },
    {
        "case_id": "pm_checkout_mobile",
        "product_hint": "commerce",
        "feedback": [
            "Checkout crashes on mobile Safari",
            "Coupon apply button disappears on small screens",
            "Payment confirmation is unclear",
        ],
        "insight_title": "Mobile checkout instability",
        "opportunity_title": "Stabilize mobile checkout",
        "prd_sections": ["Goals", "In Scope", "Out of Scope"],
    },
    {
        "case_id": "pm_search_latency",
        "product_hint": "catalog",
        "feedback": [
            "Category filters make search feel slow",
            "Faceted search freezes on large catalogs",
        ],
        "insight_title": "Search latency",
        "opportunity_title": "Speed up catalog search",
        "prd_sections": ["Goals", "Success Metrics"],
    },
    {
        "case_id": "pm_onboarding",
        "product_hint": "growth",
        "feedback": [
            "New users do not know where to start",
            "Empty states are confusing",
            "Setup checklist is missing",
        ],
        "insight_title": "Onboarding confusion",
        "opportunity_title": "Improve first-run onboarding",
        "prd_sections": ["Goals", "Acceptance Criteria"],
    },
    {
        "case_id": "pm_notifications",
        "product_hint": "engagement",
        "feedback": [
            "Too many email notifications",
            "Important alerts are buried",
            "No digest mode",
        ],
        "insight_title": "Notification overload",
        "opportunity_title": "Redesign notification preferences",
        "prd_sections": ["Goals", "Risks", "Success Metrics"],
    },
]


def _build_markdown(sections: list[str]) -> str:
    parts = [f"# PRD\n"]
    for section in sections:
        parts.append(f"## {section}\n- placeholder for {section.lower()}\n")
    return "\n".join(parts)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", EVAL_CASES, ids=[item["case_id"] for item in EVAL_CASES])
async def test_pm_pipeline_eval_case(case, tmp_path) -> None:
    repository = PmRepository(tmp_path / f"{case['case_id']}.sqlite3")
    await repository.initialize()

    async def _fake_cluster(feedback_items, **kwargs):
        feedback_ids = [item.id for item in feedback_items]
        return [
            InsightCluster(
                id=f"ins-{case['case_id']}",
                title=case["insight_title"],
                summary=f"Clustered from {len(feedback_ids)} feedback items",
                feedback_ids=feedback_ids,
                source_refs=[f"feedback:{fid}" for fid in feedback_ids],
                evidence_quotes=[feedback_items[0].text[:80]],
            )
        ]

    opportunity = OpportunityBrief(
        id=f"opp-{case['case_id']}",
        title=case["opportunity_title"],
        problem=case["insight_title"],
        users="Target users",
        value="Business value",
        constraints=["Ship in one quarter"],
        open_questions=["What is the success metric?"],
        insight_ids=[f"ins-{case['case_id']}"],
        source_refs=[f"insight:ins-{case['case_id']}"],
        evidence_refs=[f"feedback:placeholder"],
    )
    prd = PRDDraft(
        id=f"prd-{case['case_id']}",
        title=f"{case['opportunity_title']} PRD",
        markdown=_build_markdown(case["prd_sections"]),
        opportunity_id=opportunity.id,
        goals=["Ship measurable improvement"],
        in_scope=["Core flow"],
        out_of_scope=["Unrelated rewrite"],
        acceptance_criteria=["Measurable metric moves"],
        risks=["Scope creep"],
        success_metrics=["Activation or retention improves"],
        review_run_id=f"run-{case['case_id']}",
        source_refs=[f"opportunity:{opportunity.id}"],
        evidence_refs=[f"insight:ins-{case['case_id']}"],
    )

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
            new=AsyncMock(return_value=(prd, {"run_id": prd.review_run_id})),
        ),
    ):
        result = await run_pm_pipeline(
            case["feedback"],
            product_hint=case["product_hint"],
            repository=repository,
            run_quality_gate=False,
        )

    # Insight is traceable to original feedback.
    assert result["insight_ids"]
    insight = (await repository.get_insight(result["insight_ids"][0])).value
    assert insight is not None
    assert insight.source_refs
    assert all(ref.startswith("feedback:") for ref in insight.source_refs)
    assert set(insight.feedback_ids) <= set(result["feedback_ids"])

    # Opportunity references insights.
    assert result["opportunity_id"] == opportunity.id
    stored_opportunity = (await repository.get_opportunity(result["opportunity_id"])).value
    assert stored_opportunity is not None
    assert stored_opportunity.insight_ids

    # PRD has required structural sections.
    stored_prd = (await repository.get_prd(result["prd_id"])).value
    assert stored_prd is not None
    for section in case["prd_sections"]:
        assert section in stored_prd.markdown
    assert stored_prd.source_refs
    assert stored_prd.acceptance_criteria or "Acceptance Criteria" in stored_prd.markdown

    # Pipeline completed with review linkage available for quality gate consumers.
    assert result["status"] == "completed"
    assert result["review_run_id"] == prd.review_run_id
