"""Project-scoped connector sync HTTP routes."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .service import build_sync_idempotency_key, enqueue_sync_task, list_connector_summaries
from .store import ConnectorSyncStore


class ManualSyncRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    resource: str = ""


def register_connector_sync_routes(
    router: APIRouter,
    *,
    sync_store: ConnectorSyncStore,
    get_project: Callable[[str], dict[str, Any]],
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> None:
    @router.get("/projects/{project_id}/connectors")
    async def list_project_connectors(project_id: str):
        get_project(project_id)
        return {
            "project_id": project_id,
            "connectors": list_connector_summaries(sync_store, project_id=project_id),
        }

    @router.post("/projects/{project_id}/connectors/sync")
    async def enqueue_project_connector_sync(project_id: str, payload: ManualSyncRequest):
        get_project(project_id)
        provider = payload.provider.strip().lower()
        body: dict[str, Any] = {"trigger": "manual"}
        if payload.resource.strip():
            body["resource"] = payload.resource.strip()
        idempotency_key = build_sync_idempotency_key(
            project_id,
            provider,
            resource=payload.resource.strip(),
        )
        try:
            task = enqueue_sync_task(
                sync_store,
                project_id=project_id,
                provider=provider,
                payload=body,
                idempotency_key=idempotency_key,
                new_id=new_id,
                now=now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project_id": project_id,
            "task": task,
            "deduplicated": bool(task.get("deduplicated")),
        }
