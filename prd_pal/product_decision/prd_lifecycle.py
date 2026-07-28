"""Opportunity approval and PRD quality lifecycle for the decision workspace."""

from __future__ import annotations

import uuid
from typing import Any

from prd_pal.quality_engine import QualityAssessment, QualityAssessmentRequest, QualityEngine
from prd_pal.quality_engine.models import QualityGateDecision
from prd_pal.utils.time import utc_now_iso

from .models import (
    DecisionAuditEvent,
    OpportunityCandidate,
    OpportunityCandidateStatus,
    PrdVersion,
    PrdVersionStatus,
    ProductOwnerConfig,
    WriteReceipt,
)
from .repository import ProductDecisionRepository
from .services import DecisionDomainError, OpportunityService


def _audit_id() -> str:
    return f"audit-{uuid.uuid4().hex[:12]}"


def _prd_id() -> str:
    return f"prd-{uuid.uuid4().hex[:12]}"


def _prd_version_id(prd_id: str, version: int) -> str:
    return f"{prd_id}:v{version}"


class AuthorizationService:
    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository

    async def require_owner_or_admin(
        self, *, product_id: str, actor_open_id: str, action: str
    ) -> ProductOwnerConfig:
        actor = str(actor_open_id or "").strip()
        if not actor:
            raise DecisionDomainError("actor_required", f"{action} requires actor open_id")
        result = await self.repository.get_product_owner(product_id)
        if not result.ok or result.value is None:
            raise DecisionDomainError(
                "product_owner_missing",
                f"No owner configured for product {product_id}",
            )
        config = result.value
        if actor == config.owner_open_id or actor in set(config.admin_open_ids):
            return config
        raise DecisionDomainError(
            "permission_denied",
            f"Actor {actor} is not owner/admin for product {product_id}",
        )


class ApprovalService:
    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository
        self.auth = AuthorizationService(repository)
        self.opportunities = OpportunityService(repository)

    async def approve_opportunity(
        self,
        opportunity_id: str,
        *,
        actor_open_id: str,
        reason: str = "",
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        current = await self.opportunities._require(opportunity_id)
        await self.auth.require_owner_or_admin(
            product_id=current.product_id,
            actor_open_id=actor_open_id,
            action="approve_opportunity",
        )
        if current.status != OpportunityCandidateStatus.pending_approval:
            raise DecisionDomainError(
                "invalid_opportunity_transition",
                "Only pending_approval opportunities can be approved.",
            )
        # Idempotent concurrent approve: same version stays approved once.
        existing = await self.repository.get_opportunity(opportunity_id)
        if (
            existing.ok
            and existing.value is not None
            and existing.value.status == OpportunityCandidateStatus.approved
        ):
            return existing.value, WriteReceipt(
                artifact_id=opportunity_id,
                version=existing.value.version,
                audit_id=existing.value.audit_id,
                next_human_action="generate_formal_prd",
                status=str(OpportunityCandidateStatus.approved),
            )
        return await self.opportunities._transition(
            opportunity_id,
            OpportunityCandidateStatus.approved,
            action="approve",
            actor=actor_open_id,
            reason=reason,
            next_human_action="generate_formal_prd",
        )


class PrdLifecycleService:
    """Formal PRD versions: draft → quality_checked → approved|waived → ready_for_delivery."""

    def __init__(
        self,
        repository: ProductDecisionRepository,
        *,
        quality_engine: QualityEngine | None = None,
    ) -> None:
        self.repository = repository
        self.auth = AuthorizationService(repository)
        self.opportunities = OpportunityService(repository)
        self.quality_engine = quality_engine or QualityEngine()

    async def create_from_approved_opportunity(
        self,
        opportunity_id: str,
        *,
        title: str = "",
        markdown: str = "",
        actor_open_id: str = "",
    ) -> tuple[PrdVersion, WriteReceipt]:
        opportunity = await self.opportunities._require(opportunity_id)
        if opportunity.status != OpportunityCandidateStatus.approved:
            raise DecisionDomainError(
                "opportunity_not_approved",
                "Formal PRD requires an owner-approved opportunity; candidates cannot bypass approval.",
            )
        if actor_open_id:
            await self.auth.require_owner_or_admin(
                product_id=opportunity.product_id,
                actor_open_id=actor_open_id,
                action="create_prd",
            )
        body = markdown.strip() or _default_markdown(opportunity)
        prd_id = _prd_id()
        audit_id = _audit_id()
        now = utc_now_iso()
        version = PrdVersion(
            id=_prd_version_id(prd_id, 1),
            prd_id=prd_id,
            product_id=opportunity.product_id,
            opportunity_id=opportunity.id,
            version=1,
            title=title.strip() or opportunity.title,
            markdown=body,
            status=PrdVersionStatus.draft,
            evidence_refs=list(opportunity.evidence_refs),
            source_refs=[f"opportunity:{opportunity.id}", *opportunity.source_refs],
            source_urls=list(opportunity.source_urls),
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
        )
        saved = await self.repository.insert_prd_version(version)
        if not saved.ok or saved.value is None:
            raise DecisionDomainError(
                "prd_persist_failed",
                saved.error.message if saved.error else "prd persist failed",
            )
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=opportunity.product_id,
                artifact_type="prd_version",
                artifact_id=version.id,
                action="create",
                actor=actor_open_id,
                reason="",
                artifact_version=1,
                created_at=now,
            )
        )
        return saved.value, WriteReceipt(
            artifact_id=version.id,
            version=1,
            audit_id=audit_id,
            next_human_action="request_quality_assessment",
            status=str(PrdVersionStatus.draft),
        )

    async def revise(
        self,
        prd_version_id: str,
        *,
        title: str | None = None,
        markdown: str | None = None,
        actor_open_id: str = "",
        reason: str = "",
    ) -> tuple[PrdVersion, WriteReceipt]:
        current = await self._require_version(prd_version_id)
        if actor_open_id:
            await self.auth.require_owner_or_admin(
                product_id=current.product_id,
                actor_open_id=actor_open_id,
                action="revise_prd",
            )
        next_version_no = current.version + 1
        audit_id = _audit_id()
        now = utc_now_iso()
        revised = PrdVersion(
            id=_prd_version_id(current.prd_id, next_version_no),
            prd_id=current.prd_id,
            product_id=current.product_id,
            opportunity_id=current.opportunity_id,
            version=next_version_no,
            title=title if title is not None else current.title,
            markdown=markdown if markdown is not None else current.markdown,
            status=PrdVersionStatus.draft,
            evidence_refs=list(current.evidence_refs),
            source_refs=list(current.source_refs),
            source_urls=list(current.source_urls),
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
            metadata={"revised_from": current.id, "reason": reason},
        )
        saved = await self.repository.insert_prd_version(revised)
        if not saved.ok or saved.value is None:
            # Unique constraint → treat as concurrent revise race.
            raise DecisionDomainError(
                "prd_version_conflict",
                saved.error.message if saved.error else "version already exists",
            )
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=current.product_id,
                artifact_type="prd_version",
                artifact_id=revised.id,
                action="revise",
                actor=actor_open_id,
                reason=reason,
                artifact_version=next_version_no,
                created_at=now,
                metadata={"previous_version_id": current.id},
            )
        )
        return saved.value, WriteReceipt(
            artifact_id=revised.id,
            version=next_version_no,
            audit_id=audit_id,
            next_human_action="request_quality_assessment",
            status=str(PrdVersionStatus.draft),
        )

    async def assess_quality(
        self, prd_version_id: str, *, actor_open_id: str = ""
    ) -> tuple[PrdVersion, QualityAssessment, WriteReceipt]:
        current = await self._require_version(prd_version_id)
        if current.status not in {PrdVersionStatus.draft, PrdVersionStatus.quality_checked}:
            raise DecisionDomainError(
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
        stored = await self.repository.save_quality_assessment(assessment)
        if not stored.ok:
            raise DecisionDomainError(
                "quality_persist_failed",
                stored.error.message if stored.error else "quality persist failed",
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = current.model_copy(
            update={
                "status": PrdVersionStatus.quality_checked,
                "quality_assessment_id": assessment.id,
                "quality_decision": str(assessment.decision),
                "audit_id": audit_id,
                "updated_at": now,
            }
        )
        # Mutable status fields on the same version row; markdown remains immutable.
        saved = await self.repository.update_prd_version_gate(updated)
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=current.product_id,
                artifact_type="prd_version",
                artifact_id=current.id,
                action="quality_check",
                actor=actor_open_id,
                reason=str(assessment.decision),
                artifact_version=current.version,
                created_at=now,
                metadata={"quality_assessment_id": assessment.id},
            )
        )
        next_action = (
            "owner_approve_for_delivery"
            if assessment.decision == QualityGateDecision.pass_
            else "revise_and_reassess_or_owner_waive"
        )
        return saved.value or updated, assessment, WriteReceipt(
            artifact_id=current.id,
            version=current.version,
            audit_id=audit_id,
            next_human_action=next_action,
            status=str(PrdVersionStatus.quality_checked),
        )

    async def approve(
        self,
        prd_version_id: str,
        *,
        actor_open_id: str,
        reason: str = "",
    ) -> tuple[PrdVersion, WriteReceipt]:
        current = await self._require_version(prd_version_id)
        await self.auth.require_owner_or_admin(
            product_id=current.product_id,
            actor_open_id=actor_open_id,
            action="approve_prd",
        )
        if current.status == PrdVersionStatus.approved:
            return current, WriteReceipt(
                artifact_id=current.id,
                version=current.version,
                audit_id=current.audit_id,
                next_human_action="mark_ready_for_delivery",
                status=str(PrdVersionStatus.approved),
            )
        if current.status != PrdVersionStatus.quality_checked:
            raise DecisionDomainError(
                "invalid_prd_transition",
                "Only quality_checked PRDs can be approved.",
            )
        if current.quality_decision != QualityGateDecision.pass_:
            raise DecisionDomainError(
                "quality_not_passed",
                "Only pass assessments can be approved; use waive with a reason otherwise.",
            )
        return await self._gate_transition(
            current,
            PrdVersionStatus.approved,
            action="approve",
            actor=actor_open_id,
            reason=reason,
            next_human_action="mark_ready_for_delivery",
        )

    async def waive(
        self,
        prd_version_id: str,
        *,
        actor_open_id: str,
        reason: str,
    ) -> tuple[PrdVersion, WriteReceipt]:
        if not str(reason or "").strip():
            raise DecisionDomainError(
                "waiver_reason_required",
                "Owner waiver requires a non-empty reason.",
            )
        current = await self._require_version(prd_version_id)
        await self.auth.require_owner_or_admin(
            product_id=current.product_id,
            actor_open_id=actor_open_id,
            action="waive_prd",
        )
        if current.status == PrdVersionStatus.waived:
            return current, WriteReceipt(
                artifact_id=current.id,
                version=current.version,
                audit_id=current.audit_id,
                next_human_action="mark_ready_for_delivery",
                status=str(PrdVersionStatus.waived),
            )
        if current.status != PrdVersionStatus.quality_checked:
            raise DecisionDomainError(
                "invalid_prd_transition",
                "Only quality_checked PRDs can be waived.",
            )
        if current.quality_decision == QualityGateDecision.pass_:
            raise DecisionDomainError(
                "waiver_not_needed",
                "Pass assessments should be approved, not waived.",
            )
        return await self._gate_transition(
            current,
            PrdVersionStatus.waived,
            action="waive",
            actor=actor_open_id,
            reason=reason,
            next_human_action="mark_ready_for_delivery",
        )

    async def mark_ready_for_delivery(
        self,
        prd_version_id: str,
        *,
        actor_open_id: str,
        reason: str = "",
    ) -> tuple[PrdVersion, WriteReceipt]:
        current = await self._require_version(prd_version_id)
        await self.auth.require_owner_or_admin(
            product_id=current.product_id,
            actor_open_id=actor_open_id,
            action="ready_for_delivery",
        )
        if current.status == PrdVersionStatus.ready_for_delivery:
            return current, WriteReceipt(
                artifact_id=current.id,
                version=current.version,
                audit_id=current.audit_id,
                next_human_action="export_delivery_package",
                status=str(PrdVersionStatus.ready_for_delivery),
            )
        if current.status not in {PrdVersionStatus.approved, PrdVersionStatus.waived}:
            raise DecisionDomainError(
                "invalid_prd_transition",
                "Only approved or waived PRDs can become ready_for_delivery.",
            )
        return await self._gate_transition(
            current,
            PrdVersionStatus.ready_for_delivery,
            action="ready_for_delivery",
            actor=actor_open_id,
            reason=reason,
            next_human_action="export_delivery_package",
        )

    async def reopen(
        self,
        prd_version_id: str,
        *,
        actor_open_id: str,
        reason: str = "",
    ) -> tuple[PrdVersion, WriteReceipt]:
        """Re-open by creating a new draft version; previous versions stay immutable."""
        return await self.revise(
            prd_version_id,
            actor_open_id=actor_open_id,
            reason=reason or "reopened",
        )

    async def _gate_transition(
        self,
        current: PrdVersion,
        status: PrdVersionStatus,
        *,
        action: str,
        actor: str,
        reason: str,
        next_human_action: str,
    ) -> tuple[PrdVersion, WriteReceipt]:
        # Concurrent duplicate approval: if already in target status, return current.
        latest = await self._require_version(current.id)
        if latest.status == status:
            return latest, WriteReceipt(
                artifact_id=latest.id,
                version=latest.version,
                audit_id=latest.audit_id,
                next_human_action=next_human_action,
                status=str(status),
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = latest.model_copy(
            update={"status": status, "audit_id": audit_id, "updated_at": now}
        )
        saved = await self.repository.update_prd_version_gate(updated)
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=latest.product_id,
                artifact_type="prd_version",
                artifact_id=latest.id,
                action=action,
                actor=actor,
                reason=reason,
                artifact_version=latest.version,
                created_at=now,
            )
        )
        return saved.value or updated, WriteReceipt(
            artifact_id=latest.id,
            version=latest.version,
            audit_id=audit_id,
            next_human_action=next_human_action,
            status=str(status),
        )

    async def _require_version(self, prd_version_id: str) -> PrdVersion:
        result = await self.repository.get_prd_version(prd_version_id)
        if not result.ok or result.value is None:
            raise DecisionDomainError(
                "prd_version_not_found", f"prd version not found: {prd_version_id}"
            )
        return result.value


def _default_markdown(opportunity: OpportunityCandidate) -> str:
    lines = [
        f"# {opportunity.title}",
        "",
        "## Problem",
        opportunity.problem or opportunity.title,
        "",
        "## Users",
        opportunity.users or "TBD",
        "",
        "## Value",
        opportunity.value or "TBD",
        "",
        "## Evidence",
        *[f"- {ref}" for ref in opportunity.evidence_refs],
    ]
    return "\n".join(lines)
