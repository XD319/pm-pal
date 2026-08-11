"""Project-scoped PM domain HTTP routes under /api/projects/{project_id}. :-)"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .models import EvidenceRecord, EvidenceSource, SourceSyncStatus
from .repository import ProjectDomainRepository
from .services import (
    DeliveryService,
    InsightService,
    OpportunityService,
    PrdLifecycleService,
    ProjectDomainError,
    confirm_evidence,
)


class EvidenceConfirmBody(BaseModel):
    confirmed: bool = True
    actor: str = ""


class InsightCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = ""
    theme: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""


class OpportunityCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    problem: str = ""
    users: str = ""
    value: str = ""
    insight_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    actor: str = ""


class ActorBody(BaseModel):
    actor: str = ""
    reason: str = ""


class PrdCreateBody(BaseModel):
    opportunity_id: str = Field(min_length=1)
    title: str = ""
    markdown: str = ""
    actor: str = ""


class DeliveryExportBody(BaseModel):
    prd_version_id: str = Field(min_length=1)
    target_kind: str = "local_bundle"
    actor: str = ""
    idempotency_key: str = ""


class EvidenceIngestBody(BaseModel):
    source_type: str = "manual"
    display_name: str = "Manual feedback"
    content: str = Field(min_length=1)
    external_id: str = ""
    source_url: str = ""
    author: str = ""
    actor: str = ""
    confirm: bool = False


def _http_error(exc: ProjectDomainError | LookupError | ValueError) -> HTTPException:
    if isinstance(exc, ProjectDomainError):
        return HTTPException(
            status_code=422, detail={"code": exc.code, "message": exc.message}
        )
    if isinstance(exc, LookupError):
        return HTTPException(
            status_code=404, detail={"code": "not_found", "message": str(exc)}
        )
    return HTTPException(
        status_code=400, detail={"code": "bad_request", "message": str(exc)}
    )


def register_project_domain_routes(
    router: APIRouter,
    *,
    repository: ProjectDomainRepository,
) -> None:
    insights = InsightService(repository)
    opportunities = OpportunityService(repository)
    prds = PrdLifecycleService(repository)
    deliveries = DeliveryService(repository)

    def require_project(project_id: str) -> None:
        try:
            repository.ensure_project(project_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/projects/{project_id}/summary")
    async def project_summary(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return repository.workbench_summary(project_id)

    @router.get("/projects/{project_id}/evidence-sources")
    async def list_evidence_sources(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return {
            "sources": [
                item.model_dump() for item in repository.list_sources(project_id)
            ]
        }

    @router.get("/projects/{project_id}/evidence")
    async def list_evidence(
        project_id: str,
        query: str = "",
        confirmed_only: bool = False,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> dict[str, Any]:
        require_project(project_id)
        rows = repository.list_evidence(
            project_id, query=query, confirmed_only=confirmed_only, limit=limit
        )
        return {"evidence": [item.model_dump() for item in rows]}

    @router.post("/projects/{project_id}/evidence")
    async def ingest_evidence(
        project_id: str, body: EvidenceIngestBody
    ) -> dict[str, Any]:
        require_project(project_id)
        import uuid

        source_id = f"source-{uuid.uuid4().hex[:12]}"
        external_id = body.external_id.strip() or f"manual-{uuid.uuid4().hex[:10]}"
        source = EvidenceSource(
            id=source_id,
            project_id=project_id,
            source_type=body.source_type,
            external_id=external_id,
            source_url=body.source_url,
            display_name=body.display_name or "Manual feedback",
            sync_status=SourceSyncStatus.succeeded,
        )
        repository.upsert_source(source)
        record = EvidenceRecord(
            id=f"evidence-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            source_id=source_id,
            external_id=external_id,
            content=body.content,
            source_url=body.source_url,
            author=body.author or body.actor,
        )
        synced = repository.sync_evidence(source_id, [record], cursor=external_id)
        evidence = synced[0]
        if body.confirm:
            evidence = confirm_evidence(
                repository, evidence.id, confirmed=True, actor=body.actor
            )
        return {"evidence": evidence.model_dump(), "source": source.model_dump()}

    @router.post("/projects/{project_id}/evidence/{evidence_id}/confirm")
    async def confirm_evidence_route(
        project_id: str, evidence_id: str, body: EvidenceConfirmBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            item = repository.get_evidence(evidence_id)
            if item is None or item.project_id != project_id:
                raise LookupError(f"evidence not found: {evidence_id}")
            evidence = confirm_evidence(
                repository, evidence_id, confirmed=body.confirmed, actor=body.actor
            )
        except (LookupError, ProjectDomainError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"evidence": evidence.model_dump()}

    @router.get("/projects/{project_id}/insights")
    async def list_insights(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return {
            "insights": [
                item.model_dump() for item in repository.list_insights(project_id)
            ]
        }

    @router.post("/projects/{project_id}/insights")
    async def create_insight(
        project_id: str, body: InsightCreateBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            insight, receipt = insights.create_insight(
                project_id=project_id,
                title=body.title,
                summary=body.summary,
                theme=body.theme,
                evidence_refs=body.evidence_refs,
                actor=body.actor,
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"insight": insight.model_dump(), "receipt": receipt.model_dump()}

    @router.get("/projects/{project_id}/opportunities")
    async def list_opportunities(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return {
            "opportunities": [
                item.model_dump() for item in repository.list_opportunities(project_id)
            ]
        }

    @router.post("/projects/{project_id}/opportunities")
    async def create_opportunity(
        project_id: str, body: OpportunityCreateBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            opportunity, receipt = opportunities.create_candidate(
                project_id=project_id,
                title=body.title,
                problem=body.problem,
                users=body.users,
                value=body.value,
                insight_ids=body.insight_ids,
                evidence_refs=body.evidence_refs,
                actor=body.actor,
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {
            "opportunity": opportunity.model_dump(),
            "receipt": receipt.model_dump(),
        }

    @router.post("/projects/{project_id}/opportunities/{opportunity_id}/submit")
    async def submit_opportunity(
        project_id: str, opportunity_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            item = opportunities._require(opportunity_id)
            if item.project_id != project_id:
                raise LookupError(f"opportunity not found: {opportunity_id}")
            opportunity, receipt = opportunities.submit_for_approval(
                opportunity_id, actor=body.actor, reason=body.reason
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {
            "opportunity": opportunity.model_dump(),
            "receipt": receipt.model_dump(),
        }

    @router.post("/projects/{project_id}/opportunities/{opportunity_id}/approve")
    async def approve_opportunity(
        project_id: str, opportunity_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            item = opportunities._require(opportunity_id)
            if item.project_id != project_id:
                raise LookupError(f"opportunity not found: {opportunity_id}")
            opportunity, receipt = opportunities.approve(
                opportunity_id, actor=body.actor or "local", reason=body.reason
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {
            "opportunity": opportunity.model_dump(),
            "receipt": receipt.model_dump(),
        }

    @router.post("/projects/{project_id}/opportunities/{opportunity_id}/reject")
    async def reject_opportunity(
        project_id: str, opportunity_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            item = opportunities._require(opportunity_id)
            if item.project_id != project_id:
                raise LookupError(f"opportunity not found: {opportunity_id}")
            opportunity, receipt = opportunities.reject(
                opportunity_id, actor=body.actor, reason=body.reason
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {
            "opportunity": opportunity.model_dump(),
            "receipt": receipt.model_dump(),
        }

    @router.get("/projects/{project_id}/prd-versions")
    async def list_prd_versions(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return {
            "prd_versions": [
                item.model_dump() for item in repository.list_prd_versions(project_id)
            ]
        }

    @router.post("/projects/{project_id}/prd-versions")
    async def create_prd(project_id: str, body: PrdCreateBody) -> dict[str, Any]:
        require_project(project_id)
        try:
            opportunity = opportunities._require(body.opportunity_id)
            if opportunity.project_id != project_id:
                raise LookupError(f"opportunity not found: {body.opportunity_id}")
            version, receipt = prds.create_from_approved_opportunity(
                body.opportunity_id,
                title=body.title,
                markdown=body.markdown,
                actor=body.actor,
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"prd_version": version.model_dump(), "receipt": receipt.model_dump()}

    @router.post("/projects/{project_id}/prd-versions/{prd_version_id}/assess")
    async def assess_prd(
        project_id: str, prd_version_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            current = prds._require(prd_version_id)
            if current.project_id != project_id:
                raise LookupError(f"prd not found: {prd_version_id}")
            version, assessment, receipt = await prds.assess_quality(
                prd_version_id, actor=body.actor
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {
            "prd_version": version.model_dump(),
            "assessment": assessment.model_dump()
            if hasattr(assessment, "model_dump")
            else {},
            "receipt": receipt.model_dump(),
        }

    @router.post("/projects/{project_id}/prd-versions/{prd_version_id}/approve")
    async def approve_prd(
        project_id: str, prd_version_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            current = prds._require(prd_version_id)
            if current.project_id != project_id:
                raise LookupError(f"prd not found: {prd_version_id}")
            version, receipt = prds.approve(
                prd_version_id, actor=body.actor or "local", reason=body.reason
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"prd_version": version.model_dump(), "receipt": receipt.model_dump()}

    @router.post("/projects/{project_id}/prd-versions/{prd_version_id}/waive")
    async def waive_prd(
        project_id: str, prd_version_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            current = prds._require(prd_version_id)
            if current.project_id != project_id:
                raise LookupError(f"prd not found: {prd_version_id}")
            version, receipt = prds.waive(
                prd_version_id, actor=body.actor or "local", reason=body.reason
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"prd_version": version.model_dump(), "receipt": receipt.model_dump()}

    @router.post("/projects/{project_id}/prd-versions/{prd_version_id}/ready")
    async def ready_prd(
        project_id: str, prd_version_id: str, body: ActorBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            current = prds._require(prd_version_id)
            if current.project_id != project_id:
                raise LookupError(f"prd not found: {prd_version_id}")
            version, receipt = prds.mark_ready(prd_version_id, actor=body.actor)
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"prd_version": version.model_dump(), "receipt": receipt.model_dump()}

    @router.get("/projects/{project_id}/deliveries")
    async def list_deliveries(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return {
            "deliveries": [
                item.model_dump() for item in repository.list_deliveries(project_id)
            ]
        }

    @router.post("/projects/{project_id}/deliveries")
    async def export_delivery(
        project_id: str, body: DeliveryExportBody
    ) -> dict[str, Any]:
        require_project(project_id)
        try:
            current = prds._require(body.prd_version_id)
            if current.project_id != project_id:
                raise LookupError(f"prd not found: {body.prd_version_id}")
            delivery, receipt = deliveries.export(
                prd_version_id=body.prd_version_id,
                target_kind=body.target_kind,
                actor=body.actor,
                idempotency_key=body.idempotency_key,
            )
        except (ProjectDomainError, LookupError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"delivery": delivery.model_dump(), "receipt": receipt.model_dump()}

    @router.get("/projects/{project_id}/trace")
    async def list_trace(project_id: str) -> dict[str, Any]:
        require_project(project_id)
        return {
            "links": [
                item.model_dump() for item in repository.list_trace_links(project_id)
            ]
        }
