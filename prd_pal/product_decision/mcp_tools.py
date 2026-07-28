"""MCP-facing helpers for the product decision workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prd_pal.product_decision.delivery import (
    DeliveryService,
    FeishuBitableDeliveryTarget,
    FeishuProjectDeliveryTarget,
)
from prd_pal.product_decision.prd_lifecycle import ApprovalService, PrdLifecycleService
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.product_decision.services import (
    CollectService,
    DecisionDomainError,
    InsightService,
    OpportunityService,
)
from prd_pal.product_decision.traceability import build_decision_trace

DEFAULT_DECISION_DB_PATH = Path("data") / "product_decision.sqlite3"


def _db_path(options: dict[str, Any] | None) -> str:
    resolved = options or {}
    path = str(resolved.get("db_path") or "").strip()
    return path or str(DEFAULT_DECISION_DB_PATH)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _receipt_payload(entity_key: str, entity: Any, receipt: Any) -> dict[str, Any]:
    return {
        entity_key: entity.model_dump(mode="json"),
        "artifact_id": receipt.artifact_id,
        "version": receipt.version,
        "audit_id": receipt.audit_id,
        "next_human_action": receipt.next_human_action,
        "status": receipt.status,
    }


async def _repo(options: dict[str, Any] | None) -> ProductDecisionRepository:
    repository = ProductDecisionRepository(_db_path(options))
    result = await repository.initialize()
    if not result.ok:
        message = result.error.message if result.error else "initialize failed"
        raise DecisionDomainError("repository_error", message)
    return repository


async def search_decision_evidence_for_mcp(
    *,
    product_id: str = "",
    query: str = "",
    limit: int = 50,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        items = await CollectService(await _repo(options)).search_evidence(
            product_id=product_id, query=query, limit=limit
        )
        return {
            "count": len(items),
            "evidence": [item.model_dump(mode="json") for item in items],
        }
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"search_decision_evidence failed: {exc}")


async def create_opportunity_candidate_for_mcp(
    *,
    product_id: str,
    title: str,
    problem: str = "",
    users: str = "",
    value: str = "",
    insight_ids: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    actor: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        opportunity, receipt = await OpportunityService(await _repo(options)).create_candidate(
            product_id=product_id,
            title=title,
            problem=problem,
            users=users,
            value=value,
            insight_ids=insight_ids or [],
            evidence_refs=evidence_refs or [],
            actor=actor,
        )
        return _receipt_payload("opportunity", opportunity, receipt)
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"create_opportunity_candidate failed: {exc}")


async def submit_opportunity_decision_for_mcp(
    *,
    opportunity_id: str,
    action: str,
    actor_open_id: str = "",
    reason: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        repository = await _repo(options)
        opportunities = OpportunityService(repository)
        approvals = ApprovalService(repository)
        normalized = str(action or "").strip().lower()
        if normalized == "submit":
            opportunity, receipt = await opportunities.submit_for_approval(
                opportunity_id, actor=actor_open_id, reason=reason
            )
        elif normalized == "reject":
            opportunity, receipt = await opportunities.reject(
                opportunity_id, actor=actor_open_id, reason=reason
            )
        elif normalized == "approve":
            opportunity, receipt = await approvals.approve_opportunity(
                opportunity_id, actor_open_id=actor_open_id, reason=reason
            )
        else:
            return _error(
                "invalid_input",
                "action must be one of: submit, approve, reject",
            )
        return _receipt_payload("opportunity", opportunity, receipt)
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"submit_opportunity_decision failed: {exc}")


async def generate_formal_prd_for_mcp(
    *,
    opportunity_id: str,
    title: str = "",
    markdown: str = "",
    actor_open_id: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        version, receipt = await PrdLifecycleService(await _repo(options)).create_from_approved_opportunity(
            opportunity_id,
            title=title,
            markdown=markdown,
            actor_open_id=actor_open_id,
        )
        return _receipt_payload("prd_version", version, receipt)
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"generate_formal_prd failed: {exc}")


async def request_prd_quality_assessment_for_mcp(
    *,
    prd_version_id: str,
    actor_open_id: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        version, assessment, receipt = await PrdLifecycleService(
            await _repo(options)
        ).assess_quality(prd_version_id, actor_open_id=actor_open_id)
        payload = _receipt_payload("prd_version", version, receipt)
        payload["quality_assessment"] = assessment.model_dump(mode="json")
        return payload
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"request_prd_quality_assessment failed: {exc}")


async def export_approved_delivery_package_for_mcp(
    *,
    prd_version_id: str,
    actor_open_id: str = "",
    app_token: str = "",
    table_id: str = "",
    project_key: str = "",
    field_mapping: dict[str, str] | None = None,
    enable_project: bool = False,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        repository = await _repo(options)
        bitable = FeishuBitableDeliveryTarget(
            app_token=app_token,
            table_id=table_id,
            field_mapping=dict(field_mapping or {}),
        )
        target: Any = bitable
        if enable_project or project_key:
            target = FeishuProjectDeliveryTarget(
                project_key=project_key,
                field_mapping=dict(field_mapping or {}),
                fallback=bitable,
                enabled=bool(project_key),
            )
        export, receipt = await DeliveryService(repository).export_prd(
            prd_version_id, target=target, actor_open_id=actor_open_id
        )
        return _receipt_payload("delivery", export, receipt)
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"export_approved_delivery_package failed: {exc}")


async def create_decision_insight_for_mcp(
    *,
    product_id: str,
    title: str,
    evidence_refs: list[str],
    summary: str = "",
    theme: str = "",
    actor: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        insight, receipt = await InsightService(await _repo(options)).create_insight(
            product_id=product_id,
            title=title,
            summary=summary,
            theme=theme,
            evidence_refs=evidence_refs,
            actor=actor,
        )
        return _receipt_payload("insight", insight, receipt)
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"create_decision_insight failed: {exc}")


async def get_decision_trace_for_mcp(
    *,
    root_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return await build_decision_trace(await _repo(options), root_id)
    except ValueError as exc:
        return _error("not_found", str(exc))
    except DecisionDomainError as exc:
        return _error(exc.code, exc.message)
    except Exception as exc:
        return _error("internal_error", f"get_decision_trace failed: {exc}")
