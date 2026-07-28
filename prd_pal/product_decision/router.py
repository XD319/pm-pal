from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from prd_pal.platform import LocalArtifactStore, LocalJobQueue, NullNotificationSink

from .feishu_client import FeishuEvidenceClient
from .models import EvidenceRecord, EvidenceSource, EvidenceSourceType, SyncTrigger
from .repository import ProductDecisionRepository
from .scheduler import DailyEvidenceSyncScheduler
from .prd_lifecycle import ApprovalService, PrdLifecycleService
from .delivery import (
    DeliveryService,
    FeishuBitableDeliveryTarget,
    FeishuProjectDeliveryTarget,
)
from .services import (
    CollectService,
    DecisionDomainError,
    EvaluateService,
    InsightService,
    OpportunityService,
)
from .sync_service import EvidenceSyncService
from .models import ProductOwnerConfig


class SourceCreateRequest(BaseModel):
    product_id: str = ""
    source_type: EvidenceSourceType | str = Field(min_length=1)
    external_id: str = ""
    source_url: str = ""
    display_name: str = ""
    field_mapping: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceSyncItem(BaseModel):
    external_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    summary: str = ""
    quote: str = ""
    source_url: str = ""
    author: str = ""
    occurred_at: str = ""
    source_version: str = ""
    source_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceSyncRequest(BaseModel):
    cursor: str = ""
    records: list[EvidenceSyncItem] = Field(default_factory=list)


class InsightCreateRequest(BaseModel):
    product_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = ""
    theme: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityCreateRequest(BaseModel):
    product_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    problem: str = ""
    users: str = ""
    value: str = ""
    insight_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityUpdateRequest(BaseModel):
    title: str | None = None
    problem: str | None = None
    users: str | None = None
    value: str | None = None
    add_evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""
    reason: str = ""


class OpportunityActionRequest(BaseModel):
    actor: str = ""
    reason: str = ""


class OpportunityEvaluateRequest(BaseModel):
    method: str = "rice"
    reach: float = 1.0
    impact: float = 1.0
    confidence: float = 1.0
    effort: float = 1.0
    ease: float = 1.0
    actor: str = ""


class ProductOwnerRequest(BaseModel):
    product_id: str = Field(min_length=1)
    owner_open_id: str = Field(min_length=1)
    admin_open_ids: list[str] = Field(default_factory=list)


class ActorReasonRequest(BaseModel):
    actor_open_id: str = Field(min_length=1)
    reason: str = ""


class WaiveRequest(BaseModel):
    actor_open_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PrdCreateRequest(BaseModel):
    title: str = ""
    markdown: str = ""
    actor_open_id: str = ""


class PrdReviseRequest(BaseModel):
    title: str | None = None
    markdown: str | None = None
    actor_open_id: str = ""
    reason: str = ""


class DeliveryExportRequest(BaseModel):
    actor_open_id: str = ""
    target: str = "feishu_bitable"
    app_token: str = ""
    table_id: str = ""
    project_key: str = ""
    field_mapping: dict[str, str] = Field(default_factory=dict)
    enable_project: bool = False
    base_url: str = ""


def _domain_http(exc: DecisionDomainError) -> HTTPException:
    status = 404 if exc.code.endswith("_not_found") or exc.code.endswith("_missing") else 422
    if exc.code in {"permission_denied", "opportunity_not_approved", "quality_not_passed"}:
        status = 409 if exc.code != "permission_denied" else 403
    if exc.code == "permission_denied":
        status = 403
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message})


def create_product_decision_router(
    *,
    db_path: str | Path,
    sync_service: EvidenceSyncService | None = None,
    artifacts_root: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/decision", tags=["product-decision"])
    queue = LocalJobQueue()
    notifications = NullNotificationSink()
    artifacts = LocalArtifactStore(artifacts_root or Path("data") / "artifacts")

    async def repo() -> ProductDecisionRepository:
        repository = ProductDecisionRepository(db_path)
        result = await repository.initialize()
        if not result.ok:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "decision_repository_error",
                    "message": result.error.message if result.error else "initialize failed",
                },
            )
        return repository

    async def service() -> EvidenceSyncService:
        if sync_service is not None:
            return sync_service
        repository = await repo()
        return EvidenceSyncService(
            repository,
            job_queue=queue,
            notifications=notifications,
            artifacts=artifacts,
            client=FeishuEvidenceClient(),
        )

    @router.post("/sources")
    async def register_source(payload: SourceCreateRequest) -> dict:
        repository = await repo()
        source = EvidenceSource(
            id=f"source-{uuid.uuid4().hex[:12]}",
            **payload.model_dump(),
        )
        saved = await repository.upsert_source(source)
        if not saved.ok or saved.value is None:
            raise HTTPException(status_code=500, detail={"code": "source_persist_failed"})
        return {"source": saved.value.model_dump(mode="json")}

    @router.get("/sources")
    async def list_sources(product_id: str = "") -> dict:
        result = await (await repo()).list_sources(product_id)
        return {"sources": [item.model_dump(mode="json") for item in (result.value or [])]}

    @router.get("/sources/{source_id}")
    async def get_source(source_id: str) -> dict:
        result = await (await repo()).get_source(source_id)
        if not result.ok or result.value is None:
            raise HTTPException(
                status_code=404, detail={"code": "source_not_found", "message": source_id}
            )
        return {"source": result.value.model_dump(mode="json")}

    @router.post("/sources/{source_id}/sync")
    async def sync_source(source_id: str, payload: EvidenceSyncRequest) -> dict:
        """Compatibility path: inject already-fetched records (tests / adapters)."""
        repository = await repo()
        source = await repository.get_source(source_id)
        if not source.ok or source.value is None:
            raise HTTPException(
                status_code=404, detail={"code": "source_not_found", "message": source_id}
            )
        records = [
            EvidenceRecord(
                id=f"evidence-{uuid.uuid4().hex[:12]}",
                source_id=source_id,
                product_id=source.value.product_id,
                **item.model_dump(),
            )
            for item in payload.records
        ]
        result = await repository.sync_evidence(
            source_id, records, cursor=payload.cursor
        )
        if not result.ok:
            await repository.mark_sync_failed(
                source_id, result.error.message if result.error else "sync failed"
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "source_sync_failed",
                    "message": result.error.message if result.error else "sync failed",
                },
            )
        return {
            "source_id": source_id,
            "synced_count": len(result.value or []),
            "evidence": [item.model_dump(mode="json") for item in (result.value or [])],
        }

    @router.post("/sources/{source_id}/refresh")
    async def refresh_source(source_id: str) -> dict:
        """H5 manual refresh; shares daily idempotency key with the 02:00 job."""
        repository = await repo()
        source = await repository.get_source(source_id)
        if not source.ok or source.value is None:
            raise HTTPException(
                status_code=404, detail={"code": "source_not_found", "message": source_id}
            )
        outcome = await (await service()).sync_source(
            source_id, trigger=SyncTrigger.manual
        )
        if outcome.get("status") == "failed":
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "source_refresh_failed",
                    "message": outcome.get("error") or "refresh failed",
                    "job_key": outcome.get("job_key"),
                },
            )
        refreshed = await repository.get_source(source_id)
        return {
            "source": refreshed.value.model_dump(mode="json") if refreshed.value else None,
            "job": outcome,
        }

    @router.post("/evidence/{evidence_id}/confirm")
    async def confirm_evidence(evidence_id: str) -> dict:
        result = await (await repo()).mark_evidence_confirmed(evidence_id, confirmed=True)
        if not result.ok or result.value is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "evidence_not_found", "message": evidence_id},
            )
        return {"evidence": result.value.model_dump(mode="json")}

    @router.get("/evidence")
    async def list_evidence(
        product_id: str = "", query: str = "", limit: int = 100
    ) -> dict:
        collect = CollectService(await repo())
        try:
            evidence = await collect.search_evidence(
                product_id=product_id, query=query, limit=limit
            )
        except DecisionDomainError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {"evidence": [item.model_dump(mode="json") for item in evidence]}

    @router.post("/insights")
    async def create_insight(payload: InsightCreateRequest) -> dict:
        service = InsightService(await repo())
        try:
            insight, receipt = await service.create_insight(**payload.model_dump())
        except DecisionDomainError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "insight": insight.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.get("/insights")
    async def list_insights(product_id: str = "") -> dict:
        service = InsightService(await repo())
        try:
            insights = await service.list_insights(product_id=product_id)
        except DecisionDomainError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {"insights": [item.model_dump(mode="json") for item in insights]}

    @router.post("/opportunities")
    async def create_opportunity(payload: OpportunityCreateRequest) -> dict:
        service = OpportunityService(await repo())
        try:
            opportunity, receipt = await service.create_candidate(**payload.model_dump())
        except DecisionDomainError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "opportunity": opportunity.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.get("/opportunities")
    async def list_opportunities(product_id: str = "") -> dict:
        service = OpportunityService(await repo())
        try:
            opportunities = await service.list_candidates(product_id=product_id)
        except DecisionDomainError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "opportunities": [item.model_dump(mode="json") for item in opportunities]
        }

    @router.patch("/opportunities/{opportunity_id}")
    async def update_opportunity(
        opportunity_id: str, payload: OpportunityUpdateRequest
    ) -> dict:
        service = OpportunityService(await repo())
        try:
            opportunity, receipt = await service.update_candidate(
                opportunity_id, **payload.model_dump()
            )
        except DecisionDomainError as exc:
            status = 404 if exc.code == "opportunity_not_found" else 422
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "opportunity": opportunity.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/opportunities/{opportunity_id}/reject")
    async def reject_opportunity(
        opportunity_id: str, payload: OpportunityActionRequest
    ) -> dict:
        service = OpportunityService(await repo())
        try:
            opportunity, receipt = await service.reject(
                opportunity_id, actor=payload.actor, reason=payload.reason
            )
        except DecisionDomainError as exc:
            status = 404 if exc.code == "opportunity_not_found" else 422
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "opportunity": opportunity.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/opportunities/{opportunity_id}/submit")
    async def submit_opportunity(
        opportunity_id: str, payload: OpportunityActionRequest
    ) -> dict:
        service = OpportunityService(await repo())
        try:
            opportunity, receipt = await service.submit_for_approval(
                opportunity_id, actor=payload.actor, reason=payload.reason
            )
        except DecisionDomainError as exc:
            status = 404 if exc.code == "opportunity_not_found" else 422
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "opportunity": opportunity.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/opportunities/{opportunity_id}/evaluate")
    async def evaluate_opportunity(
        opportunity_id: str, payload: OpportunityEvaluateRequest
    ) -> dict:
        service = EvaluateService(await repo())
        try:
            opportunity, receipt = await service.evaluate(
                opportunity_id, **payload.model_dump()
            )
        except DecisionDomainError as exc:
            status = 404 if exc.code == "opportunity_not_found" else 422
            raise HTTPException(
                status_code=status, detail={"code": exc.code, "message": exc.message}
            ) from exc
        return {
            "opportunity": opportunity.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/opportunities/{opportunity_id}/approve")
    async def approve_opportunity(
        opportunity_id: str, payload: ActorReasonRequest
    ) -> dict:
        service = ApprovalService(await repo())
        try:
            opportunity, receipt = await service.approve_opportunity(
                opportunity_id,
                actor_open_id=payload.actor_open_id,
                reason=payload.reason,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "opportunity": opportunity.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/opportunities/{opportunity_id}/prd")
    async def create_prd(opportunity_id: str, payload: PrdCreateRequest | None = None) -> dict:
        body = payload or PrdCreateRequest()
        service = PrdLifecycleService(await repo())
        try:
            version, receipt = await service.create_from_approved_opportunity(
                opportunity_id,
                title=body.title,
                markdown=body.markdown,
                actor_open_id=body.actor_open_id,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "prd_version": version.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/owners")
    async def upsert_owner(payload: ProductOwnerRequest) -> dict:
        repository = await repo()
        saved = await repository.upsert_product_owner(ProductOwnerConfig(**payload.model_dump()))
        if not saved.ok or saved.value is None:
            raise HTTPException(status_code=500, detail={"code": "owner_persist_failed"})
        return {"owner": saved.value.model_dump(mode="json")}

    @router.post("/prd-versions/{prd_version_id}/assess")
    async def assess_prd(prd_version_id: str, payload: ActorReasonRequest | None = None) -> dict:
        service = PrdLifecycleService(await repo())
        try:
            version, assessment, receipt = await service.assess_quality(
                prd_version_id,
                actor_open_id=(payload.actor_open_id if payload else ""),
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "prd_version": version.model_dump(mode="json"),
            "quality_assessment": assessment.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/prd-versions/{prd_version_id}/approve")
    async def approve_prd(prd_version_id: str, payload: ActorReasonRequest) -> dict:
        service = PrdLifecycleService(await repo())
        try:
            version, receipt = await service.approve(
                prd_version_id,
                actor_open_id=payload.actor_open_id,
                reason=payload.reason,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "prd_version": version.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/prd-versions/{prd_version_id}/waive")
    async def waive_prd(prd_version_id: str, payload: WaiveRequest) -> dict:
        service = PrdLifecycleService(await repo())
        try:
            version, receipt = await service.waive(
                prd_version_id,
                actor_open_id=payload.actor_open_id,
                reason=payload.reason,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "prd_version": version.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/prd-versions/{prd_version_id}/ready")
    async def ready_prd(prd_version_id: str, payload: ActorReasonRequest) -> dict:
        service = PrdLifecycleService(await repo())
        try:
            version, receipt = await service.mark_ready_for_delivery(
                prd_version_id,
                actor_open_id=payload.actor_open_id,
                reason=payload.reason,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "prd_version": version.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.post("/prd-versions/{prd_version_id}/revise")
    async def revise_prd(prd_version_id: str, payload: PrdReviseRequest) -> dict:
        service = PrdLifecycleService(await repo())
        try:
            version, receipt = await service.revise(
                prd_version_id,
                title=payload.title,
                markdown=payload.markdown,
                actor_open_id=payload.actor_open_id,
                reason=payload.reason,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "prd_version": version.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.get("/prd-versions")
    async def list_prd_versions(prd_id: str = "", product_id: str = "") -> dict:
        result = await (await repo()).list_prd_versions(prd_id=prd_id, product_id=product_id)
        return {
            "prd_versions": [
                item.model_dump(mode="json") for item in (result.value or [])
            ]
        }

    @router.post("/prd-versions/{prd_version_id}/export")
    async def export_prd(prd_version_id: str, payload: DeliveryExportRequest) -> dict:
        repository = await repo()
        bitable = FeishuBitableDeliveryTarget(
            app_token=payload.app_token,
            table_id=payload.table_id,
            field_mapping=payload.field_mapping,
            base_url=payload.base_url,
        )
        if payload.enable_project or payload.target == "feishu_project":
            target = FeishuProjectDeliveryTarget(
                project_key=payload.project_key,
                field_mapping=payload.field_mapping,
                fallback=bitable,
                enabled=bool(payload.project_key),
            )
        else:
            target = bitable
        service = DeliveryService(repository)
        try:
            export, receipt = await service.export_prd(
                prd_version_id,
                target=target,
                actor_open_id=payload.actor_open_id,
            )
        except DecisionDomainError as exc:
            raise _domain_http(exc) from exc
        return {
            "delivery": export.model_dump(mode="json"),
            **receipt.model_dump(mode="json"),
        }

    @router.get("/deliveries")
    async def list_deliveries(prd_version_id: str = "", product_id: str = "") -> dict:
        result = await (await repo()).list_delivery_exports(
            prd_version_id=prd_version_id, product_id=product_id
        )
        return {
            "deliveries": [item.model_dump(mode="json") for item in (result.value or [])]
        }

    return router


def build_default_sync_stack(
    *,
    db_path: str | Path,
    artifacts_root: str | Path | None = None,
    admin_open_ids: list[str] | None = None,
) -> tuple[ProductDecisionRepository, EvidenceSyncService, DailyEvidenceSyncScheduler]:
    repository = ProductDecisionRepository(db_path)
    service = EvidenceSyncService(
        repository,
        job_queue=LocalJobQueue(),
        notifications=NullNotificationSink(),
        artifacts=LocalArtifactStore(artifacts_root or Path("data") / "artifacts"),
        client=FeishuEvidenceClient(),
        admin_open_ids=admin_open_ids,
    )
    scheduler = DailyEvidenceSyncScheduler(service)
    return repository, service, scheduler
