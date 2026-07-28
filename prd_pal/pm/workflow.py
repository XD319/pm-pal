"""Deterministic PM pipeline orchestration (compatibility path).

Stage work lives in independent services:
- capture_feedback (collect)
- cluster_feedback (insight attribution)
- build_opportunity (candidate generation)
- draft_prd_from_opportunity (legacy PRD path)

The decision workspace (`prd_pal.product_decision.services`) is the gated path for
evidence-backed insights and proposed opportunities. This module keeps
``/api/pm/pipeline/run`` working as a compatibility orchestrator.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .insight_service import cluster_feedback
from .opportunity_service import build_opportunity
from .prd_service import draft_prd_from_opportunity
from .repository import PmRepository
from .schemas import (
    FeedbackItem,
    InsightCluster,
    OpportunityBrief,
    PRDDraft,
    PipelineRunRecord,
    PipelineStage,
    PipelineStatus,
)

# Explicit stage aliases so callers can import the split services directly.
attribute_insights = cluster_feedback
generate_opportunity_candidate = build_opportunity


def _new_feedback_id() -> str:
    return f"fb-{uuid.uuid4().hex[:12]}"


def _new_pipeline_id() -> str:
    return f"pipe-{uuid.uuid4().hex[:12]}"


async def capture_feedback(
    texts: list[str],
    *,
    product_hint: str = "",
    product_id: str = "",
    source: str = "manual",
    repository: PmRepository | None = None,
) -> list[FeedbackItem]:
    """Normalize raw texts into FeedbackItem records and optionally persist them."""

    cleaned = [str(text or "").strip() for text in texts if str(text or "").strip()]
    if not cleaned:
        raise ValueError("feedback texts must not be empty")

    items = [
        FeedbackItem(
            id=_new_feedback_id(),
            text=text,
            source=source,
            product_id=product_id,
            product_hint=product_hint,
            source_refs=[f"input:{index}"],
        )
        for index, text in enumerate(cleaned, start=1)
    ]
    if repository is not None:
        for item in items:
            result = await repository.upsert_feedback(item)
            if not result.ok:
                message = result.error.message if result.error else "persist failed"
                raise RuntimeError(f"failed to persist feedback: {message}")
    return items


async def run_pm_pipeline(
    feedback_texts: list[str],
    *,
    product_hint: str = "",
    product_id: str = "",
    source: str = "manual",
    db_path: str | Path | None = None,
    repository: PmRepository | None = None,
    run_quality_gate: bool = True,
    review_callable: Any | None = None,
) -> dict[str, Any]:
    """Run the fixed PM workflow and persist intermediate artifacts.

    Returns structured ids plus stage payloads for API/MCP/console consumers.
    """

    repo = repository
    owned_repo = False
    if repo is None:
        from . import DEFAULT_PM_DB_PATH

        repo = PmRepository(db_path or DEFAULT_PM_DB_PATH)
        owned_repo = True
        init_result = await repo.initialize()
        if not init_result.ok:
            message = init_result.error.message if init_result.error else "init failed"
            raise RuntimeError(f"failed to initialize pm repository: {message}")

    pipeline_id = _new_pipeline_id()
    record = PipelineRunRecord(
        id=pipeline_id,
        status=PipelineStatus.running,
        stage=PipelineStage.capture,
        product_hint=product_hint,
        product_id=product_id,
    )
    await repo.upsert_pipeline_run(record)

    feedback_items: list[FeedbackItem] = []
    insights: list[InsightCluster] = []
    opportunity: OpportunityBrief | None = None
    prd: PRDDraft | None = None
    review_payload: dict[str, Any] | None = None

    try:
        feedback_items = await capture_feedback(
            feedback_texts,
            product_hint=product_hint,
            source=source,
            product_id=product_id,
            repository=repo,
        )
        record = record.model_copy(
            update={
                "stage": PipelineStage.cluster,
                "feedback_ids": [item.id for item in feedback_items],
                "source_refs": [f"feedback:{item.id}" for item in feedback_items],
            }
        )
        await repo.upsert_pipeline_run(record)

        insights = await cluster_feedback(
            feedback_items, product_hint=product_hint, run_id=pipeline_id
        )
        for insight in insights:
            await repo.upsert_artifact(
                artifact_type="insight",
                artifact_id=insight.id,
                payload=insight,
                pipeline_id=pipeline_id,
            )
        record = record.model_copy(
            update={
                "stage": PipelineStage.opportunity,
                "insight_ids": [insight.id for insight in insights],
            }
        )
        await repo.upsert_pipeline_run(record)

        opportunity = await build_opportunity(
            insights, product_hint=product_hint, run_id=pipeline_id
        )
        await repo.upsert_artifact(
            artifact_type="opportunity",
            artifact_id=opportunity.id,
            payload=opportunity,
            pipeline_id=pipeline_id,
        )
        record = record.model_copy(
            update={
                "stage": PipelineStage.prd,
                "opportunity_id": opportunity.id,
            }
        )
        await repo.upsert_pipeline_run(record)

        prd, review_payload = await draft_prd_from_opportunity(
            opportunity,
            product_hint=product_hint,
            run_id=pipeline_id,
            review_callable=review_callable,
            run_quality_gate=run_quality_gate,
        )
        await repo.upsert_artifact(
            artifact_type="prd",
            artifact_id=prd.id,
            payload=prd,
            pipeline_id=pipeline_id,
        )
        record = record.model_copy(
            update={
                "stage": PipelineStage.complete,
                "status": PipelineStatus.completed,
                "prd_id": prd.id,
                "review_run_id": prd.review_run_id,
            }
        )
        await repo.upsert_pipeline_run(record)
        from .traceability import persist_pipeline_traceability

        await persist_pipeline_traceability(repo, record)
    except Exception as exc:
        record = record.model_copy(
            update={
                "status": PipelineStatus.failed,
                "error_message": str(exc),
            }
        )
        await repo.upsert_pipeline_run(record)
        raise

    return {
        "pipeline_id": pipeline_id,
        "status": str(record.status),
        "stage": str(record.stage),
        "product_hint": product_hint,
        "product_id": product_id,
        "feedback_ids": [item.id for item in feedback_items],
        "insight_ids": [insight.id for insight in insights],
        "opportunity_id": opportunity.id if opportunity else "",
        "prd_id": prd.id if prd else "",
        "review_run_id": prd.review_run_id if prd else "",
        "feedback": [item.model_dump(mode="python") for item in feedback_items],
        "insights": [insight.model_dump(mode="python") for insight in insights],
        "opportunity": opportunity.model_dump(mode="python") if opportunity else None,
        "prd": prd.model_dump(mode="python") if prd else None,
        "review": review_payload,
        "db_path": str(repo.db_path) if owned_repo or db_path else "",
        "compatibility": {
            "orchestrator": "pm_pipeline",
            "note": "Formal decision-workspace candidates cannot mint PRDs without owner approval.",
        },
    }


# Compat alias for the collect stage.
collect_feedback = capture_feedback
