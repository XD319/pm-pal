"""HTTP API for the PM Agent pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from prd_pal.pm import DEFAULT_PM_DB_PATH
from prd_pal.pm.insight_service import cluster_feedback
from prd_pal.pm.opportunity_service import build_opportunity
from prd_pal.pm.prd_service import draft_prd_from_opportunity
from prd_pal.pm.repository import PmRepository
from prd_pal.pm.schemas import FeedbackItem, InsightCluster, OpportunityBrief
from prd_pal.pm.workflow import capture_feedback, run_pm_pipeline


class FeedbackCreateRequest(BaseModel):
    texts: list[str] = Field(default_factory=list)
    product_hint: str = ""
    source: str = "manual"


class InsightsGenerateRequest(BaseModel):
    feedback_ids: list[str] = Field(default_factory=list)
    feedback_texts: list[str] = Field(default_factory=list)
    product_hint: str = ""


class OpportunityCreateRequest(BaseModel):
    insight_ids: list[str] = Field(default_factory=list)
    product_hint: str = ""


class PrdGenerateRequest(BaseModel):
    opportunity_id: str = ""
    product_hint: str = ""
    run_quality_gate: bool = True


class PipelineRunRequest(BaseModel):
    feedback_texts: list[str] = Field(default_factory=list)
    product_hint: str = ""
    source: str = "manual"
    run_quality_gate: bool = True


def create_pm_router(*, db_path: str | Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/pm", tags=["pm"])
    resolved_db_path = Path(db_path) if db_path is not None else DEFAULT_PM_DB_PATH

    async def _repo() -> PmRepository:
        repository = PmRepository(resolved_db_path)
        result = await repository.initialize()
        if not result.ok:
            message = result.error.message if result.error else "initialize failed"
            raise HTTPException(
                status_code=500,
                detail={"code": "pm_repository_error", "message": message},
            )
        return repository

    @router.post("/feedback")
    async def create_feedback(payload: FeedbackCreateRequest) -> dict[str, Any]:
        repository = await _repo()
        try:
            items = await capture_feedback(
                payload.texts,
                product_hint=payload.product_hint,
                source=payload.source,
                repository=repository,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_feedback", "message": str(exc)},
            ) from exc
        return {
            "count": len(items),
            "feedback_ids": [item.id for item in items],
            "feedback": [item.model_dump(mode="python") for item in items],
        }

    @router.post("/insights/generate")
    async def generate_insights(payload: InsightsGenerateRequest) -> dict[str, Any]:
        repository = await _repo()
        feedback_items: list[FeedbackItem] = []
        for feedback_id in payload.feedback_ids:
            result = await repository.get_feedback(feedback_id)
            if not result.ok or result.value is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "feedback_not_found",
                        "message": f"feedback_id not found: {feedback_id}",
                    },
                )
            feedback_items.append(result.value)
        if payload.feedback_texts:
            feedback_items.extend(
                await capture_feedback(
                    payload.feedback_texts,
                    product_hint=payload.product_hint,
                    repository=repository,
                )
            )
        if not feedback_items:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_insights_request",
                    "message": "Provide feedback_ids and/or feedback_texts.",
                },
            )
        try:
            insights = await cluster_feedback(
                feedback_items, product_hint=payload.product_hint
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "insight_generation_failed", "message": str(exc)},
            ) from exc
        for insight in insights:
            await repository.upsert_artifact(
                artifact_type="insight",
                artifact_id=insight.id,
                payload=insight,
            )
        return {
            "count": len(insights),
            "insight_ids": [insight.id for insight in insights],
            "insights": [insight.model_dump(mode="python") for insight in insights],
        }

    @router.post("/opportunities")
    async def create_opportunity(payload: OpportunityCreateRequest) -> dict[str, Any]:
        repository = await _repo()
        insights: list[InsightCluster] = []
        for insight_id in payload.insight_ids:
            result = await repository.get_insight(insight_id)
            if not result.ok or result.value is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "insight_not_found",
                        "message": f"insight_id not found: {insight_id}",
                    },
                )
            insights.append(result.value)
        if not insights:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_opportunity_request",
                    "message": "Provide at least one insight_id.",
                },
            )
        try:
            opportunity = await build_opportunity(
                insights, product_hint=payload.product_hint
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "opportunity_generation_failed", "message": str(exc)},
            ) from exc
        await repository.upsert_artifact(
            artifact_type="opportunity",
            artifact_id=opportunity.id,
            payload=opportunity,
        )
        return {
            "opportunity_id": opportunity.id,
            "opportunity": opportunity.model_dump(mode="python"),
        }

    @router.post("/prds/generate")
    async def generate_prd(payload: PrdGenerateRequest) -> dict[str, Any]:
        repository = await _repo()
        opportunity_id = str(payload.opportunity_id or "").strip()
        if not opportunity_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_prd_request",
                    "message": "opportunity_id is required.",
                },
            )
        result = await repository.get_opportunity(opportunity_id)
        if not result.ok or result.value is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "opportunity_not_found",
                    "message": f"opportunity_id not found: {opportunity_id}",
                },
            )
        opportunity: OpportunityBrief = result.value
        draft, review_payload = await draft_prd_from_opportunity(
            opportunity,
            product_hint=payload.product_hint,
            run_quality_gate=payload.run_quality_gate,
        )
        await repository.upsert_artifact(
            artifact_type="prd",
            artifact_id=draft.id,
            payload=draft,
        )
        return {
            "prd_id": draft.id,
            "review_run_id": draft.review_run_id,
            "prd": draft.model_dump(mode="python"),
            "review": review_payload,
        }

    @router.post("/pipeline/run")
    async def run_pipeline(payload: PipelineRunRequest) -> dict[str, Any]:
        try:
            return await run_pm_pipeline(
                payload.feedback_texts,
                product_hint=payload.product_hint,
                source=payload.source,
                db_path=resolved_db_path,
                run_quality_gate=payload.run_quality_gate,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_pipeline_request", "message": str(exc)},
            ) from exc

    @router.get("/pipeline/{pipeline_id}")
    async def get_pipeline(pipeline_id: str) -> dict[str, Any]:
        repository = await _repo()
        result = await repository.get_pipeline_run(pipeline_id)
        if not result.ok or result.value is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "pipeline_not_found",
                    "message": f"pipeline_id not found: {pipeline_id}",
                },
            )
        record = result.value
        payload: dict[str, Any] = {
            "pipeline_id": record.id,
            "pipeline": record.model_dump(mode="python"),
        }
        if record.opportunity_id:
            opportunity_result = await repository.get_opportunity(record.opportunity_id)
            if opportunity_result.ok and opportunity_result.value is not None:
                payload["opportunity"] = opportunity_result.value.model_dump(
                    mode="python"
                )
        if record.prd_id:
            prd_result = await repository.get_prd(record.prd_id)
            if prd_result.ok and prd_result.value is not None:
                payload["prd"] = prd_result.value.model_dump(mode="python")
        insights = []
        for insight_id in record.insight_ids:
            insight_result = await repository.get_insight(insight_id)
            if insight_result.ok and insight_result.value is not None:
                insights.append(insight_result.value.model_dump(mode="python"))
        payload["insights"] = insights
        return payload

    @router.get("/traceability/{root_id}")
    async def get_traceability(root_id: str) -> dict[str, Any]:
        from prd_pal.pm.traceability import get_pm_traceability

        repository = await _repo()
        try:
            return await get_pm_traceability(repository, root_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_traceability_request", "message": str(exc)},
            ) from exc

    @router.post("/products")
    async def upsert_product(payload: dict[str, Any]) -> dict[str, Any]:
        from prd_pal.pm.models import ProductContext

        repository = await _repo()
        product = ProductContext.model_validate(payload)
        result = await repository.upsert_product_context(product)
        if not result.ok or result.value is None:
            message = result.error.message if result.error else "persist failed"
            raise HTTPException(
                status_code=500,
                detail={"code": "product_persist_failed", "message": message},
            )
        return {"product_id": result.value.id, "product": result.value.model_dump(mode="python")}

    return router
