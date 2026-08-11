"""Facade that adapts the legacy review kernel to a product-quality contract."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pm_pal.service.review_service import ReviewResultSummary, review_prd_text_async
from pm_pal.utils.time import utc_now_iso

from .models import QualityAssessment, QualityAssessmentRequest, QualityGateDecision

ReviewKernel = Callable[
    [QualityAssessmentRequest], Awaitable[ReviewResultSummary | dict[str, Any]]
]


class QualityEngine:
    """Run the review kernel without coupling it to PM lifecycle orchestration."""

    def __init__(self, review_kernel: ReviewKernel | None = None) -> None:
        self._review_kernel = review_kernel or self._run_legacy_kernel

    async def assess(self, request: QualityAssessmentRequest) -> QualityAssessment:
        result = await self._review_kernel(request)
        return self._to_assessment(request, result)

    async def _run_legacy_kernel(
        self, request: QualityAssessmentRequest
    ) -> ReviewResultSummary:
        return await review_prd_text_async(
            prd_text=request.prd_text,
            config_overrides={
                "mode": "quick",
                "audit_context": {
                    "source": "quality_engine",
                    "tool_name": "quality_engine.assess",
                    "client_metadata": {
                        "prd_version_id": request.prd_version_id,
                        "opportunity_id": request.opportunity_id,
                        "evidence_refs": list(request.evidence_refs),
                    },
                },
            },
        )

    @staticmethod
    def _to_assessment(
        request: QualityAssessmentRequest,
        result: ReviewResultSummary | dict[str, Any],
    ) -> QualityAssessment:
        payload = (
            result.to_dict()
            if isinstance(result, ReviewResultSummary)
            else dict(result)
        )
        findings = _dict_list(payload.get("findings"))
        risks = _dict_list(payload.get("risk_items") or payload.get("risks"))
        clarification = _clarification_items(payload)
        high_risk_ratio = _number(payload.get("high_risk_ratio"))
        coverage_ratio = _number(payload.get("coverage_ratio"))
        decision = (
            QualityGateDecision.blocked
            if high_risk_ratio > 0
            else QualityGateDecision.needs_revision
            if findings or clarification
            else QualityGateDecision.pass_
        )
        return QualityAssessment(
            id=f"quality-{uuid.uuid4().hex[:12]}",
            prd_version_id=request.prd_version_id,
            review_run_id=str(payload.get("run_id") or payload.get("review_id") or ""),
            decision=decision,
            quality_score=round(
                max(0.0, min(1.0, coverage_ratio)) * (1.0 - min(1.0, high_risk_ratio)),
                4,
            ),
            findings=findings,
            risks=risks,
            clarification_items=clarification,
            evidence_refs=list(request.evidence_refs),
            policy=request.quality_policy,
            created_at=utc_now_iso(),
            metadata={"legacy_status": str(payload.get("status") or "")},
        )


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return (
        [dict(item) for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _clarification_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        return _dict_list(clarification.get("questions"))
    return _dict_list(payload.get("open_questions"))


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
