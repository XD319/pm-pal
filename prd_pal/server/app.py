from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from prd_pal.monitoring import (
    append_audit_event,
    resolve_audit_client_metadata,
)
from prd_pal.integrations.feishu import create_feishu_router
from prd_pal.integrations.github import create_github_router
from prd_pal.integrations.notion import create_notion_router
from prd_pal.run_review import make_run_id
from prd_pal.server.job_registry import JobRegistry
from prd_pal.server.job_state import (
    ClarificationAnswerRequest,
    JobRecord,
    RUN_PROGRESS_FILENAME as RUN_PROGRESS_FILENAME,
    RevisionConfirmRequest,
    RevisionInputRequest,
    RevisionStageRequest,
    job_status_payload as _job_status_payload,
    persisted_status_payload as _persisted_status_payload,
    persist_job_snapshot as _persist_job_snapshot,
    result_unavailable_detail as _result_unavailable_detail,
    run_job as _run_job_impl,
    terminal_payload_for_job as _terminal_payload_for_job,
    terminal_payload_for_run_dir as _terminal_payload_for_run_dir,
)
from prd_pal.server.feishu_context import (
    build_result_page_payload as _build_result_page_payload_impl,
    build_run_entry_context as _build_run_entry_context_impl,
    entry_context_path as _entry_context_path_impl,
    extract_result_page_context as _extract_result_page_context,
    is_feishu_audit_context as _is_feishu_audit_context,
    merge_result_page_context as _merge_result_page_context,
    persist_run_entry_context as _persist_run_entry_context_impl,
    read_run_entry_context as _read_run_entry_context,
    resolve_request_feishu_context as _resolve_request_feishu_context,
    resolve_run_feishu_context as _resolve_run_feishu_context_impl,
    validate_feishu_run_access as _validate_feishu_run_access,
)
from prd_pal.server.report_exports import (
    build_report_csv as _build_report_csv,
    build_report_html as _build_report_html,
    load_report_payload as _load_report_payload,
)
from prd_pal.server.security import (
    authenticate_request,
    client_ip,
    controlled_error_response,
    enforce_submission_rate_limit,
    reset_submission_rate_limits as _reset_submission_rate_limits,  # noqa: F401
    security_settings,
    should_skip_request_logging,
)
from prd_pal.server.sse import ProgressBroadcaster
from prd_pal.server.pm_router import create_pm_router
from prd_pal.server.project_space import create_project_space_router, new_id, now
from prd_pal.product_decision.router import (
    build_default_sync_stack,
    create_product_decision_router,
)
from prd_pal.service.roadmap_service import (
    diff_roadmap_versions,
    generate_constrained_roadmap,
    generate_roadmap_for_run,
)
from prd_pal.service.report_service import RUN_ID_PATTERN
from prd_pal.service.review_service import (
    ReviewArtifactNotFoundError,
    ReviewResultNotReadyError,
    ReviewRunNotFoundError,
    answer_review_clarification,
    answer_review_clarification_async,
    confirm_revision_action,
    get_review_artifact_preview_payload,
    get_review_result_payload,
    record_revision_input,
    record_revision_stage_decision,
)
from prd_pal.service.revision_service import generate_revision_for_run_async
from prd_pal.templates import TemplateRegistryError, list_template_records
from prd_pal.utils.logging import get_logger
from prd_pal.workspace import (
    ArtifactRepository,
    ArtifactVersion,
    ArtifactVersionStatus,
    WorkspaceRepository,
    WorkspaceState,
)

OUTPUTS_ROOT = Path("outputs")
WORKSPACE_DB_PATH = Path("data") / "workspace.sqlite3"
PROJECT_SPACE_DB_PATH = Path("data") / "project_space.sqlite3"
PRODUCT_DECISION_DB_PATH = Path("data") / "product_decision.sqlite3"
PRODUCT_DECISION_ARTIFACTS_ROOT = Path("data") / "artifacts"
FRONTEND_DIST_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "dist"
ARTIFACT_REVIEW_RUN_TABLE = "artifact_review_runs"
log = get_logger("server.http")

(
    _product_decision_repository,
    _product_decision_sync_service,
    _product_decision_scheduler,
    _product_decision_job_queue,
) = build_default_sync_stack(
    db_path=PRODUCT_DECISION_DB_PATH,
    artifacts_root=PRODUCT_DECISION_ARTIFACTS_ROOT,
)


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    load_dotenv()
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    PRODUCT_DECISION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRODUCT_DECISION_ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    await _job_registry.recover()
    await _product_decision_repository.initialize()
    await _product_decision_job_queue.initialize()
    await _product_decision_job_queue.recover()
    _product_decision_scheduler.start()
    app.state.product_decision_sync = _product_decision_sync_service
    app.state.product_decision_scheduler = _product_decision_scheduler
    app.state.product_decision_job_queue = _product_decision_job_queue
    app.state.startup_completed = True
    try:
        yield
    finally:
        await _product_decision_scheduler.stop()
        app.state.startup_completed = False


app = FastAPI(title="Requirement Review V2 API", version="2.0", lifespan=_app_lifespan)
_job_registry = JobRegistry(lambda: OUTPUTS_ROOT)
_jobs = _job_registry.jobs
_jobs_lock = _job_registry.lock


def _outputs_root_writable() -> bool:
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    probe_path = OUTPUTS_ROOT / ".ready-probe"
    probe_path.write_text("ok", encoding="utf-8")
    probe_path.unlink(missing_ok=True)
    return True


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def _run_job(
    job: JobRecord,
    *,
    prd_text: str | None = None,
    prd_path: str | None = None,
    source: str | None = None,
    mode: str | None = None,
    llm_options: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
) -> None:
    await _run_job_impl(
        job,
        outputs_root=OUTPUTS_ROOT,
        prd_text=prd_text,
        prd_path=prd_path,
        source=source,
        mode=mode,
        llm_options=llm_options,
        audit_context=audit_context,
    )


async def _enqueue_review_run(
    *,
    prd_text: str | None = None,
    prd_path: str | None = None,
    source: str | None = None,
    mode: str | None = None,
    llm_options: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_id, run_dir = await _allocate_unique_run_dir()
    entry_context = _persist_run_entry_context(run_dir, audit_context)

    if isinstance(audit_context, dict) and audit_context:
        append_audit_event(
            run_dir,
            operation="review_submission",
            status="accepted",
            run_id=run_id,
            audit_context=audit_context,
            details={
                "source_origin": str(
                    (entry_context or {}).get("source_origin") or "web"
                ).strip(),
                "entry_mode": str(
                    (entry_context or {}).get("entry_mode") or "direct"
                ).strip(),
            },
        )

    job = JobRecord(run_id=run_id, run_dir=run_dir)
    _persist_job_snapshot(job)
    task = asyncio.create_task(
        _run_job(
            job,
            prd_text=prd_text,
            prd_path=prd_path,
            source=source,
            mode=mode,
            llm_options=llm_options,
            **(
                {"audit_context": audit_context}
                if isinstance(audit_context, dict) and audit_context
                else {}
            ),
        )
    )
    job.task = task
    await _job_registry.register(job)
    payload: dict[str, Any] = {"run_id": run_id}
    if _is_feishu_audit_context(audit_context):
        payload["result_page"] = _build_result_page_payload(
            run_id, audit_context=audit_context, run_dir=run_dir
        )
    return payload


async def _allocate_unique_run_dir(*, max_attempts: int = 30) -> tuple[str, Path]:
    OUTPUTS_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(max(1, int(max_attempts))):
        run_id = make_run_id()
        run_dir = OUTPUTS_ROOT / run_id
        async with _jobs_lock:
            if run_id not in _jobs and not run_dir.exists():
                run_dir.mkdir(parents=True, exist_ok=False)
                return run_id, run_dir
        await asyncio.sleep(0.05)
    raise RuntimeError("unable to allocate unique run_id")


def _submit_review_clarification_internal(
    *,
    run_id: str,
    answers: list[dict[str, Any]],
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = OUTPUTS_ROOT / str(run_id or "").strip()
    _validate_feishu_run_access(
        run_dir=run_dir,
        context=resolve_audit_client_metadata(audit_context),
    )
    return answer_review_clarification(
        run_id=run_id,
        answers=answers,
        outputs_root=OUTPUTS_ROOT,
        audit_context=audit_context,
    )


def _merge_audit_context_with_request(
    audit_context: dict[str, Any] | None,
    request_context: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(audit_context) if isinstance(audit_context, dict) else {}
    merged_client_metadata = _merge_result_page_context(
        request_context,
        resolve_audit_client_metadata(audit_context),
    )
    if merged_client_metadata:
        base["client_metadata"] = merged_client_metadata
    if not str(base.get("actor") or "").strip():
        base["actor"] = (
            str(merged_client_metadata.get("open_id") or "feishu").strip() or "feishu"
        )
    return base


def _entry_context_path(run_dir: Path) -> Path:
    return _entry_context_path_impl(run_dir)


def _is_feishu_audit_context(audit_context: dict[str, Any] | None) -> bool:
    if not isinstance(audit_context, dict):
        return False
    if str(audit_context.get("source") or "").strip().lower() == "feishu":
        return True
    client_metadata = resolve_audit_client_metadata(audit_context)
    return str(client_metadata.get("trigger_source") or "").strip().lower() == "feishu"


def _build_run_entry_context(
    audit_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return _build_run_entry_context_impl(audit_context)


def _persist_run_entry_context(
    run_dir: Path, audit_context: dict[str, Any] | None
) -> dict[str, Any] | None:
    return _persist_run_entry_context_impl(run_dir, audit_context)


def _build_result_page_payload(
    run_id: str,
    *,
    audit_context: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
    run_dir: Path | None = None,
) -> dict[str, str]:
    return _build_result_page_payload_impl(
        run_id,
        audit_context=audit_context,
        request_context=request_context,
        run_dir=run_dir,
    )


def _resolve_run_feishu_context(
    run_id: str, context: dict[str, Any] | None
) -> dict[str, str]:
    return _resolve_run_feishu_context_impl(
        run_id, context, outputs_root=OUTPUTS_ROOT
    )


def _enforce_run_access(request: Request | None, run_id: str) -> None:
    run_dir = OUTPUTS_ROOT / str(run_id or "").strip()
    if not run_dir.exists() or not run_dir.is_dir():
        return
    try:
        _validate_feishu_run_access(
            run_dir=run_dir, context=_resolve_request_feishu_context(request)
        )
    except PermissionError as exc:
        message = str(exc)
        code = (
            "feishu_context_required" if "requires" in message else "run_access_denied"
        )
        raise HTTPException(
            status_code=403, detail={"code": code, "message": message}
        ) from exc


def _run_id_from_run_level_api_path(path: str) -> str:
    parts = [part for part in str(path or "").strip().split("/") if part]
    if (
        len(parts) >= 5
        and parts[0] == "api"
        and parts[1] == "projects"
        and parts[3] == "reviews"
    ):
        candidate = parts[4]
        if RUN_ID_PATTERN.fullmatch(candidate):
            return candidate
    return ""


def _request_has_valid_feishu_run_context(request: Request) -> bool:
    run_id = _run_id_from_run_level_api_path(request.url.path)
    if not run_id:
        return False

    run_dir = OUTPUTS_ROOT / run_id
    entry_context = _read_run_entry_context(run_dir)
    if str(entry_context.get("source_origin") or "").strip().lower() != "feishu":
        return False

    request_context = _resolve_request_feishu_context(request)
    if not request_context:
        return False
    try:
        _validate_feishu_run_access(run_dir=run_dir, context=request_context)
    except PermissionError:
        return False
    return True


def _workspace_context_from_request(request: Request | None) -> dict[str, str]:
    return _resolve_request_feishu_context(request)


def _build_revision_stage_payload(*, run_id: str) -> dict[str, Any]:
    run_dir = OUTPUTS_ROOT / str(run_id or "").strip()
    report_path = run_dir / "report.json"
    if not report_path.exists() or not report_path.is_file():
        return {
            "status": "unavailable",
            "decision": "",
            "entered_revision": False,
            "available": False,
            "decision_required": False,
            "allow_continue_without_revision": False,
            "revision_confirmed": False,
            "confirmed_revision_ref": "",
            "draft_revision_ref": "",
            "preferred_prd_source": "original_prd",
            "preferred_prd_ref": "report.requirement_doc",
            "updated_at": "",
        }
    try:
        result_payload = get_review_result_payload(
            run_id=run_id, outputs_root=OUTPUTS_ROOT
        )
    except (ReviewRunNotFoundError, ReviewResultNotReadyError, ValueError):
        return {
            "status": "unavailable",
            "decision": "",
            "entered_revision": False,
            "available": False,
            "decision_required": False,
            "allow_continue_without_revision": False,
            "revision_confirmed": False,
            "confirmed_revision_ref": "",
            "draft_revision_ref": "",
            "preferred_prd_source": "original_prd",
            "preferred_prd_ref": "report.requirement_doc",
            "updated_at": "",
        }
    revision_stage = result_payload.get("revision_stage")
    return (
        dict(revision_stage)
        if isinstance(revision_stage, dict)
        else {
            "status": "unavailable",
            "decision": "",
            "entered_revision": False,
            "available": False,
            "decision_required": False,
            "allow_continue_without_revision": False,
            "revision_confirmed": False,
            "confirmed_revision_ref": "",
            "draft_revision_ref": "",
            "preferred_prd_source": "original_prd",
            "preferred_prd_ref": "report.requirement_doc",
            "updated_at": "",
        }
    )


def _extract_workspace_entry_context(workspace: WorkspaceState) -> dict[str, str]:
    metadata = workspace.metadata if isinstance(workspace.metadata, dict) else {}
    expected_open_id = str(
        metadata.get("submitter_open_id") or metadata.get("open_id") or "",
    ).strip()
    expected_tenant_key = str(metadata.get("tenant_key") or "").strip()
    if (not expected_open_id or not expected_tenant_key) and workspace.current_run_id:
        run_entry = _read_run_entry_context(
            OUTPUTS_ROOT / str(workspace.current_run_id).strip()
        )
        expected_open_id = (
            expected_open_id or str(run_entry.get("submitter_open_id") or "").strip()
        )
        expected_tenant_key = (
            expected_tenant_key or str(run_entry.get("tenant_key") or "").strip()
        )
    return {
        "open_id": expected_open_id,
        "tenant_key": expected_tenant_key,
    }


def _validate_feishu_workspace_access(
    *, workspace: WorkspaceState, context: dict[str, Any] | None
) -> None:
    provided = dict(context) if isinstance(context, dict) else {}
    provided_open_id = str(provided.get("open_id") or "").strip()
    provided_tenant_key = str(provided.get("tenant_key") or "").strip()
    if not provided_tenant_key:
        raise PermissionError(
            "This workspace requires the originating Feishu identity context."
        )

    expected = _extract_workspace_entry_context(workspace)
    expected_open_id = str(expected.get("open_id") or "").strip()
    expected_tenant_key = str(expected.get("tenant_key") or "").strip()
    if expected_tenant_key and provided_tenant_key != expected_tenant_key:
        raise PermissionError(
            "This workspace is not accessible from the current Feishu tenant context."
        )
    if expected_open_id and provided_open_id != expected_open_id:
        raise PermissionError(
            "This workspace is not accessible to the current Feishu user."
        )


def _enforce_workspace_access_http(
    *, workspace: WorkspaceState, context: dict[str, Any] | None
) -> None:
    try:
        _validate_feishu_workspace_access(workspace=workspace, context=context)
    except PermissionError as exc:
        message = str(exc)
        code = (
            "feishu_context_required" if "requires" in message else "run_access_denied"
        )
        raise HTTPException(
            status_code=403, detail={"code": code, "message": message}
        ) from exc


async def _load_workspace_or_404(workspace_id: str) -> WorkspaceState:
    repository = WorkspaceRepository(WORKSPACE_DB_PATH)
    await repository.initialize()
    workspace_result = await repository.get_workspace(workspace_id)
    if workspace_result.ok and workspace_result.value is not None:
        return workspace_result.value
    raise HTTPException(
        status_code=404,
        detail={
            "code": "workspace_not_found",
            "message": f"workspace_id not found: {workspace_id}",
        },
    )


def _workspace_version_payload(version: ArtifactVersion) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "artifact_key": version.artifact_key,
        "artifact_type": version.artifact_type,
        "version_number": version.version_number,
        "status": str(version.status),
        "title": version.title,
        "parent_version_id": version.parent_version_id,
        "source_run_id": version.source_run_id,
        "created_at": version.created_at,
        "updated_at": version.updated_at,
    }


def _list_recent_workspace_runs(
    workspace_id: str,
    *,
    limit: int = 5,
    request_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    if not WORKSPACE_DB_PATH.exists():
        return []
    query = f"""
        SELECT run_id, artifact_version_id, review_result_version_id, updated_at
        FROM {ARTIFACT_REVIEW_RUN_TABLE}
        WHERE workspace_id = ?
        ORDER BY updated_at DESC, run_id DESC
        LIMIT ?
    """
    try:
        with sqlite3.connect(WORKSPACE_DB_PATH) as connection:
            rows = connection.execute(
                query, (workspace_id, max(1, int(limit)))
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "run_id": str(row[0] or ""),
            "artifact_version_id": str(row[1] or ""),
            "review_result_version_id": str(row[2] or ""),
            "updated_at": str(row[3] or ""),
            "result_url": _build_workspace_review_result_url(
                str(row[0] or ""),
                request_context=request_context,
            ),
        }
        for row in rows
        if str(row[0] or "").strip()
    ]


def _build_workspace_review_result_url(
    run_id: str, *, request_context: dict[str, Any] | None = None
) -> str:
    normalized_run_id = str(run_id or "").strip()
    base_payload = _build_result_page_payload(
        normalized_run_id,
        request_context=request_context,
        run_dir=OUTPUTS_ROOT / normalized_run_id,
    )
    context = _extract_result_page_context(request_context)
    if not context:
        return base_payload["url"]

    query_params = _merge_result_page_context(context)
    query_params["embed"] = "feishu"
    query_string = urlencode(query_params)
    return (
        f"/run/{normalized_run_id}?{query_string}"
        if query_string
        else base_payload["url"]
    )


async def _list_feishu_workspace_overviews(
    *, request: Request, limit: int = 20
) -> dict[str, Any]:
    repository = WorkspaceRepository(WORKSPACE_DB_PATH)
    await repository.initialize()
    result = await repository.list_workspaces()
    workspaces = result.value if result.ok and isinstance(result.value, list) else []
    context = _workspace_context_from_request(request)

    overviews: list[dict[str, Any]] = []
    for workspace in workspaces:
        try:
            _validate_feishu_workspace_access(workspace=workspace, context=context)
        except PermissionError:
            continue
        current_versions = {
            key: version_id
            for key, version_id in workspace.current_version_ids.items()
            if str(key).strip() and str(version_id).strip()
        }
        overviews.append(
            {
                "workspace_id": workspace.workspace_id,
                "name": workspace.name,
                "status": str(workspace.status),
                "current_run_id": workspace.current_run_id,
                "current_version_ids": current_versions,
                "recent_reviews": _list_recent_workspace_runs(
                    workspace.workspace_id,
                    limit=3,
                    request_context=context,
                ),
            }
        )
        if len(overviews) >= max(1, int(limit)):
            break
    return {"count": len(overviews), "workspaces": overviews}


async def _get_feishu_workspace_overview(
    *, workspace_id: str, request: Request
) -> dict[str, Any]:
    workspace = await _load_workspace_or_404(workspace_id)
    _enforce_workspace_access_http(
        workspace=workspace, context=_workspace_context_from_request(request)
    )
    return {
        "workspace_id": workspace.workspace_id,
        "name": workspace.name,
        "status": str(workspace.status),
        "current_run_id": workspace.current_run_id,
        "current_version_ids": workspace.current_version_ids,
        "versions": [
            _workspace_version_payload(version) for version in workspace.versions
        ],
        "recent_reviews": _list_recent_workspace_runs(
            workspace.workspace_id,
            limit=5,
            request_context=_workspace_context_from_request(request),
        ),
    }


async def _list_feishu_workspace_versions(
    *, workspace_id: str, artifact_key: str, request: Request
) -> dict[str, Any]:
    workspace = await _load_workspace_or_404(workspace_id)
    _enforce_workspace_access_http(
        workspace=workspace, context=_workspace_context_from_request(request)
    )
    versions = workspace.list_versions(artifact_key)
    versions.sort(key=lambda item: item.version_number, reverse=True)
    return {
        "workspace_id": workspace_id,
        "artifact_key": artifact_key,
        "count": len(versions),
        "versions": [_workspace_version_payload(version) for version in versions],
    }


async def _start_feishu_workspace_review(
    *,
    workspace_id: str,
    artifact_key: str,
    version_id: str,
    request: Request,
) -> dict[str, Any]:
    workspace = await _load_workspace_or_404(workspace_id)
    context = _workspace_context_from_request(request)
    _enforce_workspace_access_http(workspace=workspace, context=context)
    version = next(
        (item for item in workspace.versions if item.version_id == version_id), None
    )
    if version is None or version.artifact_key != artifact_key:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "version_not_found",
                "message": f"version_id not found: {version_id}",
            },
        )
    run_response = await _enqueue_review_run(
        prd_path=version.content_path,
        audit_context={
            "source": "feishu",
            "tool_name": "feishu.workspace.review",
            "actor": str(context.get("open_id") or "feishu").strip() or "feishu",
            "client_metadata": {
                **context,
                "workspace_id": workspace_id,
                "artifact_key": artifact_key,
                "version_id": version_id,
                "trigger_source": "feishu",
            },
        },
    )
    run_id = str(run_response.get("run_id") or "").strip()
    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "artifact_key": artifact_key,
        "version_id": version_id,
        "result_page": _build_result_page_payload(
            run_id,
            request_context=context,
            audit_context={
                "source": "feishu",
                "tool_name": "feishu.workspace.review",
                "client_metadata": {
                    **context,
                    "workspace_id": workspace_id,
                    "artifact_key": artifact_key,
                    "version_id": version_id,
                    "trigger_source": "feishu",
                },
            },
            run_dir=OUTPUTS_ROOT / run_id,
        ),
    }


async def _submit_feishu_workspace_clarification(
    *,
    workspace_id: str,
    request: Request,
    payload: Any,
) -> dict[str, Any]:
    run_id = str(payload.run_id or "").strip()
    workspace = None
    if run_id:
        candidates = [
            item
            for item in _list_recent_workspace_runs(
                workspace_id=str(workspace_id or ""), limit=50
            )
        ]
        if any(str(item.get("run_id") or "").strip() == run_id for item in candidates):
            workspace = await _load_workspace_or_404(workspace_id)
    if workspace is not None:
        _enforce_workspace_access_http(
            workspace=workspace, context=_workspace_context_from_request(request)
        )
    request_context = _resolve_run_feishu_context(
        run_id, _workspace_context_from_request(request)
    )
    audit_context = _merge_audit_context_with_request(
        payload.build_audit_context(), request_context
    )
    result_payload = _submit_review_clarification_internal(
        run_id=run_id,
        answers=payload.to_answers_payload(),
        audit_context=audit_context,
    )
    clarification = (
        result_payload.get("clarification", {})
        if isinstance(result_payload, dict)
        else {}
    )
    has_pending_questions = bool(
        isinstance(clarification, dict)
        and clarification.get("triggered")
        and clarification.get("status") == "pending"
    )
    return {
        "workspace_id": workspace_id,
        "run_id": run_id,
        "clarification_status": str(clarification.get("status", "") or "not_needed"),
        "has_pending_questions": has_pending_questions,
        "clarification": clarification,
        "result_page": _build_result_page_payload(
            run_id,
            request_context=request_context,
            audit_context=audit_context,
            run_dir=OUTPUTS_ROOT / run_id,
        ),
    }


async def _submit_feishu_clarification(
    *, request: Request, payload: Any
) -> dict[str, Any]:
    run_id = str(payload.run_id or "").strip()
    request_context = _resolve_run_feishu_context(
        run_id, _resolve_request_feishu_context(request)
    )
    audit_context = _merge_audit_context_with_request(
        payload.build_audit_context(), request_context
    )
    result_payload = _submit_review_clarification_internal(
        run_id=run_id,
        answers=payload.to_answers_payload(),
        audit_context=audit_context,
    )
    clarification = (
        result_payload.get("clarification", {})
        if isinstance(result_payload, dict)
        else {}
    )
    has_pending_questions = bool(
        isinstance(clarification, dict)
        and clarification.get("triggered")
        and clarification.get("status") == "pending"
    )
    return {
        "run_id": run_id,
        "clarification_status": str(clarification.get("status", "") or "not_needed"),
        "has_pending_questions": has_pending_questions,
        "clarification": clarification,
        "result_page": _build_result_page_payload(
            run_id,
            request_context=request_context,
            audit_context=audit_context,
            run_dir=OUTPUTS_ROOT / run_id,
        ),
    }


async def _derive_feishu_workspace_version(
    *,
    workspace_id: str,
    version_id: str,
    request: Request,
    payload: Any,
) -> dict[str, Any]:
    workspace = await _load_workspace_or_404(workspace_id)
    context = _workspace_context_from_request(request)
    _enforce_workspace_access_http(workspace=workspace, context=context)
    source_version = next(
        (item for item in workspace.versions if item.version_id == version_id), None
    )
    if source_version is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "version_not_found",
                "message": f"version_id not found: {version_id}",
            },
        )
    same_artifact_versions = workspace.list_versions(source_version.artifact_key)
    next_version_number = (
        max((item.version_number for item in same_artifact_versions), default=0) + 1
    )
    timestamp = datetime.now(timezone.utc).isoformat()
    derived_version = ArtifactVersion(
        version_id=f"{source_version.artifact_key}-v{next_version_number}-{uuid.uuid4().hex[:8]}",
        workspace_id=source_version.workspace_id,
        artifact_key=source_version.artifact_key,
        artifact_type=source_version.artifact_type,
        status=ArtifactVersionStatus.draft,
        version_number=next_version_number,
        title=source_version.title or source_version.artifact_key,
        parent_version_id=source_version.version_id,
        source_run_id=source_version.source_run_id,
        created_at=timestamp,
        updated_at=timestamp,
        content_path=source_version.content_path,
        content_checksum=source_version.content_checksum,
        change_summary=f"Derived from {source_version.version_id}",
        metadata={
            **source_version.metadata,
            "derived_from_version_id": source_version.version_id,
            "derived_by_open_id": str(context.get("open_id") or "").strip(),
            "trigger_source": "feishu",
        },
    )

    artifact_repository = ArtifactRepository(WORKSPACE_DB_PATH)
    workspace_repository = WorkspaceRepository(WORKSPACE_DB_PATH)
    await artifact_repository.initialize()
    await workspace_repository.initialize()
    await artifact_repository.upsert_version(derived_version)
    workspace.register_version(derived_version, make_current=True)
    workspace.metadata = {
        **(workspace.metadata if isinstance(workspace.metadata, dict) else {}),
        "last_derive_audit": payload.build_audit_context(),
    }
    workspace.updated_at = timestamp
    await workspace_repository.upsert_workspace(workspace)
    return {
        "workspace_id": workspace_id,
        "artifact_key": derived_version.artifact_key,
        "source_version_id": source_version.version_id,
        "derived_version_id": derived_version.version_id,
        "version_number": derived_version.version_number,
    }


async def _get_feishu_workspace_diff(
    *,
    workspace_id: str,
    from_version: str,
    to_version: str,
    request: Request,
) -> dict[str, Any]:
    workspace = await _load_workspace_or_404(workspace_id)
    _enforce_workspace_access_http(
        workspace=workspace, context=_workspace_context_from_request(request)
    )
    left = next(
        (item for item in workspace.versions if item.version_id == from_version), None
    )
    right = next(
        (item for item in workspace.versions if item.version_id == to_version), None
    )
    if left is None or right is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "version_not_found", "message": "diff versions not found."},
        )
    return {
        "workspace_id": workspace_id,
        "from_version": _workspace_version_payload(left),
        "to_version": _workspace_version_payload(right),
        "diff_summary": {
            "artifact_key_changed": left.artifact_key != right.artifact_key,
            "content_path_changed": left.content_path != right.content_path,
            "status_changed": str(left.status) != str(right.status),
            "version_number_delta": right.version_number - left.version_number,
        },
        "h5_diff_url": f"/?workspace={workspace_id}&from={from_version}&to={to_version}",
    }


async def _update_feishu_workspace_roadmap(
    *,
    workspace_id: str,
    request: Request,
    payload: Any,
) -> dict[str, Any]:
    workspace = await _load_workspace_or_404(workspace_id)
    _enforce_workspace_access_http(
        workspace=workspace, context=_workspace_context_from_request(request)
    )
    existing_metadata = (
        workspace.metadata if isinstance(workspace.metadata, dict) else {}
    )
    old_roadmap = existing_metadata.get("roadmap")
    old_payload = (
        old_roadmap
        if isinstance(old_roadmap, dict)
        else {"version": "v0", "roadmap_items": []}
    )
    new_version_label = f"v{int(time.time())}"
    new_payload = generate_constrained_roadmap(
        tasks=payload.tasks,
        milestones=payload.milestones,
        dependencies=payload.dependencies,
        risk_items=payload.risk_items,
        acceptance_criteria_coverage=payload.acceptance_criteria_coverage,
        business_priority_hints=payload.business_priority_hints,
        version=new_version_label,
    )
    roadmap_diff = diff_roadmap_versions(old_payload, new_payload)
    workspace.metadata = {
        **existing_metadata,
        "roadmap": new_payload,
        "roadmap_diff": roadmap_diff,
        "roadmap_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    repository = WorkspaceRepository(WORKSPACE_DB_PATH)
    await repository.initialize()
    await repository.upsert_workspace(workspace)
    return {
        "workspace_id": workspace_id,
        "roadmap": new_payload,
        "diff": roadmap_diff,
    }


@app.middleware("http")
async def _request_logging_middleware(request: Request, call_next):
    path = request.url.path
    if should_skip_request_logging(path):
        return await call_next(request)

    trace_id = uuid.uuid4().hex
    started = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000)
        status_code = response.status_code if response is not None else 500
        log.info(
            "request completed",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip(request),
            },
        )
        if response is not None:
            response.headers["X-Trace-ID"] = trace_id


@app.middleware("http")
async def _api_security_middleware(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    if request.url.path.startswith("/api/feishu/"):
        return await call_next(request)

    settings = security_settings()
    auth_error = authenticate_request(request, settings)
    if auth_error is not None:
        if _request_has_valid_feishu_run_context(request):
            return await call_next(request)
        return auth_error

    rate_limit_error = enforce_submission_rate_limit(request, settings)
    if rate_limit_error is not None:
        return rate_limit_error

    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def _handle_request_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    return controlled_error_response(
        422,
        code="request_validation_error",
        message="Request validation failed.",
        extra={"errors": _json_safe(exc.errors())},
    )


@app.exception_handler(Exception)
async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    return controlled_error_response(
        500,
        code="internal_server_error",
        message=f"The server could not process the request: {type(exc).__name__}.",
    )


@app.get("/health")
def healthcheck() -> dict[str, Any]:
    scheduler = getattr(app.state, "product_decision_scheduler", None)
    return {
        "ok": True,
        "status": "healthy",
        "service": "requirement-review-v1",
        "decision_workspace": {
            "evidence_sync_scheduler": bool(scheduler is not None),
            "db_path": str(PRODUCT_DECISION_DB_PATH),
        },
    }


@app.get("/ready")
def readiness_check() -> JSONResponse:
    startup_completed = bool(getattr(app.state, "startup_completed", False))
    outputs_writable = False
    errors: list[str] = []

    try:
        outputs_writable = _outputs_root_writable()
    except Exception as exc:
        errors.append(f"outputs_root_unwritable: {exc}")

    ready = startup_completed and outputs_writable
    payload = {
        "ok": ready,
        "status": "ready" if ready else "not_ready",
        "service": "requirement-review-v1",
        "checks": {
            "startup_completed": startup_completed,
            "outputs_root_writable": outputs_writable,
            "frontend_available": FRONTEND_DIST_ROOT.exists(),
        },
        "errors": errors,
    }
    if ready:
        return JSONResponse(status_code=200, content=payload)
    return JSONResponse(status_code=503, content=payload)


@app.get("/api/templates")
def list_templates_endpoint(
    version: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        templates = list(list_template_records(version=version))
    except TemplateRegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"count": len(templates), "templates": templates}


@app.get("/api/templates/{template_type}")
def list_templates_by_type_endpoint(
    template_type: str, version: str | None = Query(default=None)
) -> dict[str, Any]:
    try:
        templates = list(
            list_template_records(template_type=template_type, version=version)
        )
    except TemplateRegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "template_type": template_type,
        "count": len(templates),
        "templates": templates,
    }


async def _project_get_review_status(run_id: str) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    run_dir = OUTPUTS_ROOT / run_id
    job = await _job_registry.get(run_id)

    if job:
        payload = _job_status_payload(job)
        payload["revision_stage"] = _build_revision_stage_payload(run_id=run_id)
        payload["result_page"] = _build_result_page_payload(
            run_id,
            request_context=request_context,
            run_dir=run_dir,
        )
        return payload

    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    payload = _persisted_status_payload(run_id, run_dir)
    payload["revision_stage"] = _build_revision_stage_payload(run_id=run_id)
    payload["result_page"] = _build_result_page_payload(
        run_id,
        request_context=request_context,
        run_dir=run_dir,
    )
    return payload


async def _project_stream_review_progress(run_id: str) -> StreamingResponse:
    job = await _job_registry.get(run_id)

    run_dir = OUTPUTS_ROOT / run_id
    if job is None and not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    broadcaster = ProgressBroadcaster()

    async def event_stream():
        terminal_payload = _terminal_payload_for_job(job) if job is not None else None
        if terminal_payload is None and job is None and run_dir.exists():
            terminal_payload = _terminal_payload_for_run_dir(
                run_id,
                run_dir,
                persisted_status_payload=_persisted_status_payload,
            )
        if terminal_payload is not None:
            yield broadcaster.encode_event(terminal_payload)
            return

        async for event in broadcaster.subscribe(run_id):
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _project_submit_clarification(
    run_id: str, payload: ClarificationAnswerRequest
) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    try:
        response_payload = await answer_review_clarification_async(
            run_id=run_id,
            answers=[item.model_dump(mode="python") for item in payload.answers],
            outputs_root=OUTPUTS_ROOT,
            audit_context={
                "source": "project_space",
                "tool_name": "review.clarification",
                "actor": "local",
                "client_metadata": request_context,
            },
            patch=payload.patch,
            patch_context=payload.patch_context,
        )
        response_payload["result_page"] = _build_result_page_payload(
            run_id,
            request_context=request_context,
            run_dir=OUTPUTS_ROOT / run_id,
        )
        return response_payload
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "clarification_unavailable",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_clarification_payload",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_update_revision_stage(
    run_id: str, payload: RevisionStageRequest
) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    try:
        revision_stage = record_revision_stage_decision(
            run_id=run_id,
            decision=payload.decision,
            outputs_root=OUTPUTS_ROOT,
            audit_context={
                "source": "project_space",
                "tool_name": "review.revision_stage",
                "actor": "local",
                "client_metadata": request_context,
            },
        )
        return {
            "run_id": run_id,
            "revision_stage": revision_stage,
            "result_page": _build_result_page_payload(
                run_id,
                request_context=request_context,
                run_dir=OUTPUTS_ROOT / run_id,
            ),
        }
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc), "run_id": run_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_stage_unavailable",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_submit_revision_input(
    run_id: str, payload: RevisionInputRequest
) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    try:
        response_payload = record_revision_input(
            run_id=run_id,
            selected_review_basis=payload.selected_review_basis,
            extra_instructions=payload.extra_instructions,
            meeting_notes_text=payload.meeting_notes_text,
            meeting_notes_file_ref=payload.meeting_notes_file_ref,
            outputs_root=OUTPUTS_ROOT,
            audit_context={
                "source": "project_space",
                "tool_name": "review.revision_input",
                "actor": "local",
                "client_metadata": request_context,
            },
        )
        response_payload["result_page"] = _build_result_page_payload(
            run_id,
            request_context=request_context,
            run_dir=OUTPUTS_ROOT / run_id,
        )
        return response_payload
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc), "run_id": run_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_input_unavailable",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_generate_revision(run_id: str) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    try:
        response_payload = await generate_revision_for_run_async(
            run_id=run_id,
            outputs_root=OUTPUTS_ROOT,
            audit_context={
                "source": "project_space",
                "tool_name": "review.revision_generate",
                "actor": "local",
                "client_metadata": request_context,
            },
        )
        response_payload["result_page"] = _build_result_page_payload(
            run_id,
            request_context=request_context,
            run_dir=OUTPUTS_ROOT / run_id,
        )
        return response_payload
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc), "run_id": run_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_generate_unavailable",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_confirm_revision(
    run_id: str, payload: RevisionConfirmRequest
) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    try:
        revision_stage = confirm_revision_action(
            run_id=run_id,
            action=payload.action,
            additional_requirements=payload.additional_requirements,
            outputs_root=OUTPUTS_ROOT,
            audit_context={
                "source": "project_space",
                "tool_name": "review.revision_confirm",
                "actor": "local",
                "client_metadata": request_context,
            },
        )
        response_payload: dict[str, Any] = {
            "run_id": run_id,
            "revision_stage": revision_stage,
            "result_page": _build_result_page_payload(
                run_id,
                request_context=request_context,
                run_dir=OUTPUTS_ROOT / run_id,
            ),
        }
        if payload.action == "regenerate_revision":
            generation_payload = await generate_revision_for_run_async(
                run_id=run_id,
                outputs_root=OUTPUTS_ROOT,
                audit_context={
                    "source": "project_space",
                    "tool_name": "review.revision_regenerate",
                    "actor": "local",
                    "client_metadata": request_context,
                },
            )
            response_payload["revision_generation"] = generation_payload
            response_payload["revision_stage"] = _build_revision_stage_payload(
                run_id=run_id
            )
        return response_payload
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc), "run_id": run_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_confirm_unavailable",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_generate_roadmap(run_id: str) -> dict[str, Any]:
    try:
        generate_roadmap_for_run(run_id=run_id, outputs_root=OUTPUTS_ROOT)
        return get_review_result_payload(run_id=run_id, outputs_root=OUTPUTS_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc), "run_id": run_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "roadmap_generation_unavailable",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_get_review_result(run_id: str) -> dict[str, Any]:
    request_context = _resolve_run_feishu_context(run_id, {})
    try:
        payload = get_review_result_payload(run_id=run_id, outputs_root=OUTPUTS_ROOT)
        payload["result_page"] = _build_result_page_payload(
            run_id,
            request_context=request_context,
            run_dir=OUTPUTS_ROOT / run_id,
        )
        return payload
    except ReviewRunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc)},
        ) from exc
    except ReviewResultNotReadyError as exc:
        job = await _job_registry.get(run_id)
        detail = _result_unavailable_detail(run_id, job, outputs_root=OUTPUTS_ROOT)
        if detail.get("code") == "result_not_ready":
            detail["message"] = str(exc)
        raise HTTPException(status_code=409, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "invalid_report_content", "message": str(exc)},
        ) from exc


async def _project_get_artifact_preview(run_id: str, artifact_key: str) -> dict[str, Any]:
    try:
        return get_review_artifact_preview_payload(
            run_id=run_id,
            artifact_key=artifact_key,
            outputs_root=OUTPUTS_ROOT,
        )
    except ReviewRunNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "run_not_found", "message": str(exc)},
        ) from exc
    except ReviewResultNotReadyError as exc:
        job = await _job_registry.get(run_id)
        detail = _result_unavailable_detail(run_id, job, outputs_root=OUTPUTS_ROOT)
        if detail.get("code") == "result_not_ready":
            detail["message"] = str(exc)
        raise HTTPException(status_code=409, detail=detail) from exc
    except ReviewArtifactNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "artifact_not_found",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "invalid_artifact_preview",
                "message": str(exc),
                "run_id": run_id,
            },
        ) from exc


async def _project_get_report(
    run_id: str, format: str = "md"
) -> Response:
    if format not in {"md", "json", "html", "csv"}:
        raise HTTPException(status_code=422, detail=f"Unsupported report format: {format}")
    run_dir = OUTPUTS_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"run_id not found: {run_id}")

    if format in {"md", "json"}:
        filename = "report.md" if format == "md" else "report.json"
        path = run_dir / filename
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"{filename} not found for run_id={run_id}"
            )

        media_type = (
            "text/markdown; charset=utf-8" if format == "md" else "application/json"
        )
        return FileResponse(path=str(path), media_type=media_type, filename=filename)

    report_payload = _load_report_payload(run_dir)
    report_md_path = run_dir / "report.md"
    if not report_md_path.exists() and format == "html":
        raise HTTPException(
            status_code=404, detail=f"report.md not found for run_id={run_id}"
        )

    if format == "html":
        report_md = report_md_path.read_text(encoding="utf-8")
        html_content = _build_report_html(
            run_id=run_id,
            report_payload=report_payload,
            report_md=report_md,
            run_dir=run_dir,
        )
        return Response(
            content=html_content,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="report.html"'},
        )

    csv_content = _build_report_csv(report_payload)
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="report.csv"'},
    )


_project_space_router, _feishu_config_store, _project_sync_store, _github_config_store, _notion_config_store = create_project_space_router(
    db_path=PROJECT_SPACE_DB_PATH,
    enqueue_review=_enqueue_review_run,
    get_run_status=_project_get_review_status,
    get_run_result=_project_get_review_result,
    stream_progress=_project_stream_review_progress,
    submit_clarification=_project_submit_clarification,
    update_revision_stage=_project_update_revision_stage,
    submit_revision_input=_project_submit_revision_input,
    generate_revision=_project_generate_revision,
    confirm_revision=_project_confirm_revision,
    generate_roadmap=_project_generate_roadmap,
    get_artifact_preview=_project_get_artifact_preview,
    get_report=_project_get_report,
)
app.include_router(_project_space_router)
app.include_router(
    create_feishu_router(
        submit_review_run=_enqueue_review_run,
        submit_clarification=_submit_feishu_clarification,
        list_workspace_overviews=_list_feishu_workspace_overviews,
        get_workspace_overview=_get_feishu_workspace_overview,
        list_workspace_versions=_list_feishu_workspace_versions,
        start_workspace_review=_start_feishu_workspace_review,
        submit_workspace_clarification=_submit_feishu_workspace_clarification,
        derive_workspace_version=_derive_feishu_workspace_version,
        get_workspace_diff=_get_feishu_workspace_diff,
        update_workspace_roadmap=_update_feishu_workspace_roadmap,
        sync_store=_project_sync_store,
        config_store=_feishu_config_store,
        new_id=new_id,
        now=now,
    )
)
app.include_router(
    create_github_router(
        sync_store=_project_sync_store,
        config_store=_github_config_store,
        new_id=new_id,
        now=now,
    )
)
app.include_router(
    create_notion_router(
        sync_store=_project_sync_store,
        config_store=_notion_config_store,
        new_id=new_id,
        now=now,
    )
)
app.include_router(create_pm_router())
app.include_router(
    create_product_decision_router(
        db_path=PRODUCT_DECISION_DB_PATH,
        sync_service=_product_decision_sync_service,
        artifacts_root=PRODUCT_DECISION_ARTIFACTS_ROOT,
    )
)


if FRONTEND_DIST_ROOT.exists():
    app.mount(
        "/", StaticFiles(directory=FRONTEND_DIST_ROOT, html=True), name="frontend"
    )

