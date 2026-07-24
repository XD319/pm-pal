"""Minimal MCP server entrypoint (stdio transport).

Run:
    python -m prd_pal.mcp_server.server
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

from prd_pal.monitoring import (
    RetryOperationError,
    RetryTargetNotFoundError,
    query_audit_events,
    retry_operation as retry_governance_operation,
)
from prd_pal.service.execution_service import (
    get_execution_status_for_mcp,
    get_traceability_for_mcp,
    handoff_to_executor_for_mcp,
    list_execution_tasks_for_mcp,
    update_execution_task_for_mcp,
)
from prd_pal.service.report_service import get_report_for_mcp
from prd_pal.service.review_service import (
    answer_review_clarification_for_mcp,
    approve_handoff_for_mcp,
    classify_review_input_error,
    generate_delivery_bundle_for_mcp,
    get_review_workspace_for_mcp,
    prepare_agent_handoff_for_mcp_async,
    review_prd_for_mcp_async,
    review_requirement_for_mcp_async,
)
from prd_pal.templates import (
    TemplateRegistryError,
    get_template_record,
    list_template_records,
)

mcp = FastMCP("requirement-review-v1")


@mcp.tool()
def ping() -> dict[str, Any]:
    """Health check tool for MCP connectivity."""
    return {"ok": True}


@mcp.tool()
async def review_prd(
    prd_text: str | None = None,
    prd_path: str | None = None,
    source: str | None = None,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run the fuller MCP workflow-oriented review contract and return artifact-rich outputs."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="review_prd"
        )
        client_meta = _extract_client_metadata(ctx=ctx, options=audited_options)
        return await review_prd_for_mcp_async(
            prd_text=prd_text,
            prd_path=prd_path,
            source=source,
            options=audited_options,
            invocation_meta={"client_metadata": client_meta} if client_meta else {},
        )
    except FileNotFoundError as exc:
        return _error_response("PRD_NOT_FOUND", str(exc))
    except NotImplementedError as exc:
        return _error_response("NOT_IMPLEMENTED", str(exc))
    except (TypeError, ValueError) as exc:
        classified_error = _review_input_error_response(exc)
        if classified_error is not None:
            return _error_response(
                classified_error["code"], classified_error["message"]
            )
        return _error_response("INVALID_INPUT", str(exc))
    except Exception as exc:
        classified_error = _review_input_error_response(exc)
        if classified_error is not None:
            return _error_response(
                classified_error["code"], classified_error["message"]
            )
        return _error_response("INTERNAL_ERROR", f"review_prd failed: {exc}")


@mcp.tool()
async def review_requirement(
    source: str | None = None,
    prd_text: str | None = None,
    prd_path: str | None = None,
    mode: Literal["auto", "quick", "full"] | None = None,
    metadata: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Review-only facade over the review service with a compact approval-loop-style payload."""
    try:
        resolved_options = _merge_tool_options(metadata=metadata, options=options)
        if mode is not None:
            resolved_options = {**resolved_options, "mode": mode}
        audited_options = _with_audit_context(
            options=resolved_options, ctx=ctx, tool_name="review_requirement"
        )
        client_meta = _extract_client_metadata(ctx=ctx, options=audited_options)
        return await review_requirement_for_mcp_async(
            prd_text=prd_text,
            prd_path=prd_path,
            source=source,
            options=audited_options,
            invocation_meta={"client_metadata": client_meta} if client_meta else {},
        )
    except FileNotFoundError as exc:
        return _review_requirement_error_response("PRD_NOT_FOUND", str(exc))
    except NotImplementedError as exc:
        return _review_requirement_error_response("NOT_IMPLEMENTED", str(exc))
    except (TypeError, ValueError) as exc:
        classified_error = _review_input_error_response(exc)
        if classified_error is not None:
            return _review_requirement_error_response(
                classified_error["code"], classified_error["message"]
            )
        return _review_requirement_error_response("INVALID_INPUT", str(exc))
    except Exception as exc:
        classified_error = _review_input_error_response(exc)
        if classified_error is not None:
            return _review_requirement_error_response(
                classified_error["code"], classified_error["message"]
            )
        return _review_requirement_error_response(
            "INTERNAL_ERROR", f"review_requirement failed: {exc}"
        )


@mcp.tool()
async def prepare_agent_handoff(
    agent: Literal["all", "codex", "claude_code", "openclaw"] = "all",
    run_id: str | None = None,
    source: str | None = None,
    prd_text: str | None = None,
    prd_path: str | None = None,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Prepare adapter-specific request payloads for downstream coding agents."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="prepare_agent_handoff"
        )
        client_meta = _extract_client_metadata(ctx=ctx, options=audited_options)
        return await prepare_agent_handoff_for_mcp_async(
            agent=agent,
            run_id=run_id,
            prd_text=prd_text,
            prd_path=prd_path,
            source=source,
            options=audited_options,
            invocation_meta={"client_metadata": client_meta} if client_meta else {},
        )
    except FileNotFoundError as exc:
        return _prepare_agent_handoff_error_response(
            run_id=run_id, agent=agent, code="NOT_FOUND", message=str(exc)
        )
    except NotImplementedError as exc:
        return _prepare_agent_handoff_error_response(
            run_id=run_id, agent=agent, code="NOT_IMPLEMENTED", message=str(exc)
        )
    except (TypeError, ValueError) as exc:
        classified_error = _review_input_error_response(exc)
        if classified_error is not None:
            return _prepare_agent_handoff_error_response(
                run_id=run_id,
                agent=agent,
                code=classified_error["code"],
                message=classified_error["message"],
            )
        return _prepare_agent_handoff_error_response(
            run_id=run_id, agent=agent, code="INVALID_INPUT", message=str(exc)
        )
    except Exception as exc:
        classified_error = _review_input_error_response(exc)
        if classified_error is not None:
            return _prepare_agent_handoff_error_response(
                run_id=run_id,
                agent=agent,
                code=classified_error["code"],
                message=classified_error["message"],
            )
        return _prepare_agent_handoff_error_response(
            run_id=run_id,
            agent=agent,
            code="INTERNAL_ERROR",
            message=f"prepare_agent_handoff failed: {exc}",
        )


@mcp.tool()
def answer_review_clarification(
    run_id: str,
    answers: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply user clarification answers to the persisted review result and return the refreshed facade payload."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="answer_review_clarification"
        )
        return answer_review_clarification_for_mcp(
            run_id=run_id,
            answers=answers,
            options=audited_options,
        )
    except FileNotFoundError as exc:
        return {
            "run_id": str(run_id or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "run_id": str(run_id or ""),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "run_id": str(run_id or ""),
            "error": {
                "code": "internal_error",
                "message": f"answer_review_clarification failed: {exc}",
            },
        }


@mcp.tool()
def get_report(
    run_id: str,
    format: Literal["md", "json"] = "md",
    offset: int = 0,
    limit: int | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch report artifact content by run_id (supports markdown/json)."""
    try:
        resolved_options = options or {}
        if not isinstance(resolved_options, dict):
            raise TypeError("options must be an object")
        outputs_root = resolved_options.get("outputs_root", "outputs")
        return get_report_for_mcp(
            run_id=run_id,
            format=format,
            offset=offset,
            limit=limit,
            outputs_root=str(outputs_root),
        )
    except (TypeError, ValueError) as exc:
        return {
            "run_id": str(run_id or ""),
            "format": str(format or "md"),
            "content": "",
            "paths": {
                "report_md_path": "",
                "report_json_path": "",
            },
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "run_id": str(run_id or ""),
            "format": str(format or "md"),
            "content": "",
            "paths": {
                "report_md_path": "",
                "report_json_path": "",
            },
            "error": {"code": "internal_error", "message": f"get_report failed: {exc}"},
        }


@mcp.tool()
async def generate_delivery_bundle(
    run_id: str,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Generate or regenerate the standardized delivery bundle for a completed run."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="generate_delivery_bundle"
        )
        return generate_delivery_bundle_for_mcp(run_id=run_id, options=audited_options)
    except FileNotFoundError as exc:
        return {
            "run_id": str(run_id or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "run_id": str(run_id or ""),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "run_id": str(run_id or ""),
            "error": {
                "code": "internal_error",
                "message": f"generate_delivery_bundle failed: {exc}",
            },
        }


@mcp.tool()
def approve_handoff(
    bundle_id: str,
    action: Literal["approve", "need_more_info", "block_by_risk", "reset_to_draft"],
    reviewer: str = "",
    comment: str = "",
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Apply one approval operation to a persisted delivery bundle and return updated record paths."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="approve_handoff", actor=reviewer
        )
        return approve_handoff_for_mcp(
            bundle_id=bundle_id,
            action=action,
            reviewer=reviewer,
            comment=comment,
            options=audited_options,
        )
    except FileNotFoundError as exc:
        return {
            "bundle_id": str(bundle_id or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "bundle_id": str(bundle_id or ""),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "bundle_id": str(bundle_id or ""),
            "error": {
                "code": "internal_error",
                "message": f"approve_handoff failed: {exc}",
            },
        }


@mcp.tool()
async def handoff_to_executor(
    bundle_id: str,
    execution_mode: str = "agent_assisted",
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Turn one approved delivery bundle into persisted execution tasks."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="handoff_to_executor"
        )
        return handoff_to_executor_for_mcp(
            bundle_id=bundle_id, execution_mode=execution_mode, options=audited_options
        )
    except FileNotFoundError as exc:
        return {
            "bundle_id": str(bundle_id or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "bundle_id": str(bundle_id or ""),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "bundle_id": str(bundle_id or ""),
            "error": {
                "code": "internal_error",
                "message": f"handoff_to_executor failed: {exc}",
            },
        }


@mcp.tool()
def update_execution_task(
    task_id: str,
    status: Literal[
        "assigned", "in_progress", "waiting_review", "completed", "failed", "cancelled"
    ],
    actor: str = "",
    assigned_to: str = "",
    detail: str = "",
    result_summary: str = "",
    artifact_paths: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Persist one execution task status update from an external executor callback or polling loop."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="update_execution_task", actor=actor
        )
        return update_execution_task_for_mcp(
            task_id=task_id,
            status=status,
            actor=actor,
            assigned_to=assigned_to,
            detail=detail,
            result_summary=result_summary,
            artifact_paths=artifact_paths,
            options=audited_options,
        )
    except FileNotFoundError as exc:
        return {
            "task_id": str(task_id or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "task_id": str(task_id or ""),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "task_id": str(task_id or ""),
            "error": {
                "code": "internal_error",
                "message": f"update_execution_task failed: {exc}",
            },
        }


@mcp.tool()
def list_execution_tasks(
    bundle_id: str | None = None,
    status: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List persisted execution tasks, optionally filtered by bundle or status."""
    try:
        return list_execution_tasks_for_mcp(
            bundle_id=bundle_id, status=status, options=options
        )
    except FileNotFoundError as exc:
        return {"error": {"code": "not_found", "message": str(exc)}}
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"list_execution_tasks failed: {exc}",
            }
        }


@mcp.tool()
def get_review_workspace(
    run_id: str | None = None,
    bundle_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query persisted review workspace state by run or bundle identifier."""
    try:
        return get_review_workspace_for_mcp(
            run_id=run_id, bundle_id=bundle_id, options=options
        )
    except FileNotFoundError as exc:
        return {"error": {"code": "not_found", "message": str(exc)}}
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"get_review_workspace failed: {exc}",
            }
        }


@mcp.tool()
def get_template_registry(
    template_type: str | None = None,
    template_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Query registered templates and resolved versions for prompts and artifacts."""
    try:
        if template_id and str(template_id).strip():
            template = get_template_record(str(template_id).strip(), version=version)
            return {"count": 1, "templates": [template]}

        templates = list(
            list_template_records(template_type=template_type, version=version)
        )
        payload: dict[str, Any] = {"count": len(templates), "templates": templates}
        if template_type is not None:
            payload["template_type"] = template_type
        if version is not None:
            payload["version"] = version
        return payload
    except TemplateRegistryError as exc:
        return {
            "count": 0,
            "templates": [],
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "count": 0,
            "templates": [],
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "count": 0,
            "templates": [],
            "error": {
                "code": "internal_error",
                "message": f"get_template_registry failed: {exc}",
            },
        }


@mcp.tool()
def get_audit_events(
    run_id: str | None = None,
    bundle_id: str | None = None,
    task_id: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query persisted audit events with optional workflow filters."""
    try:
        resolved_options = options or {}
        if not isinstance(resolved_options, dict):
            raise TypeError("options must be an object")
        outputs_root = resolved_options.get("outputs_root", "outputs")
        events = query_audit_events(
            outputs_root,
            run_id=run_id,
            bundle_id=bundle_id,
            task_id=task_id,
            event_type=event_type,
            status=status,
        )
        return {
            "count": len(events),
            "events": events,
            "filters": {
                "run_id": run_id,
                "bundle_id": bundle_id,
                "task_id": task_id,
                "event_type": event_type,
                "status": status,
            },
        }
    except (TypeError, ValueError) as exc:
        return {
            "count": 0,
            "events": [],
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "count": 0,
            "events": [],
            "error": {
                "code": "internal_error",
                "message": f"get_audit_events failed: {exc}",
            },
        }


@mcp.tool()
def retry_operation(
    run_id: str,
    operation: str,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Retry one non-blocking governance operation for a persisted run."""
    try:
        audited_options = _with_audit_context(
            options=options, ctx=ctx, tool_name="retry_operation"
        )
        outputs_root = audited_options.get("outputs_root", "outputs")
        result = retry_governance_operation(
            run_id=run_id,
            operation=operation,
            outputs_root=outputs_root,
            audit_context=audited_options.get("audit_context"),
        )
        return result
    except RetryTargetNotFoundError as exc:
        return {
            "run_id": str(run_id or ""),
            "operation": str(operation or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except RetryOperationError as exc:
        return {
            "run_id": str(run_id or ""),
            "operation": str(operation or ""),
            "error": {"code": "invalid_operation", "message": str(exc)},
        }
    except FileNotFoundError as exc:
        return {
            "run_id": str(run_id or ""),
            "operation": str(operation or ""),
            "error": {"code": "not_found", "message": str(exc)},
        }
    except (TypeError, ValueError) as exc:
        return {
            "run_id": str(run_id or ""),
            "operation": str(operation or ""),
            "error": {"code": "invalid_input", "message": str(exc)},
        }
    except Exception as exc:
        return {
            "run_id": str(run_id or ""),
            "operation": str(operation or ""),
            "error": {
                "code": "internal_error",
                "message": f"retry_operation failed: {exc}",
            },
        }


@mcp.tool()
def get_execution_status(
    bundle_id: str | None = None,
    task_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query persisted execution task status by bundle or task identifier."""
    try:
        return get_execution_status_for_mcp(
            bundle_id=bundle_id, task_id=task_id, options=options
        )
    except FileNotFoundError as exc:
        return {"error": {"code": "not_found", "message": str(exc)}}
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"get_execution_status failed: {exc}",
            }
        }


@mcp.tool()
def get_traceability(
    requirement_id: str | None = None,
    task_id: str | None = None,
    bundle_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query persisted traceability links by requirement, task, or bundle."""
    try:
        return get_traceability_for_mcp(
            requirement_id=requirement_id,
            task_id=task_id,
            bundle_id=bundle_id,
            options=options,
        )
    except FileNotFoundError as exc:
        return {"error": {"code": "not_found", "message": str(exc)}}
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"get_traceability failed: {exc}",
            }
        }


def _pm_db_path_from_options(options: dict[str, Any] | None) -> str | None:
    resolved = options or {}
    if not isinstance(resolved, dict):
        raise TypeError("options must be an object")
    db_path = str(resolved.get("db_path") or "").strip()
    return db_path or None


@mcp.tool()
async def capture_feedback(
    texts: list[str] | None = None,
    product_hint: str = "",
    source: str = "manual",
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Capture raw PM feedback texts into persisted FeedbackItem records."""
    try:
        from prd_pal.pm.repository import PmRepository
        from prd_pal.pm.workflow import capture_feedback as capture_feedback_impl
        from prd_pal.pm import DEFAULT_PM_DB_PATH

        audited = _with_audit_context(
            options=options, ctx=ctx, tool_name="capture_feedback"
        )
        db_path = _pm_db_path_from_options(audited) or str(DEFAULT_PM_DB_PATH)
        repository = PmRepository(db_path)
        init_result = await repository.initialize()
        if not init_result.ok:
            message = (
                init_result.error.message if init_result.error else "initialize failed"
            )
            return {"error": {"code": "repository_error", "message": message}}
        items = await capture_feedback_impl(
            list(texts or []),
            product_hint=product_hint,
            source=source,
            repository=repository,
        )
        return {
            "count": len(items),
            "feedback_ids": [item.id for item in items],
            "feedback": [item.model_dump(mode="python") for item in items],
        }
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"capture_feedback failed: {exc}",
            }
        }


@mcp.tool()
async def cluster_feedback(
    feedback_ids: list[str] | None = None,
    feedback_texts: list[str] | None = None,
    product_hint: str = "",
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Cluster feedback into insights with source_refs."""
    try:
        from prd_pal.pm import DEFAULT_PM_DB_PATH
        from prd_pal.pm.insight_service import cluster_feedback as cluster_feedback_impl
        from prd_pal.pm.repository import PmRepository
        from prd_pal.pm.workflow import capture_feedback as capture_feedback_impl

        audited = _with_audit_context(
            options=options, ctx=ctx, tool_name="cluster_feedback"
        )
        db_path = _pm_db_path_from_options(audited) or str(DEFAULT_PM_DB_PATH)
        repository = PmRepository(db_path)
        init_result = await repository.initialize()
        if not init_result.ok:
            message = (
                init_result.error.message if init_result.error else "initialize failed"
            )
            return {"error": {"code": "repository_error", "message": message}}

        feedback_items = []
        for feedback_id in feedback_ids or []:
            result = await repository.get_feedback(str(feedback_id))
            if not result.ok or result.value is None:
                return {
                    "error": {
                        "code": "not_found",
                        "message": f"feedback_id not found: {feedback_id}",
                    }
                }
            feedback_items.append(result.value)
        if feedback_texts:
            feedback_items.extend(
                await capture_feedback_impl(
                    list(feedback_texts),
                    product_hint=product_hint,
                    repository=repository,
                )
            )
        insights = await cluster_feedback_impl(
            feedback_items, product_hint=product_hint
        )
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
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"cluster_feedback failed: {exc}",
            }
        }


@mcp.tool()
async def create_opportunity(
    insight_ids: list[str] | None = None,
    product_hint: str = "",
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Create an opportunity brief from persisted insights."""
    try:
        from prd_pal.pm import DEFAULT_PM_DB_PATH
        from prd_pal.pm.opportunity_service import build_opportunity
        from prd_pal.pm.repository import PmRepository

        audited = _with_audit_context(
            options=options, ctx=ctx, tool_name="create_opportunity"
        )
        db_path = _pm_db_path_from_options(audited) or str(DEFAULT_PM_DB_PATH)
        repository = PmRepository(db_path)
        init_result = await repository.initialize()
        if not init_result.ok:
            message = (
                init_result.error.message if init_result.error else "initialize failed"
            )
            return {"error": {"code": "repository_error", "message": message}}

        insights = []
        for insight_id in insight_ids or []:
            result = await repository.get_insight(str(insight_id))
            if not result.ok or result.value is None:
                return {
                    "error": {
                        "code": "not_found",
                        "message": f"insight_id not found: {insight_id}",
                    }
                }
            insights.append(result.value)
        opportunity = await build_opportunity(insights, product_hint=product_hint)
        await repository.upsert_artifact(
            artifact_type="opportunity",
            artifact_id=opportunity.id,
            payload=opportunity,
        )
        return {
            "opportunity_id": opportunity.id,
            "opportunity": opportunity.model_dump(mode="python"),
        }
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"create_opportunity failed: {exc}",
            }
        }


@mcp.tool()
async def generate_prd_from_opportunity(
    opportunity_id: str,
    product_hint: str = "",
    run_quality_gate: bool = True,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Generate a PRD draft from an opportunity and optionally run the review quality gate."""
    try:
        from prd_pal.pm import DEFAULT_PM_DB_PATH
        from prd_pal.pm.prd_service import draft_prd_from_opportunity
        from prd_pal.pm.repository import PmRepository

        audited = _with_audit_context(
            options=options, ctx=ctx, tool_name="generate_prd_from_opportunity"
        )
        db_path = _pm_db_path_from_options(audited) or str(DEFAULT_PM_DB_PATH)
        repository = PmRepository(db_path)
        init_result = await repository.initialize()
        if not init_result.ok:
            message = (
                init_result.error.message if init_result.error else "initialize failed"
            )
            return {"error": {"code": "repository_error", "message": message}}

        result = await repository.get_opportunity(str(opportunity_id or "").strip())
        if not result.ok or result.value is None:
            return {
                "error": {
                    "code": "not_found",
                    "message": f"opportunity_id not found: {opportunity_id}",
                }
            }
        draft, review_payload = await draft_prd_from_opportunity(
            result.value,
            product_hint=product_hint,
            run_quality_gate=bool(run_quality_gate),
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
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"generate_prd_from_opportunity failed: {exc}",
            }
        }


@mcp.tool()
async def run_pm_pipeline(
    feedback_texts: list[str] | None = None,
    product_hint: str = "",
    source: str = "manual",
    run_quality_gate: bool = True,
    options: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Run the deterministic PM pipeline from feedback texts through PRD review."""
    try:
        from prd_pal.pm import DEFAULT_PM_DB_PATH
        from prd_pal.pm.workflow import run_pm_pipeline as run_pm_pipeline_impl

        audited = _with_audit_context(
            options=options, ctx=ctx, tool_name="run_pm_pipeline"
        )
        db_path = _pm_db_path_from_options(audited) or str(DEFAULT_PM_DB_PATH)
        return await run_pm_pipeline_impl(
            list(feedback_texts or []),
            product_hint=product_hint,
            source=source,
            db_path=db_path,
            run_quality_gate=bool(run_quality_gate),
        )
    except (TypeError, ValueError) as exc:
        return {"error": {"code": "invalid_input", "message": str(exc)}}
    except Exception as exc:
        return {
            "error": {
                "code": "internal_error",
                "message": f"run_pm_pipeline failed: {exc}",
            }
        }


def _with_audit_context(
    *,
    options: dict[str, Any] | None,
    ctx: Context | None,
    tool_name: str,
    actor: str = "",
) -> dict[str, Any]:
    resolved_options = options or {}
    if not isinstance(resolved_options, dict):
        raise TypeError("options must be an object")

    updated_options = dict(resolved_options)
    existing_context = updated_options.get("audit_context")
    audit_context = dict(existing_context) if isinstance(existing_context, dict) else {}

    existing_client_metadata = audit_context.get("client_metadata")
    merged_client_metadata = (
        dict(existing_client_metadata)
        if isinstance(existing_client_metadata, dict)
        else {}
    )
    merged_client_metadata.update(
        _extract_client_metadata(ctx=ctx, options=updated_options)
    )
    if merged_client_metadata:
        audit_context["client_metadata"] = merged_client_metadata

    normalized_actor = str(actor or "").strip()
    if normalized_actor:
        audit_context["actor"] = normalized_actor
    elif str(audit_context.get("actor") or "").strip() == "":
        client_id = str(merged_client_metadata.get("client_id") or "").strip()
        if client_id:
            audit_context["actor"] = client_id

    if str(audit_context.get("source") or "").strip() == "":
        audit_context["source"] = "mcp"
    audit_context["tool_name"] = tool_name
    updated_options["audit_context"] = audit_context
    return updated_options


def _merge_tool_options(
    *,
    metadata: dict[str, Any] | None,
    options: dict[str, Any] | None,
) -> dict[str, Any]:
    if metadata is not None and not isinstance(metadata, dict):
        raise TypeError("metadata must be an object")
    if options is not None and not isinstance(options, dict):
        raise TypeError("options must be an object")

    merged: dict[str, Any] = {}
    if isinstance(metadata, dict):
        merged.update(metadata)
    if isinstance(options, dict):
        merged.update(options)
    return merged


def _extract_client_metadata(
    ctx: Context | None, options: dict[str, Any] | None
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if isinstance(options, dict):
        option_meta = options.get("client_metadata")
        if isinstance(option_meta, dict):
            metadata.update(option_meta)

    if ctx is None:
        return metadata

    try:
        client_id = ctx.client_id
        if client_id:
            metadata["client_id"] = client_id
    except Exception:
        pass

    try:
        request_meta = ctx.request_context.meta
        if request_meta is not None:
            request_meta_dump = request_meta.model_dump(exclude_none=True)
            if request_meta_dump:
                metadata["request_meta"] = request_meta_dump
    except Exception:
        pass

    try:
        client_info = getattr(
            getattr(ctx.request_context.session, "client_params", None),
            "clientInfo",
            None,
        )
        if client_info is not None:
            if hasattr(client_info, "model_dump"):
                metadata["client_info"] = client_info.model_dump(exclude_none=True)
            elif isinstance(client_info, dict):
                metadata["client_info"] = client_info
    except Exception:
        pass

    return metadata


def _review_input_error_response(exc: Exception) -> dict[str, str] | None:
    classified_error = classify_review_input_error(exc)
    if classified_error is None:
        return None
    return {"code": classified_error.code, "message": classified_error.message}


def _error_response(code: str, message: str) -> dict[str, Any]:
    return {
        "run_id": "",
        "status": "failed",
        "metrics": {
            "coverage_ratio": 0.0,
            "high_risk_ratio": 0.0,
            "revision_round": 0,
        },
        "artifacts": {
            "report_md_path": "",
            "report_json_path": "",
            "trace_path": "",
        },
        "error": {
            "code": code,
            "message": message,
        },
    }


def _review_requirement_error_response(code: str, message: str) -> dict[str, Any]:
    return {
        "review_id": "",
        "run_id": "",
        "findings": [],
        "open_questions": [],
        "risk_items": [],
        "conflicts": [],
        "report_path": "",
        "review_mode": "",
        "mode": "",
        "gating": {},
        "reviewers_used": [],
        "reviewers_skipped": [],
        "error": {
            "code": code,
            "message": message,
        },
    }


def _prepare_agent_handoff_error_response(
    *,
    run_id: str | None,
    agent: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "run_id": str(run_id or ""),
        "bundle_id": "",
        "status": "failed",
        "agent_selection": str(agent or "all"),
        "request_count": 0,
        "requests": [],
        "paths": {
            "run_dir": "",
            "delivery_bundle_path": "",
            "execution_pack_path": "",
        },
        "error": {
            "code": code,
            "message": message,
        },
    }


def main() -> None:
    """Start MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
