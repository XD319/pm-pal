"""Application services: collect → insight → opportunity → evaluate."""

from __future__ import annotations

import uuid
from typing import Any

from prd_pal.pm.scoring import score_ice, score_rice
from prd_pal.utils.time import utc_now_iso

from .models import (
    DecisionAuditEvent,
    DecisionInsight,
    EvidenceRecord,
    OpportunityCandidate,
    OpportunityCandidateStatus,
    WriteReceipt,
)
from .repository import ProductDecisionRepository


class DecisionDomainError(ValueError):
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


class CollectService:
    """Evidence retrieval for the decision workspace (no FastAPI coupling)."""

    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository

    async def search_evidence(
        self, *, product_id: str = "", query: str = "", limit: int = 100
    ) -> list[EvidenceRecord]:
        result = await self.repository.list_evidence(
            product_id=product_id, query=query, limit=limit
        )
        if not result.ok:
            message = result.error.message if result.error else "evidence search failed"
            raise DecisionDomainError("evidence_search_failed", message)
        return list(result.value or [])


class InsightService:
    """Attribution / clustering into durable insights with mandatory evidence refs."""

    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository

    async def create_insight(
        self,
        *,
        product_id: str,
        title: str,
        summary: str = "",
        theme: str = "",
        evidence_refs: list[str],
        actor: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[DecisionInsight, WriteReceipt]:
        product_id = str(product_id or "").strip()
        if not product_id:
            raise DecisionDomainError("product_required", "product_id is required")
        refs = [str(item).strip() for item in evidence_refs if str(item or "").strip()]
        if not refs:
            raise DecisionDomainError(
                "evidence_required",
                "Insight requires at least one valid evidence_ref.",
            )
        evidence_rows = await self._load_valid_evidence(product_id, refs)
        if not evidence_rows:
            raise DecisionDomainError(
                "evidence_required",
                "Insight requires at least one valid evidence_ref in the same product.",
            )
        source_urls = [
            row.source_url for row in evidence_rows if str(row.source_url or "").strip()
        ]
        source_refs = [f"evidence:{row.id}" for row in evidence_rows]
        audit_id = _audit_id()
        now = utc_now_iso()
        insight = DecisionInsight(
            id=_insight_id(),
            product_id=product_id,
            title=title.strip() or _default_title(evidence_rows),
            summary=summary or _default_summary(evidence_rows),
            theme=theme,
            evidence_refs=[row.id for row in evidence_rows],
            source_refs=source_refs,
            source_urls=list(dict.fromkeys(source_urls)),
            version=1,
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        saved = await self.repository.upsert_insight(insight)
        if not saved.ok or saved.value is None:
            raise DecisionDomainError(
                "insight_persist_failed",
                saved.error.message if saved.error else "insight persist failed",
            )
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=product_id,
                artifact_type="insight",
                artifact_id=insight.id,
                action="create",
                actor=actor,
                reason="",
                artifact_version=1,
                created_at=now,
            )
        )
        receipt = WriteReceipt(
            artifact_id=insight.id,
            version=1,
            audit_id=audit_id,
            next_human_action="review_insight_or_create_opportunity",
            status="created",
        )
        return saved.value, receipt

    async def list_insights(self, *, product_id: str = "") -> list[DecisionInsight]:
        result = await self.repository.list_insights(product_id=product_id)
        if not result.ok:
            raise DecisionDomainError(
                "insight_list_failed",
                result.error.message if result.error else "list failed",
            )
        return list(result.value or [])

    async def _load_valid_evidence(
        self, product_id: str, refs: list[str]
    ) -> list[EvidenceRecord]:
        listed = await self.repository.list_evidence(product_id=product_id, limit=1000)
        by_id = {item.id: item for item in (listed.value or [])}
        return [by_id[ref] for ref in refs if ref in by_id]


class OpportunityService:
    """Candidate opportunities: proposed by default; cannot mint formal PRDs."""

    EDITABLE = {
        OpportunityCandidateStatus.proposed,
        OpportunityCandidateStatus.pending_approval,
    }

    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository

    async def create_candidate(
        self,
        *,
        product_id: str,
        title: str,
        problem: str = "",
        users: str = "",
        value: str = "",
        insight_ids: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        actor: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        product_id = str(product_id or "").strip()
        if not product_id:
            raise DecisionDomainError("product_required", "product_id is required")
        insight_ids = [str(item).strip() for item in (insight_ids or []) if str(item).strip()]
        evidence_ids = [
            str(item).strip() for item in (evidence_refs or []) if str(item).strip()
        ]
        insights = await self._load_insights(product_id, insight_ids)
        for insight in insights:
            evidence_ids.extend(insight.evidence_refs)
        evidence_ids = list(dict.fromkeys(evidence_ids))
        if not evidence_ids:
            raise DecisionDomainError(
                "evidence_required",
                "Opportunity candidate requires evidence_refs from insights or explicit refs.",
            )
        evidence_rows = await self._load_evidence(product_id, evidence_ids)
        if not evidence_rows:
            raise DecisionDomainError(
                "evidence_required",
                "Opportunity candidate requires at least one in-product evidence ref.",
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        candidate = OpportunityCandidate(
            id=_opportunity_id(),
            product_id=product_id,
            title=title.strip() or (insights[0].title if insights else "Untitled opportunity"),
            problem=problem or (insights[0].summary if insights else ""),
            users=users,
            value=value,
            status=OpportunityCandidateStatus.proposed,
            insight_ids=[item.id for item in insights],
            evidence_refs=[row.id for row in evidence_rows],
            source_refs=[f"evidence:{row.id}" for row in evidence_rows],
            source_urls=list(
                dict.fromkeys(
                    [row.source_url for row in evidence_rows if row.source_url]
                    + [url for insight in insights for url in insight.source_urls]
                )
            ),
            version=1,
            audit_id=audit_id,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or {}),
        )
        saved = await self.repository.upsert_opportunity(candidate)
        if not saved.ok or saved.value is None:
            raise DecisionDomainError(
                "opportunity_persist_failed",
                saved.error.message if saved.error else "persist failed",
            )
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=product_id,
                artifact_type="opportunity",
                artifact_id=candidate.id,
                action="create",
                actor=actor,
                reason="",
                artifact_version=1,
                created_at=now,
            )
        )
        return saved.value, WriteReceipt(
            artifact_id=candidate.id,
            version=1,
            audit_id=audit_id,
            next_human_action="edit_add_evidence_reject_or_submit_approval",
            status=str(OpportunityCandidateStatus.proposed),
        )

    async def update_candidate(
        self,
        opportunity_id: str,
        *,
        title: str | None = None,
        problem: str | None = None,
        users: str | None = None,
        value: str | None = None,
        add_evidence_refs: list[str] | None = None,
        actor: str = "",
        reason: str = "",
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        current = await self._require(opportunity_id)
        if current.status not in self.EDITABLE:
            raise DecisionDomainError(
                "opportunity_not_editable",
                f"Opportunity in status {current.status} cannot be edited.",
            )
        evidence_refs = list(current.evidence_refs)
        for ref in add_evidence_refs or []:
            if ref and ref not in evidence_refs:
                evidence_refs.append(ref)
        evidence_rows = await self._load_evidence(current.product_id, evidence_refs)
        if not evidence_rows:
            raise DecisionDomainError(
                "evidence_required",
                "Opportunity must keep at least one valid evidence_ref.",
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = current.model_copy(
            update={
                "title": title if title is not None else current.title,
                "problem": problem if problem is not None else current.problem,
                "users": users if users is not None else current.users,
                "value": value if value is not None else current.value,
                "evidence_refs": [row.id for row in evidence_rows],
                "source_refs": [f"evidence:{row.id}" for row in evidence_rows],
                "source_urls": list(
                    dict.fromkeys(row.source_url for row in evidence_rows if row.source_url)
                ),
                "version": current.version + 1,
                "audit_id": audit_id,
                "updated_at": now,
            }
        )
        saved = await self.repository.upsert_opportunity(updated)
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=current.product_id,
                artifact_type="opportunity",
                artifact_id=opportunity_id,
                action="edit",
                actor=actor,
                reason=reason,
                artifact_version=updated.version,
                created_at=now,
            )
        )
        return saved.value or updated, WriteReceipt(
            artifact_id=opportunity_id,
            version=updated.version,
            audit_id=audit_id,
            next_human_action="edit_add_evidence_reject_or_submit_approval",
            status=str(updated.status),
        )

    async def reject(
        self, opportunity_id: str, *, actor: str = "", reason: str = ""
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        return await self._transition(
            opportunity_id,
            OpportunityCandidateStatus.rejected,
            action="reject",
            actor=actor,
            reason=reason,
            next_human_action="none",
        )

    async def submit_for_approval(
        self, opportunity_id: str, *, actor: str = "", reason: str = ""
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        current = await self._require(opportunity_id)
        if current.status != OpportunityCandidateStatus.proposed:
            raise DecisionDomainError(
                "invalid_opportunity_transition",
                "Only proposed opportunities can be submitted for approval.",
            )
        return await self._transition(
            opportunity_id,
            OpportunityCandidateStatus.pending_approval,
            action="submit_approval",
            actor=actor,
            reason=reason,
            next_human_action="owner_approve_or_reject",
        )

    async def create_formal_prd(self, opportunity_id: str) -> None:
        """Gate: candidates cannot mint a formal PRD until owner approval (module 4)."""
        current = await self._require(opportunity_id)
        if current.status != OpportunityCandidateStatus.approved:
            raise DecisionDomainError(
                "opportunity_not_approved",
                "Formal PRD requires an owner-approved opportunity; candidates cannot bypass approval.",
            )
        raise DecisionDomainError(
            "prd_lifecycle_not_enabled",
            "Formal PRD versioning is enforced in the quality lifecycle module.",
        )

    async def list_candidates(
        self, *, product_id: str = ""
    ) -> list[OpportunityCandidate]:
        result = await self.repository.list_opportunities(product_id=product_id)
        if not result.ok:
            raise DecisionDomainError(
                "opportunity_list_failed",
                result.error.message if result.error else "list failed",
            )
        return list(result.value or [])

    async def _transition(
        self,
        opportunity_id: str,
        status: OpportunityCandidateStatus,
        *,
        action: str,
        actor: str,
        reason: str,
        next_human_action: str,
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        current = await self._require(opportunity_id)
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
        saved = await self.repository.upsert_opportunity(updated)
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=current.product_id,
                artifact_type="opportunity",
                artifact_id=opportunity_id,
                action=action,
                actor=actor,
                reason=reason,
                artifact_version=updated.version,
                created_at=now,
            )
        )
        return saved.value or updated, WriteReceipt(
            artifact_id=opportunity_id,
            version=updated.version,
            audit_id=audit_id,
            next_human_action=next_human_action,
            status=str(status),
        )

    async def _require(self, opportunity_id: str) -> OpportunityCandidate:
        result = await self.repository.get_opportunity(opportunity_id)
        if not result.ok or result.value is None:
            raise DecisionDomainError(
                "opportunity_not_found", f"opportunity not found: {opportunity_id}"
            )
        return result.value

    async def _load_insights(
        self, product_id: str, insight_ids: list[str]
    ) -> list[DecisionInsight]:
        if not insight_ids:
            return []
        listed = await self.repository.list_insights(product_id=product_id)
        by_id = {item.id: item for item in (listed.value or [])}
        missing = [item for item in insight_ids if item not in by_id]
        if missing:
            raise DecisionDomainError(
                "insight_not_found",
                f"insights not found in product {product_id}: {', '.join(missing)}",
            )
        return [by_id[item] for item in insight_ids]

    async def _load_evidence(
        self, product_id: str, refs: list[str]
    ) -> list[EvidenceRecord]:
        listed = await self.repository.list_evidence(product_id=product_id, limit=1000)
        by_id = {item.id: item for item in (listed.value or [])}
        return [by_id[ref] for ref in refs if ref in by_id]


class EvaluateService:
    """Score proposed opportunities without changing approval gates."""

    def __init__(self, repository: ProductDecisionRepository) -> None:
        self.repository = repository

    async def evaluate(
        self,
        opportunity_id: str,
        *,
        method: str = "rice",
        reach: float = 1.0,
        impact: float = 1.0,
        confidence: float = 1.0,
        effort: float = 1.0,
        ease: float = 1.0,
        actor: str = "",
    ) -> tuple[OpportunityCandidate, WriteReceipt]:
        result = await self.repository.get_opportunity(opportunity_id)
        if not result.ok or result.value is None:
            raise DecisionDomainError(
                "opportunity_not_found", f"opportunity not found: {opportunity_id}"
            )
        current = result.value
        if current.status == OpportunityCandidateStatus.rejected:
            raise DecisionDomainError(
                "opportunity_rejected",
                "Rejected opportunities cannot be evaluated.",
            )
        if method == "ice":
            scored = score_ice(impact=impact, confidence=confidence, ease=ease)
        else:
            scored = score_rice(
                reach=reach, impact=impact, confidence=confidence, effort=effort
            )
        audit_id = _audit_id()
        now = utc_now_iso()
        updated = current.model_copy(
            update={
                "score": scored.score,
                "score_method": scored.method,
                "score_details": dict(scored.details),
                "version": current.version + 1,
                "audit_id": audit_id,
                "updated_at": now,
            }
        )
        saved = await self.repository.upsert_opportunity(updated)
        await self.repository.append_audit(
            DecisionAuditEvent(
                id=audit_id,
                product_id=current.product_id,
                artifact_type="opportunity",
                artifact_id=opportunity_id,
                action="evaluate",
                actor=actor,
                reason=scored.method,
                artifact_version=updated.version,
                created_at=now,
                metadata={"score": scored.score},
            )
        )
        return saved.value or updated, WriteReceipt(
            artifact_id=opportunity_id,
            version=updated.version,
            audit_id=audit_id,
            next_human_action="edit_add_evidence_reject_or_submit_approval",
            status=str(updated.status),
        )


def _default_title(rows: list[EvidenceRecord]) -> str:
    first = rows[0].summary or rows[0].content
    return (first[:80] + "…") if len(first) > 80 else first


def _default_summary(rows: list[EvidenceRecord]) -> str:
    return " | ".join((row.summary or row.quote or row.content)[:120] for row in rows[:3])
