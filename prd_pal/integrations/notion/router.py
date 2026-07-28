"""Notion webhook HTTP routes."""
from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from prd_pal.connectors.sync import ConnectorSyncStore

from .config_store import NotionConfigStore, normalize_notion_page_id
from .events import decode_request_body, extract_entity_id, handle_notion_event_payload
from .security import (
    NotionSecuritySettings,
    NotionSignatureVerificationError,
    get_notion_security_settings,
    verify_notion_signature,
)


def _invalid_signature_response(exc: NotionSignatureVerificationError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


def _resolve_event_security_settings(
    config_store: NotionConfigStore | None,
    payload: dict,
) -> NotionSecuritySettings:
    base = get_notion_security_settings()
    if config_store is None:
        return base
    entity_id = extract_entity_id(payload)
    if entity_id:
        match = config_store.find_project_for_page_id(normalize_notion_page_id(entity_id))
        if match is not None:
            project_id, _mapping = match
            config = config_store.get(project_id)
            signing_secret = config.resolved_signing_secret() or base.signing_secret
            return NotionSecuritySettings(
                signature_disabled=base.signature_disabled,
                signing_secret=signing_secret,
            )
    return NotionSecuritySettings(
        signature_disabled=base.signature_disabled,
        signing_secret=base.signing_secret,
    )


def create_notion_router(
    *,
    sync_store: ConnectorSyncStore | None = None,
    config_store: NotionConfigStore | None = None,
    new_id: Callable[[str], str] | None = None,
    now: Callable[[], str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/notion", tags=["notion"])

    @router.post("/events")
    async def handle_notion_events(request: Request) -> JSONResponse:
        body = await request.body()
        try:
            raw_payload = decode_request_body(body)
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": "invalid_notion_payload",
                        "message": "Invalid JSON body.",
                    }
                },
            )

        if not raw_payload.get("verification_token"):
            security_settings = _resolve_event_security_settings(config_store, raw_payload)
            try:
                verify_notion_signature(
                    headers=request.headers,
                    body=body,
                    settings=security_settings,
                )
            except NotionSignatureVerificationError as exc:
                return _invalid_signature_response(exc)

        if sync_store is not None and config_store is not None and new_id and now:
            outcome = handle_notion_event_payload(
                raw_payload,
                sync_store=sync_store,
                config_store=config_store,
                new_id=new_id,
                now=now,
            )
            if outcome.get("kind") == "verification":
                return JSONResponse(status_code=200, content=outcome)
            return JSONResponse(status_code=200, content={"code": 0, "message": "ok", **outcome})

        verification_token = str(raw_payload.get("verification_token") or "").strip()
        if verification_token:
            return JSONResponse(
                status_code=200,
                content={"kind": "verification", "verification_token": verification_token},
            )
        return JSONResponse(status_code=200, content={"code": 0, "message": "ok"})

    return router
