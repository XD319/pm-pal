from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from prd_pal.connectors.sync import ConnectorSyncStore

from .config_store import FeishuConfigStore
from .crypto import FeishuDecryptError
from .events import decode_request_body, handle_feishu_event_payload, resolve_event_payload
from .models import (
    FeishuChallengeEvent,
    FeishuClarificationRequest,
    FeishuEventEnvelope,
    FeishuWorkspaceClarificationRequest,
    FeishuWorkspaceDeriveRequest,
    FeishuWorkspaceRoadmapUpdateRequest,
)
from .security import (
    FeishuSecuritySettings,
    FeishuSignatureVerificationError,
    get_feishu_security_settings,
    verify_feishu_signature,
)

SubmitReviewRun = Callable[..., Awaitable[dict[str, str]]]
SubmitClarification = Callable[..., Awaitable[dict[str, Any]]]
ListWorkspaceOverviews = Callable[..., Awaitable[dict[str, Any]]]
GetWorkspaceOverview = Callable[..., Awaitable[dict[str, Any]]]
ListWorkspaceVersions = Callable[..., Awaitable[dict[str, Any]]]
StartWorkspaceReview = Callable[..., Awaitable[dict[str, Any]]]
SubmitWorkspaceClarification = Callable[..., Awaitable[dict[str, Any]]]
DeriveWorkspaceVersion = Callable[..., Awaitable[dict[str, Any]]]
GetWorkspaceDiff = Callable[..., Awaitable[dict[str, Any]]]
UpdateWorkspaceRoadmap = Callable[..., Awaitable[dict[str, Any]]]

_LEGACY_REVIEW_GONE = JSONResponse(
    status_code=410,
    content={
        "detail": {
            "code": "legacy_endpoint_removed",
            "message": (
                "Legacy Feishu review submission was removed. "
                "Use POST /api/projects/{project_id}/reviews instead."
            ),
        }
    },
)


def _invalid_signature_response(exc: FeishuSignatureVerificationError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


def _resolve_event_security_settings(
    config_store: FeishuConfigStore | None,
) -> FeishuSecuritySettings:
    base = get_feishu_security_settings()
    if config_store is None:
        return base
    config = config_store.get("")
    webhook_secret = config.resolved_webhook_secret() or base.webhook_secret
    encrypt_key = config.resolved_encrypt_key() or base.encrypt_key
    return FeishuSecuritySettings(
        signature_disabled=base.signature_disabled,
        webhook_secret=webhook_secret,
        encrypt_key=encrypt_key,
        tolerance_sec=base.tolerance_sec,
    )


def create_feishu_router(
    *,
    submit_review_run: SubmitReviewRun,
    submit_clarification: SubmitClarification,
    list_workspace_overviews: ListWorkspaceOverviews,
    get_workspace_overview: GetWorkspaceOverview,
    list_workspace_versions: ListWorkspaceVersions,
    start_workspace_review: StartWorkspaceReview,
    submit_workspace_clarification: SubmitWorkspaceClarification,
    derive_workspace_version: DeriveWorkspaceVersion,
    get_workspace_diff: GetWorkspaceDiff,
    update_workspace_roadmap: UpdateWorkspaceRoadmap,
    sync_store: ConnectorSyncStore | None = None,
    config_store: FeishuConfigStore | None = None,
    new_id: Callable[[str], str] | None = None,
    now: Callable[[], str] | None = None,
) -> APIRouter:
    _ = submit_review_run
    router = APIRouter(prefix="/api/feishu", tags=["feishu"])

    @router.post("/events")
    async def handle_feishu_events(request: Request) -> JSONResponse:
        body = await request.body()
        security_settings = _resolve_event_security_settings(config_store)
        try:
            verify_feishu_signature(
                headers=request.headers, body=body, settings=security_settings
            )
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)

        try:
            raw_payload = decode_request_body(body)
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=400,
                content={"detail": {"code": "invalid_feishu_payload", "message": "Invalid JSON body."}},
            )

        encrypt_key = security_settings.encrypt_key
        if config_store is not None:
            encrypt_key = config_store.get("").resolved_encrypt_key() or encrypt_key
        try:
            payload = resolve_event_payload(raw_payload, encrypt_key=encrypt_key)
        except FeishuDecryptError as exc:
            return JSONResponse(
                status_code=400,
                content={"detail": {"code": "invalid_feishu_payload", "message": str(exc)}},
            )

        if sync_store is not None and config_store is not None and new_id and now:
            outcome = handle_feishu_event_payload(
                payload,
                sync_store=sync_store,
                config_store=config_store,
                new_id=new_id,
                now=now,
            )
            if outcome.get("kind") == "challenge":
                return JSONResponse(
                    status_code=200,
                    content={"challenge": outcome["challenge"]},
                )
            return JSONResponse(status_code=200, content={"code": 0, "message": "ok", **outcome})

        envelope = FeishuEventEnvelope.model_validate(payload)
        if envelope.is_challenge():
            challenge = FeishuChallengeEvent.model_validate(payload)
            return JSONResponse(
                status_code=200, content={"challenge": challenge.challenge}
            )

        return JSONResponse(status_code=200, content={"code": 0, "message": "ok"})

    @router.post("/submit", response_model=None)
    async def submit_feishu_review(request: Request) -> JSONResponse:
        body = await request.body()
        try:
            verify_feishu_signature(headers=request.headers, body=body)
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)
        return _LEGACY_REVIEW_GONE

    @router.get("/workspaces", response_model=None)
    async def list_feishu_workspaces(
        request: Request,
        limit: int = 20,
    ) -> Any:
        return await list_workspace_overviews(request=request, limit=limit)

    @router.get("/workspaces/{workspace_id}", response_model=None)
    async def get_feishu_workspace(workspace_id: str, request: Request) -> Any:
        return await get_workspace_overview(workspace_id=workspace_id, request=request)

    @router.get(
        "/workspaces/{workspace_id}/artifacts/{artifact_key}/versions",
        response_model=None,
    )
    async def get_feishu_workspace_versions(
        workspace_id: str, artifact_key: str, request: Request
    ) -> Any:
        return await list_workspace_versions(
            workspace_id=workspace_id, artifact_key=artifact_key, request=request
        )

    @router.post(
        "/workspaces/{workspace_id}/artifacts/{artifact_key}/versions/{version_id}/review",
        response_model=None,
    )
    async def review_feishu_workspace_version(
        workspace_id: str,
        artifact_key: str,
        version_id: str,
        request: Request,
    ) -> JSONResponse:
        body = await request.body()
        try:
            verify_feishu_signature(headers=request.headers, body=body)
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)
        return await start_workspace_review(
            workspace_id=workspace_id,
            artifact_key=artifact_key,
            version_id=version_id,
            request=request,
        )

    @router.post("/workspaces/{workspace_id}/clarification", response_model=None)
    async def submit_feishu_workspace_clarification(
        workspace_id: str,
        payload: FeishuWorkspaceClarificationRequest,
        request: Request,
    ) -> Any:
        body = await request.body()
        try:
            verify_feishu_signature(headers=request.headers, body=body)
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)
        try:
            return await submit_workspace_clarification(
                workspace_id=workspace_id,
                request=request,
                payload=payload,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "run_not_found", "message": str(exc)}
            ) from exc
        except PermissionError as exc:
            message = str(exc)
            code = (
                "feishu_context_required"
                if "requires" in message
                else "run_access_denied"
            )
            raise HTTPException(
                status_code=403, detail={"code": code, "message": message}
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "clarification_unavailable",
                    "message": str(exc),
                    "run_id": payload.run_id,
                },
            ) from exc
        except TypeError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_clarification_payload",
                    "message": str(exc),
                    "run_id": payload.run_id,
                },
            ) from exc

    @router.post(
        "/workspaces/{workspace_id}/versions/{version_id}/derive", response_model=None
    )
    async def derive_feishu_workspace_version(
        workspace_id: str,
        version_id: str,
        payload: FeishuWorkspaceDeriveRequest,
        request: Request,
    ) -> Any:
        body = await request.body()
        try:
            verify_feishu_signature(headers=request.headers, body=body)
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)
        return await derive_workspace_version(
            workspace_id=workspace_id,
            version_id=version_id,
            request=request,
            payload=payload,
        )

    @router.get("/workspaces/{workspace_id}/diff", response_model=None)
    async def get_feishu_workspace_version_diff(
        workspace_id: str,
        request: Request,
        from_version: str,
        to_version: str,
    ) -> Any:
        return await get_workspace_diff(
            workspace_id=workspace_id,
            from_version=from_version,
            to_version=to_version,
            request=request,
        )

    @router.post("/workspaces/{workspace_id}/roadmap", response_model=None)
    async def update_feishu_workspace_roadmap(
        workspace_id: str,
        payload: FeishuWorkspaceRoadmapUpdateRequest,
        request: Request,
    ) -> Any:
        body = await request.body()
        try:
            verify_feishu_signature(headers=request.headers, body=body)
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)
        return await update_workspace_roadmap(
            workspace_id=workspace_id,
            request=request,
            payload=payload,
        )

    @router.post("/clarification", response_model=None)
    async def submit_feishu_clarification(
        payload: FeishuClarificationRequest, request: Request
    ) -> Any:
        body = await request.body()
        try:
            verify_feishu_signature(headers=request.headers, body=body)
        except FeishuSignatureVerificationError as exc:
            return _invalid_signature_response(exc)

        try:
            return await submit_clarification(
                request=request,
                payload=payload,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "run_not_found", "message": str(exc)},
            ) from exc
        except PermissionError as exc:
            message = str(exc)
            code = (
                "feishu_context_required"
                if "requires" in message
                else "run_access_denied"
            )
            raise HTTPException(
                status_code=403,
                detail={"code": code, "message": message},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "clarification_unavailable",
                    "message": str(exc),
                    "run_id": payload.run_id,
                },
            ) from exc
        except TypeError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_clarification_payload",
                    "message": str(exc),
                    "run_id": payload.run_id,
                },
            ) from exc

    return router
