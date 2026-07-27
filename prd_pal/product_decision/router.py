from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .models import EvidenceRecord, EvidenceSource
from .repository import ProductDecisionRepository


class SourceCreateRequest(BaseModel):
    product_id: str = ""
    source_type: str = Field(min_length=1)
    source_url: str = ""
    display_name: str = ""
    metadata: dict = Field(default_factory=dict)


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
    metadata: dict = Field(default_factory=dict)


class EvidenceSyncRequest(BaseModel):
    cursor: str = ""
    records: list[EvidenceSyncItem] = Field(default_factory=list)


def create_product_decision_router(*, db_path: str | Path) -> APIRouter:
    router = APIRouter(prefix="/api/decision", tags=["product-decision"])

    async def repo() -> ProductDecisionRepository:
        repository = ProductDecisionRepository(db_path)
        result = await repository.initialize()
        if not result.ok:
            raise HTTPException(status_code=500, detail={"code": "decision_repository_error", "message": result.error.message if result.error else "initialize failed"})
        return repository

    @router.post("/sources")
    async def register_source(payload: SourceCreateRequest) -> dict:
        repository = await repo()
        source = EvidenceSource(id=f"source-{uuid.uuid4().hex[:12]}", **payload.model_dump())
        saved = await repository.upsert_source(source)
        if not saved.ok or saved.value is None:
            raise HTTPException(status_code=500, detail={"code": "source_persist_failed"})
        return {"source": saved.value.model_dump(mode="json")}

    @router.get("/sources")
    async def list_sources(product_id: str = "") -> dict:
        result = await (await repo()).list_sources(product_id)
        return {"sources": [item.model_dump(mode="json") for item in (result.value or [])]}

    @router.post("/sources/{source_id}/sync")
    async def sync_source(source_id: str, payload: EvidenceSyncRequest) -> dict:
        repository = await repo()
        source = await repository.get_source(source_id)
        if not source.ok or source.value is None:
            raise HTTPException(status_code=404, detail={"code": "source_not_found", "message": source_id})
        records = [EvidenceRecord(id=f"evidence-{uuid.uuid4().hex[:12]}", source_id=source_id, product_id=source.value.product_id, **item.model_dump()) for item in payload.records]
        result = await repository.sync_evidence(source_id, records, cursor=payload.cursor)
        if not result.ok:
            await repository.mark_sync_failed(source_id, result.error.message if result.error else "sync failed")
            raise HTTPException(status_code=422, detail={"code": "source_sync_failed", "message": result.error.message if result.error else "sync failed"})
        return {"source_id": source_id, "synced_count": len(result.value or []), "evidence": [item.model_dump(mode="json") for item in (result.value or [])]}

    @router.get("/evidence")
    async def list_evidence(product_id: str = "", query: str = "", limit: int = 100) -> dict:
        result = await (await repo()).list_evidence(product_id=product_id, query=query, limit=limit)
        return {"evidence": [item.model_dump(mode="json") for item in (result.value or [])]}

    return router
