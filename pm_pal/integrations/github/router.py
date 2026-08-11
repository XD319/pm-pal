"""GitHub webhook router for realtime connector sync."""
from __future__ import annotations

import json
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pm_pal.connectors.sync import ConnectorSyncStore

from .config_store import GitHubConfigStore
from .events import decode_request_body, handle_github_event_payload
from .security import (
    GitHubSecuritySettings,
    GitHubSignatureVerificationError,
    get_github_security_settings,
    verify_github_signature,
)


def _invalid_signature_response(exc: GitHubSignatureVerificationError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


def _resolve_event_security_settings(
    config_store: GitHubConfigStore | None,
) -> GitHubSecuritySettings:
    base = get_github_security_settings()
    if config_store is None:
        return base
    config = config_store.get("")
    webhook_secret = config.resolved_webhook_secret() or base.webhook_secret
    return GitHubSecuritySettings(
        signature_disabled=base.signature_disabled,
        webhook_secret=webhook_secret,
    )


def create_github_router(
    *,
    sync_store: ConnectorSyncStore | None = None,
    config_store: GitHubConfigStore | None = None,
    new_id: Callable[[str], str] | None = None,
    now: Callable[[], str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/github", tags=["github"])

    @router.post("/events")
    async def handle_github_events(request: Request) -> JSONResponse:
        body = await request.body()
        security_settings = _resolve_event_security_settings(config_store)
        try:
            verify_github_signature(
                headers=request.headers, body=body, settings=security_settings
            )
        except GitHubSignatureVerificationError as exc:
            return _invalid_signature_response(exc)

        try:
            payload = decode_request_body(body)
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": {
                        "code": "invalid_github_payload",
                        "message": "Invalid JSON body.",
                    }
                },
            )

        if sync_store is not None and config_store is not None and new_id and now:
            headers = {str(key): str(value) for key, value in request.headers.items()}
            outcome = handle_github_event_payload(
                payload,
                headers=headers,
                sync_store=sync_store,
                config_store=config_store,
                new_id=new_id,
                now=now,
            )
            return JSONResponse(status_code=200, content={"ok": True, **outcome})

        return JSONResponse(status_code=200, content={"ok": True})

    return router
