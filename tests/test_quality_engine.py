from __future__ import annotations

import pytest

from pm_pal.quality_engine import (
    QualityAssessmentRequest,
    QualityEngine,
    QualityGateDecision,
)


@pytest.mark.asyncio
async def test_quality_engine_adapts_legacy_result_without_product_dependencies() -> (
    None
):
    async def kernel(_request):
        return {
            "run_id": "review-1",
            "coverage_ratio": 0.9,
            "high_risk_ratio": 0.0,
            "findings": [],
            "risk_items": [],
            "open_questions": [],
            "status": "completed",
        }

    assessment = await QualityEngine(kernel).assess(
        QualityAssessmentRequest(
            prd_version_id="prd-1:v1",
            prd_text="# Login\n\nUsers can log in.",
            opportunity_id="opp-1",
            evidence_refs=["feedback:fb-1"],
        )
    )

    assert assessment.prd_version_id == "prd-1:v1"
    assert assessment.review_run_id == "review-1"
    assert assessment.decision == QualityGateDecision.pass_
    assert assessment.quality_score == 0.9
    assert assessment.evidence_refs == ["feedback:fb-1"]


@pytest.mark.asyncio
async def test_quality_engine_blocks_high_risk_results() -> None:
    async def kernel(_request):
        return {
            "high_risk_ratio": 0.2,
            "coverage_ratio": 1.0,
            "risk_items": [{"title": "Data leak"}],
        }

    assessment = await QualityEngine(kernel).assess(
        QualityAssessmentRequest(prd_version_id="prd-1:v2", prd_text="# Draft")
    )

    assert assessment.decision == QualityGateDecision.blocked
    assert assessment.risks == [{"title": "Data leak"}]
