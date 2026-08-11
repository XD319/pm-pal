"""Project-domain application services for the minimal PM daily loop. :-)"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pm_pal.project_domain.scoring import score_ice
from pm_pal.quality_engine import QualityAssessmentRequest, QualityEngine
from pm_pal.quality_engine.models import QualityGateDecision
from pm_pal.utils.time import utc_now_iso

from .models import (
    DeliveryRecord,
    DeliveryStatus,
    EvidenceRecord,
    InsightRecord,
    OpportunityRecord,
    OpportunityStatus,
    PrdStatus,
    PrdVersionRecord,
    ProjectAuditEvent,
    TraceLink,
    WriteReceipt,
)
from .repository import ProjectDomainRepository


class ProjectDomainError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _audit_id() -> str:
    return f"audit-{uuid.uuid4().hex[:12]}"


def _insight_id() -> str:
    return f"insight-{uuid.uuid4().hex[:12]}"


def _opportunity_id() -> str:
    return f"opp-{uuid.uuid4().hex[:12]}"


def _prd_id() -> str:
    return f"prd-{uuid.uuid4().hex[:12]}"


def _prd_version_id(prd_id: str, version: int) -> str:
    return f"{prd_id}:v{version}"


def _delivery_id() -> str:
    return f"delivery-{uuid.uuid4().hex[:12]}"


def _trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:12]}"


class InsightService:
    def __init__(self, repository: ProjectDomainRepository) -> None:
        self.repository = repository

    def create_insight(
        self,
        *,
        project_id: str,
        title: str,
        summary: str = "",
        theme: str = "",
        evidence_refs: list[str],
        actor: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[InsightRecord, WriteReceipt]:
        project_id = str(project_id or "").strip()
        if not project_id:
            raise ProjectDomainError("project_required", "project_id is required")
        self.repository.ensure_project(project_id)
        refs = [str(item).strip() for item in evidence_refs if str(item or "").strip()]
        evidence_rows = [
            item
            for item in self.repository.list_evidence(project_id, confirmed_only=True, limit=1000)
            if item.id in set(refs)
        ]
        if not evidence_rows:
            raise ProjectDomainError(
                "confirmed_evidence_required",
                "Insight requires at least one confirmed evidence_ref in the project.",
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        insight = InsightRecord(
            id=_insight_id(),
            project_id=project_id,
            title=title.strip() or evidence_rows[0].summary or "Untitled insight",
            summary=summary or f"基于 {len(evidence_rows)} 条已确认证据归纳。",
            theme=theme,
            evidence_refs=[row.id for row in evidence_rows],
            source_refs=[f"evidence:{row.id}" for row in evidence_rows],
            source_urls=list(
                dict.fromkeys(row.source_url for row in evidence_rows if row.source_url)
            ),
            version=1,
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        saved = self.repository.upsert_insight(insight)
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=project_id,
                artifact_type="insight",
                artifact_id=insight.id,
                action="create",
                actor=actor,
                created_at=now,
            )
        )
        for row in evidence_rows:
            self.repository.add_trace_link(
                TraceLink(
                    id=_trace_id(),
                    project_id=project_id,
                    from_type="insight",
                    from_id=insight.id,
                    to_type="evidence",
                    to_id=row.id,
                    created_at=now,
                )
            )
        return saved, WriteReceipt(
            artifact_id=insight.id,
            version=1,
            audit_id=audit_id,
            next_human_action="create_opportunity",
            status="created",
        )


class OpportunityService:
    EDITABLE = {OpportunityStatus.proposed, OpportunityStatus.pending_approval}

    def __init__(self, repository: ProjectDomainRepository) -> None:
        self.repository = repository

    def create_candidate(
        self,
        *,
        project_id: str,
        title: str,
        problem: str = "",
        users: str = "",
        value: str = "",
        insight_ids: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        actor: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[OpportunityRecord, WriteReceipt]:
        project_id = str(project_id or "").strip()
        self.repository.ensure_project(project_id)
        insight_ids = [str(i).strip() for i in (insight_ids or []) if str(i).strip()]
        evidence_ids = [str(i).strip() for i in (evidence_refs or []) if str(i).strip()]
        insights = [
            item
            for item in self.repository.list_insights(project_id)
            if item.id in set(insight_ids)
        ]
        for insight in insights:
            evidence_ids.extend(insight.evidence_refs)
        evidence_ids = list(dict.fromkeys(evidence_ids))
        evidence_rows = [
            item
            for item in self.repository.list_evidence(project_id, limit=1000)
            if item.id in set(evidence_ids)
        ]
        if not evidence_rows:
            raise ProjectDomainError(
                "evidence_required",
                "Opportunity requires evidence_refs from insights or explicit refs.",
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        ice = score_ice(impact=3, confidence=3, ease=3)
        candidate = OpportunityRecord(
            id=_opportunity_id(),
            project_id=project_id,
            title=title.strip() or (insights[0].title if insights else "Untitled opportunity"),
            problem=problem or (insights[0].summary if insights else ""),
            users=users,
            value=value,
            status=OpportunityStatus.proposed,
            insight_ids=[item.id for item in insights],
            evidence_refs=[row.id for row in evidence_rows],
            source_refs=[f"evidence:{row.id}" for row in evidence_rows],
            source_urls=list(
                dict.fromkeys(row.source_url for row in evidence_rows if row.source_url)
            ),
            score=float(ice.score),
            score_method=ice.method,
            score_details=dict(ice.details),
            version=1,
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        saved = self.repository.upsert_opportunity(candidate)
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=project_id,
                artifact_type="opportunity",
                artifact_id=candidate.id,
                action="create",
                actor=actor,
                created_at=now,
            )
        )
        return saved, WriteReceipt(
            artifact_id=candidate.id,
            version=1,
            audit_id=audit_id,
            next_human_action="submit_for_approval",
            status=str(OpportunityStatus.proposed),
        )

    def submit_for_approval(
        self, opportunity_id: str, *, actor: str = "", reason: str = ""
    ) -> tuple[OpportunityRecord, WriteReceipt]:
        current = self._require(opportunity_id)
        if current.status != OpportunityStatus.proposed:
            raise ProjectDomainError(
                "invalid_opportunity_transition",
                "Only proposed opportunities can be submitted for approval.",
            )
        return self._transition(
            current,
            OpportunityStatus.pending_approval,
            action="submit_approval",
            actor=actor,
            reason=reason,
            next_human_action="owner_approve_or_reject",
        )

    def approve(
        self, opportunity_id: str, *, actor: str, reason: str = ""
    ) -> tuple[OpportunityRecord, WriteReceipt]:
        if not str(actor or "").strip():
            raise ProjectDomainError("actor_required", "approve requires actor")
        current = self._require(opportunity_id)
        if current.status == OpportunityStatus.approved:
            return current, WriteReceipt(
                artifact_id=current.id,
                version=current.version,
                audit_id=current.audit_id,
                next_human_action="generate_formal_prd",
                status=str(OpportunityStatus.approved),
            )
        if current.status != OpportunityStatus.pending_approval:
            raise ProjectDomainError(
                "invalid_opportunity_transition",
                "Only pending_approval opportunities can be approved.",
            )
        return self._transition(
            current,
            OpportunityStatus.approved,
            action="approve",
            actor=actor,
            reason=reason,
            next_human_action="generate_formal_prd",
        )

    def reject(
        self, opportunity_id: str, *, actor: str = "", reason: str = ""
    ) -> tuple[OpportunityRecord, WriteReceipt]:
        current = self._require(opportunity_id)
        return self._transition(
            current,
            OpportunityStatus.rejected,
            action="reject",
            actor=actor,
            reason=reason,
            next_human_action="none",
        )

    def _require(self, opportunity_id: str) -> OpportunityRecord:
        item = self.repository.get_opportunity(opportunity_id)
        if item is None:
            raise ProjectDomainError("opportunity_not_found", f"Opportunity {opportunity_id} not found")
        return item

    def _transition(
        self,
        current: OpportunityRecord,
        status: OpportunityStatus,
        *,
        action: str,
        actor: str,
        reason: str,
        next_human_action: str,
    ) -> tuple[OpportunityRecord, WriteReceipt]:
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = current.model_copy(
            update={
                "status": status,
                "version": current.version + 1,
                "audit_id": audit_id,
                "updated_at": now,
            }
        )
        saved = self.repository.upsert_opportunity(updated)
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=current.project_id,
                artifact_type="opportunity",
                artifact_id=current.id,
                action=action,
                actor=actor,
                reason=reason,
                artifact_version=updated.version,
                created_at=now,
            )
        )
        return saved, WriteReceipt(
            artifact_id=current.id,
            version=updated.version,
            audit_id=audit_id,
            next_human_action=next_human_action,
            status=str(status),
        )


class PrdLifecycleService:
    def __init__(
        self,
        repository: ProjectDomainRepository,
        *,
        quality_engine: QualityEngine | None = None,
    ) -> None:
        self.repository = repository
        self.opportunities = OpportunityService(repository)
        self.quality_engine = quality_engine or QualityEngine()

    def create_from_approved_opportunity(
        self,
        opportunity_id: str,
        *,
        title: str = "",
        markdown: str = "",
        actor: str = "",
        metadata: dict[str, Any] | None = None,
        materialize_project_source: bool = True,
    ) -> tuple[PrdVersionRecord, WriteReceipt]:
        opportunity = self.opportunities._require(opportunity_id)
        if opportunity.status != OpportunityStatus.approved:
            raise ProjectDomainError(
                "opportunity_not_approved",
                "Formal PRD requires an owner-approved opportunity.",
            )
        body = markdown.strip() or _default_markdown(opportunity)
        prd_id = _prd_id()
        audit_id = _audit_id()
        now = utc_now_iso()
        project_source_id = ""
        if materialize_project_source:
            project_source_id = self._write_project_source(
                project_id=opportunity.project_id,
                title=title.strip() or opportunity.title,
                markdown=body,
                opportunity_id=opportunity.id,
            )
        version = PrdVersionRecord(
            id=_prd_version_id(prd_id, 1),
            prd_id=prd_id,
            project_id=opportunity.project_id,
            opportunity_id=opportunity.id,
            version=1,
            title=title.strip() or opportunity.title,
            markdown=body,
            status=PrdStatus.draft,
            evidence_refs=list(opportunity.evidence_refs),
            source_refs=[f"opportunity:{opportunity.id}", *opportunity.source_refs],
            source_urls=list(opportunity.source_urls),
            project_source_id=project_source_id,
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        saved = self.repository.insert_prd_version(version)
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=opportunity.project_id,
                artifact_type="prd_version",
                artifact_id=version.id,
                action="create",
                actor=actor,
                created_at=now,
            )
        )
        self.repository.add_trace_link(
            TraceLink(
                id=_trace_id(),
                project_id=opportunity.project_id,
                from_type="prd_version",
                from_id=version.id,
                to_type="opportunity",
                to_id=opportunity.id,
                created_at=now,
            )
        )
        return saved, WriteReceipt(
            artifact_id=version.id,
            version=1,
            audit_id=audit_id,
            next_human_action="request_quality_assessment",
            status=str(PrdStatus.draft),
        )

    async def assess_quality(
        self, prd_version_id: str, *, actor: str = ""
    ) -> tuple[PrdVersionRecord, Any, WriteReceipt]:
        current = self._require(prd_version_id)
        if current.status not in {PrdStatus.draft, PrdStatus.quality_checked}:
            raise ProjectDomainError(
                "invalid_prd_transition",
                f"Cannot assess quality from status {current.status}",
            )
        assessment = await self.quality_engine.assess(
            QualityAssessmentRequest(
                prd_version_id=current.id,
                prd_text=current.markdown,
                opportunity_id=current.opportunity_id,
                evidence_refs=list(current.evidence_refs),
            )
        )
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = current.model_copy(
            update={
                "status": PrdStatus.quality_checked,
                "quality_assessment_id": getattr(assessment, "id", "") or "",
                "quality_decision": str(getattr(assessment, "decision", "") or ""),
                "audit_id": audit_id,
                "updated_at": now,
            }
        )
        saved = self.repository.update_prd_version(updated)
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=current.project_id,
                artifact_type="prd_version",
                artifact_id=current.id,
                action="quality_check",
                actor=actor,
                reason=str(getattr(assessment, "decision", "") or ""),
                artifact_version=current.version,
                created_at=now,
            )
        )
        decision = str(getattr(assessment, "decision", "") or "")
        next_action = (
            "owner_approve_for_delivery"
            if decision == str(QualityGateDecision.pass_)
            else "revise_and_reassess_or_owner_waive"
        )
        return saved, assessment, WriteReceipt(
            artifact_id=current.id,
            version=current.version,
            audit_id=audit_id,
            next_human_action=next_action,
            status=str(PrdStatus.quality_checked),
        )

    def approve(
        self, prd_version_id: str, *, actor: str, reason: str = ""
    ) -> tuple[PrdVersionRecord, WriteReceipt]:
        if not str(actor or "").strip():
            raise ProjectDomainError("actor_required", "approve requires actor")
        current = self._require(prd_version_id)
        if current.status == PrdStatus.approved:
            return current, WriteReceipt(
                artifact_id=current.id,
                version=current.version,
                audit_id=current.audit_id,
                next_human_action="mark_ready_for_delivery",
                status=str(PrdStatus.approved),
            )
        if current.status != PrdStatus.quality_checked:
            raise ProjectDomainError(
                "invalid_prd_transition",
                "Only quality_checked PRDs can be approved.",
            )
        if current.quality_decision and current.quality_decision != str(QualityGateDecision.pass_):
            raise ProjectDomainError(
                "quality_not_passed",
                "PRD quality gate did not pass; waive or revise first.",
            )
        return self._set_status(
            current,
            PrdStatus.approved,
            action="approve",
            actor=actor,
            reason=reason,
            next_human_action="mark_ready_for_delivery",
        )

    def waive(
        self, prd_version_id: str, *, actor: str, reason: str = ""
    ) -> tuple[PrdVersionRecord, WriteReceipt]:
        if not str(actor or "").strip():
            raise ProjectDomainError("actor_required", "waive requires actor")
        current = self._require(prd_version_id)
        if current.status != PrdStatus.quality_checked:
            raise ProjectDomainError(
                "invalid_prd_transition",
                "Only quality_checked PRDs can be waived.",
            )
        return self._set_status(
            current,
            PrdStatus.waived,
            action="waive",
            actor=actor,
            reason=reason or "owner_waive",
            next_human_action="mark_ready_for_delivery",
        )

    def mark_ready(
        self, prd_version_id: str, *, actor: str = ""
    ) -> tuple[PrdVersionRecord, WriteReceipt]:
        current = self._require(prd_version_id)
        if current.status not in {PrdStatus.approved, PrdStatus.waived, PrdStatus.ready_for_delivery}:
            raise ProjectDomainError(
                "invalid_prd_transition",
                "Only approved or waived PRDs can be marked ready for delivery.",
            )
        if current.status == PrdStatus.ready_for_delivery:
            return current, WriteReceipt(
                artifact_id=current.id,
                version=current.version,
                audit_id=current.audit_id,
                next_human_action="export_delivery",
                status=str(PrdStatus.ready_for_delivery),
            )
        return self._set_status(
            current,
            PrdStatus.ready_for_delivery,
            action="ready_for_delivery",
            actor=actor,
            reason="",
            next_human_action="export_delivery",
        )

    def _require(self, prd_version_id: str) -> PrdVersionRecord:
        item = self.repository.get_prd_version(prd_version_id)
        if item is None:
            raise ProjectDomainError("prd_not_found", f"PRD version {prd_version_id} not found")
        return item

    def _set_status(
        self,
        current: PrdVersionRecord,
        status: PrdStatus,
        *,
        action: str,
        actor: str,
        reason: str,
        next_human_action: str,
    ) -> tuple[PrdVersionRecord, WriteReceipt]:
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = current.model_copy(
            update={"status": status, "audit_id": audit_id, "updated_at": now}
        )
        saved = self.repository.update_prd_version(updated)
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=current.project_id,
                artifact_type="prd_version",
                artifact_id=current.id,
                action=action,
                actor=actor,
                reason=reason,
                artifact_version=current.version,
                created_at=now,
            )
        )
        return saved, WriteReceipt(
            artifact_id=current.id,
            version=current.version,
            audit_id=audit_id,
            next_human_action=next_human_action,
            status=str(status),
        )

    def _write_project_source(
        self,
        *,
        project_id: str,
        title: str,
        markdown: str,
        opportunity_id: str,
    ) -> str:
        import sqlite3

        source_id = f"source_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        checksum = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        with sqlite3.connect(self.repository.path) as conn:
            conn.execute(
                "INSERT INTO project_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    project_id,
                    title,
                    "prd_text",
                    markdown,
                    "",
                    1,
                    1,
                    now,
                    None,
                    checksum,
                    json.dumps(
                        {
                            "origin": "project_domain",
                            "opportunity_id": opportunity_id,
                            "is_confirmed_prd": True,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.execute(
                "INSERT INTO project_events VALUES (?,?,?,?,?,?)",
                (
                    f"event_{uuid.uuid4().hex[:12]}",
                    project_id,
                    "prd_materialized",
                    title,
                    source_id,
                    now,
                ),
            )
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE id=?",
                (now, project_id),
            )
            conn.commit()
        return source_id


class DeliveryService:
    def __init__(self, repository: ProjectDomainRepository) -> None:
        self.repository = repository
        self.prd = PrdLifecycleService(repository)

    def export(
        self,
        *,
        prd_version_id: str,
        target_kind: str = "local_bundle",
        actor: str = "",
        idempotency_key: str = "",
        field_payload: dict[str, Any] | None = None,
    ) -> tuple[DeliveryRecord, WriteReceipt]:
        version = self.prd._require(prd_version_id)
        if version.status != PrdStatus.ready_for_delivery:
            raise ProjectDomainError(
                "prd_not_ready",
                "Delivery requires a PRD marked ready_for_delivery.",
            )
        key = idempotency_key or f"{prd_version_id}:{target_kind}"
        audit_id = _audit_id()
        now = utc_now_iso()
        delivery = DeliveryRecord(
            id=_delivery_id(),
            project_id=version.project_id,
            prd_version_id=version.id,
            target_kind=target_kind,
            idempotency_key=key,
            status=DeliveryStatus.succeeded,
            external_url="",
            external_id=version.project_source_id or version.id,
            field_payload=dict(field_payload or {"title": version.title}),
            audit_id=audit_id,
            evidence_refs=list(version.evidence_refs),
            created_at=now,
            updated_at=now,
            metadata={"actor": actor},
        )
        saved = self.repository.upsert_delivery(delivery)
        if saved.id != delivery.id:
            return saved, WriteReceipt(
                artifact_id=saved.id,
                version=1,
                audit_id=saved.audit_id,
                next_human_action="none",
                status=str(saved.status),
            )
        self.repository.append_audit(
            ProjectAuditEvent(
                id=audit_id,
                project_id=version.project_id,
                artifact_type="delivery",
                artifact_id=delivery.id,
                action="export",
                actor=actor,
                created_at=now,
            )
        )
        self.repository.add_trace_link(
            TraceLink(
                id=_trace_id(),
                project_id=version.project_id,
                from_type="delivery",
                from_id=delivery.id,
                to_type="prd_version",
                to_id=version.id,
                created_at=now,
            )
        )
        return saved, WriteReceipt(
            artifact_id=delivery.id,
            version=1,
            audit_id=audit_id,
            next_human_action="none",
            status=str(DeliveryStatus.succeeded),
        )


def _default_markdown(opportunity: OpportunityRecord) -> str:
    return (
        f"# {opportunity.title}\n\n"
        f"## Problem\n{opportunity.problem or 'TBD'}\n\n"
        f"## Users\n{opportunity.users or 'TBD'}\n\n"
        f"## Value\n{opportunity.value or 'TBD'}\n\n"
        f"## Evidence\n"
        + "\n".join(f"- `{ref}`" for ref in opportunity.evidence_refs)
        + "\n"
    )


def confirm_evidence(
    repository: ProjectDomainRepository,
    evidence_id: str,
    *,
    confirmed: bool = True,
    actor: str = "",
) -> EvidenceRecord:
    item = repository.confirm_evidence(evidence_id, confirmed=confirmed)
    repository.append_audit(
        ProjectAuditEvent(
            id=_audit_id(),
            project_id=item.project_id,
            artifact_type="evidence",
            artifact_id=item.id,
            action="confirm" if confirmed else "unconfirm",
            actor=actor,
            created_at=utc_now_iso(),
        )
    )
    return item
