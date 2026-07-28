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
from .sync_service import EvidenceSyncService


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
        result = await (await repo()).list_evidence(
            product_id=product_id, query=query, limit=limit
        )
        return {
            "evidence": [item.model_dump(mode="json") for item in (result.value or [])]
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
