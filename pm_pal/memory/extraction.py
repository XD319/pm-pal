"""Post-review memory extraction, gatekeeping, and persistence helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pm_pal.monitoring import (
    append_audit_event,
    normalize_audit_context,
    resolve_audit_actor,
)

from .models import (
    MemoryApplicability,
    MemoryEvidence,
    MemoryRecord,
    MemoryScope,
    MemoryScopeLevel,
    MemoryType,
)
from .service import MemoryService

MAX_MEMORIES_PER_RUN = 3
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_DATEISH_RE = re.compile(r"\b20\d{2}\b|\b\d{8}t\d{6}z\b", re.IGNORECASE)
_EPHEMERAL_HINTS = (
    "this run",
    "one-time",
    "temporary",
    "temp fix",
    "for now",
    "current sprint",
    "today",
    "this week",
)
_RULE_HINTS = (
    "must",
    "require",
    "required",
    "before release",
    "sign-off",
    "owner",
    "rollback",
    "audit",
    "approval",
)
_RISK_HINTS = (
    "risk",
    "break",
    "drift",
    "regress",
    "rollback",
    "dependency",
    "audit",
    "security",
    "qa",
)
_TYPE_PRIORITY = {
    MemoryType.team_rule.value: 0,
    MemoryType.risk_pattern.value: 1,
    MemoryType.clarification_fact.value: 2,
    MemoryType.review_case.value: 3,
}


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    memory_type: str
    title: str
    summary: str
    content: str
    scope: MemoryScope
    applicability: MemoryApplicability
    evidence: tuple[MemoryEvidence, ...]
    confidence: float
    reuse_score: float
    expiry_hint: str
    tags: tuple[str, ...]
    do_not_overapply: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "memory_type": self.memory_type,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "scope": self.scope.model_dump(mode="json"),
            "applicability": self.applicability.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in self.evidence],
            "confidence": round(float(self.confidence), 4),
            "reuse_score": round(float(self.reuse_score), 4),
            "expiry_hint": self.expiry_hint,
            "tags": list(self.tags),
            "do_not_overapply": self.do_not_overapply,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class CandidateRejection:
    candidate_id: str
    title: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MemoryExtractionOutcome:
    candidates: tuple[MemoryCandidate, ...]
    rejected: tuple[CandidateRejection, ...]
    kept: tuple[MemoryCandidate, ...]
    persisted: tuple[MemoryRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [item.to_dict() for item in self.candidates],
            "rejected": [item.to_dict() for item in self.rejected],
            "kept": [item.to_dict() for item in self.kept],
            "persisted": [item.model_dump(mode="json") for item in self.persisted],
            "candidate_count": len(self.candidates),
            "rejected_count": len(self.rejected),
            "kept_count": len(self.kept),
            "persisted_count": len(self.persisted),
        }


def extract_memory_candidates(
    *,
    run_id: str,
    review_result: dict[str, Any],
    canonical_review_request: dict[str, Any] | None = None,
    review_profile: dict[str, Any] | None = None,
) -> list[MemoryCandidate]:
    request = dict(canonical_review_request or {})
    profile = dict(review_profile or {})
    parallel_review = (
        review_result.get("parallel_review")
        if isinstance(review_result.get("parallel_review"), dict)
        else review_result
    )
    findings = [
        dict(item)
        for item in parallel_review.get("findings", [])
        if isinstance(item, dict)
    ]
    risk_items = [
        dict(item)
        for item in parallel_review.get("risk_items", [])
        if isinstance(item, dict)
    ]
    clarification = (
        parallel_review.get("clarification")
        if isinstance(parallel_review.get("clarification"), dict)
        else {}
    )
    summary = (
        parallel_review.get("summary")
        if isinstance(parallel_review.get("summary"), dict)
        else {}
    )

    candidates: list[MemoryCandidate] = []
    for finding in findings:
        reviewer = str(finding.get("source_reviewer", "") or "reviewer").strip().lower()
        severity = str(finding.get("severity", "") or "").strip().lower()
        detail = str(
            finding.get("detail", finding.get("description", "")) or ""
        ).strip()
        if reviewer in {"engineering", "qa", "security", "product"} and severity in {
            "high",
            "medium",
        }:
            if any(hint in detail.lower() for hint in _RULE_HINTS):
                candidates.append(
                    _build_team_rule_candidate(
                        run_id=run_id, finding=finding, request=request, profile=profile
                    )
                )
        if severity in {"high", "medium"} and any(
            hint in detail.lower() for hint in _RISK_HINTS
        ):
            candidates.append(
                _build_risk_pattern_candidate(
                    run_id=run_id, finding=finding, request=request, profile=profile
                )
            )

    for risk_item in risk_items:
        severity = str(risk_item.get("severity", "") or "").strip().lower()
        mitigation = str(
            risk_item.get("mitigation", risk_item.get("detail", "")) or ""
        ).strip()
        if severity in {"high", "medium"} and mitigation:
            candidates.append(
                _build_risk_item_candidate(
                    run_id=run_id, risk_item=risk_item, request=request, profile=profile
                )
            )

    for answer in [
        dict(item)
        for item in clarification.get("answers_applied", [])
        if isinstance(item, dict)
    ]:
        if len(str(answer.get("answer", "") or "").strip()) >= 40:
            candidates.append(
                _build_clarification_candidate(
                    run_id=run_id, answer=answer, request=request, profile=profile
                )
            )

    if (
        str(summary.get("overall_risk", "") or "").strip().lower() == "high"
        and len(findings) >= 2
    ):
        candidates.append(
            _build_review_case_candidate(
                run_id=run_id,
                findings=findings,
                summary=summary,
                request=request,
                profile=profile,
            )
        )

    return [_normalize_candidate(candidate) for candidate in candidates]


def gatekeep_memory_candidates(
    candidates: list[MemoryCandidate],
    *,
    existing_memories: list[MemoryRecord],
    max_memories: int = MAX_MEMORIES_PER_RUN,
) -> tuple[list[MemoryCandidate], list[CandidateRejection]]:
    seen_candidates: list[MemoryCandidate] = []
    rejected: list[CandidateRejection] = []
    for candidate in candidates:
        rejection_reason = _rejection_reason(candidate)
        if not rejection_reason:
            duplicate = _find_duplicate(candidate, existing_memories)
            if duplicate is not None:
                rejection_reason = f"duplicate_existing:{duplicate.memory_id}"
        if not rejection_reason:
            duplicate_candidate = _find_duplicate(candidate, seen_candidates)
            if duplicate_candidate is not None:
                rejection_reason = f"duplicate_batch:{duplicate_candidate.candidate_id}"
        if rejection_reason:
            rejected.append(
                CandidateRejection(
                    candidate.candidate_id, candidate.title, rejection_reason
                )
            )
            continue
        seen_candidates.append(candidate)

    ranked = sorted(
        seen_candidates,
        key=lambda item: (
            _TYPE_PRIORITY.get(item.memory_type, 99),
            -(float(item.confidence) + float(item.reuse_score)),
            item.title,
        ),
    )
    kept = ranked[: max(0, int(max_memories))]
    for candidate in ranked[max(0, int(max_memories)) :]:
        rejected.append(
            CandidateRejection(
                candidate.candidate_id, candidate.title, "limit_exceeded"
            )
        )
    return kept, rejected


async def process_review_memory_extraction_async(
    *,
    run_id: str,
    run_dir: str,
    review_result: dict[str, Any],
    memory_service: MemoryService,
    canonical_review_request: dict[str, Any] | None = None,
    review_profile: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
    max_memories: int = MAX_MEMORIES_PER_RUN,
) -> MemoryExtractionOutcome:
    request = dict(canonical_review_request or {})
    profile = dict(review_profile or {})
    context = normalize_audit_context(audit_context)
    actor = resolve_audit_actor(context, default="memory-extractor")

    candidates = extract_memory_candidates(
        run_id=run_id,
        review_result=review_result,
        canonical_review_request=request,
        review_profile=profile,
    )
    _audit_memory_event(
        run_dir,
        operation="memory_candidates",
        status="ok",
        run_id=run_id,
        audit_context=context,
        details={
            "candidate_count": len(candidates),
            "candidates": [item.to_dict() for item in candidates],
        },
    )

    existing = await _load_existing_memories_async(memory_service, request)
    kept, rejected = gatekeep_memory_candidates(
        candidates, existing_memories=existing, max_memories=max_memories
    )
    _audit_memory_event(
        run_dir,
        operation="memory_gatekeeping",
        status="ok",
        run_id=run_id,
        audit_context=context,
        details={
            "kept_count": len(kept),
            "rejected_count": len(rejected),
            "rejections": [item.to_dict() for item in rejected],
            "kept": [item.to_dict() for item in kept],
        },
    )

    persisted: list[MemoryRecord] = []
    for index, candidate in enumerate(kept, start=1):
        persisted.append(
            await memory_service.save_memory(
                memory_id=f"memory:{run_id}:{index}",
                memory_type=candidate.memory_type,
                title=candidate.title,
                summary=candidate.summary,
                content=candidate.content,
                scope=candidate.scope,
                applicability=candidate.applicability,
                evidence=list(candidate.evidence),
                confidence=candidate.confidence,
                reuse_score=candidate.reuse_score,
                expiry_hint=candidate.expiry_hint,
                tags=list(candidate.tags),
                do_not_overapply=candidate.do_not_overapply,
                created_by=actor,
                actor=actor,
            )
        )

    _audit_memory_event(
        run_dir,
        operation="memory_persisted",
        status="ok",
        run_id=run_id,
        audit_context=context,
        details={
            "persisted_count": len(persisted),
            "persisted_ids": [item.memory_id for item in persisted],
            "persisted_titles": [item.title for item in persisted],
        },
    )
    return MemoryExtractionOutcome(
        candidates=tuple(candidates),
        rejected=tuple(rejected),
        kept=tuple(kept),
        persisted=tuple(persisted),
    )


def _build_team_rule_candidate(
    *,
    run_id: str,
    finding: dict[str, Any],
    request: dict[str, Any],
    profile: dict[str, Any],
) -> MemoryCandidate:
    reviewer = str(finding.get("source_reviewer", "") or "reviewer").strip()
    detail = str(finding.get("detail", finding.get("description", "")) or "").strip()
    suggested_action = str(finding.get("suggested_action", "") or "").strip()
    clarification = str(finding.get("user_clarification", "") or "").strip()
    return MemoryCandidate(
        candidate_id=f"candidate:{run_id}:team_rule:{finding.get('finding_id', 'finding')!s}",
        memory_type=MemoryType.team_rule.value,
        title=f"{reviewer.title()} review gate: {str(finding.get('title', 'Stable requirement rule') or '').strip()}",
        summary=_truncate_sentence(suggested_action or detail),
        content=" ".join(
            part
            for part in (
                detail,
                suggested_action,
                f"Clarified: {clarification}" if clarification else "",
            )
            if part
        ),
        scope=_build_scope(
            memory_type=MemoryType.team_rule.value, request=request, profile=profile
        ),
        applicability=MemoryApplicability(
            summary="Apply when similar requirements trigger the same reviewer gate before delivery.",
            conditions=[
                f"reviewer:{reviewer.lower()}",
                f"severity:{str(finding.get('severity', '') or '').strip().lower()}",
            ],
            signals=[str(finding.get("category", "") or "").strip().lower()],
        ),
        evidence=(
            MemoryEvidence(
                kind="finding",
                reference=str(finding.get("finding_id", "") or "").strip(),
                summary=detail,
                metadata={
                    "reviewer": reviewer.lower(),
                    "severity": str(finding.get("severity", "") or "").strip().lower(),
                },
            ),
        ),
        confidence=0.84 if clarification else 0.74,
        reuse_score=0.9,
        expiry_hint="Revisit when the owning team changes its review gate or release policy.",
        tags=tuple(_candidate_tags(finding, extra=["team-rule", reviewer.lower()])),
        do_not_overapply="Do not turn reviewer preference into a global rule when the requirement is isolated to one-off migrations or temporary recovery work.",
        source="finding",
    )


def _build_risk_pattern_candidate(
    *,
    run_id: str,
    finding: dict[str, Any],
    request: dict[str, Any],
    profile: dict[str, Any],
) -> MemoryCandidate:
    detail = str(finding.get("detail", finding.get("description", "")) or "").strip()
    return MemoryCandidate(
        candidate_id=f"candidate:{run_id}:risk_pattern:{finding.get('finding_id', 'finding')!s}",
        memory_type=MemoryType.risk_pattern.value,
        title=f"Reusable risk pattern: {str(finding.get('title', 'Review finding') or '').strip()}",
        summary=_truncate_sentence(detail),
        content=" ".join(
            part
            for part in (
                detail,
                str(finding.get("suggested_action", "") or "").strip(),
                f"Observed severity: {str(finding.get('severity', '') or '').strip().lower()}",
            )
            if part
        ),
        scope=_build_scope(
            memory_type=MemoryType.risk_pattern.value, request=request, profile=profile
        ),
        applicability=MemoryApplicability(
            summary="Apply when similar scopes or dependencies suggest the same regression pattern.",
            conditions=[str(finding.get("category", "") or "").strip().lower()],
            signals=[str(finding.get("source_reviewer", "") or "").strip().lower()],
        ),
        evidence=(
            MemoryEvidence(
                kind="finding",
                reference=str(finding.get("finding_id", "") or "").strip(),
                summary=detail,
                metadata={
                    "severity": str(finding.get("severity", "") or "").strip().lower()
                },
            ),
        ),
        confidence=0.76,
        reuse_score=0.86,
        expiry_hint="Refresh if the underlying dependency, release process, or platform control changes.",
        tags=tuple(_candidate_tags(finding, extra=["risk-pattern"])),
        do_not_overapply="Do not apply when the same label appears without a real cross-boundary dependency or release consequence.",
        source="finding",
    )


def _build_risk_item_candidate(
    *,
    run_id: str,
    risk_item: dict[str, Any],
    request: dict[str, Any],
    profile: dict[str, Any],
) -> MemoryCandidate:
    detail = str(risk_item.get("detail", "") or "").strip()
    mitigation = str(risk_item.get("mitigation", "") or "").strip()
    reference = str(
        risk_item.get("risk_id", risk_item.get("id", risk_item.get("title", "risk")))
        or ""
    ).strip()
    return MemoryCandidate(
        candidate_id=f"candidate:{run_id}:risk_item:{reference or 'risk'}",
        memory_type=MemoryType.risk_pattern.value,
        title=f"Reusable risk pattern: {str(risk_item.get('title', 'Risk item') or '').strip()}",
        summary=_truncate_sentence(detail or mitigation),
        content=" ".join(part for part in (detail, mitigation) if part),
        scope=_build_scope(
            memory_type=MemoryType.risk_pattern.value, request=request, profile=profile
        ),
        applicability=MemoryApplicability(
            summary="Apply when a future requirement shows the same failure mode and mitigation shape.",
            conditions=[str(risk_item.get("severity", "") or "").strip().lower()],
            signals=[str(risk_item.get("category", "") or "").strip().lower()],
        ),
        evidence=(
            MemoryEvidence(
                kind="risk_item",
                reference=reference,
                summary=detail or mitigation,
                metadata={
                    "severity": str(risk_item.get("severity", "") or "").strip().lower()
                },
            ),
        ),
        confidence=0.72,
        reuse_score=0.83,
        expiry_hint="Revisit when the mitigation becomes platform-default or the dependency is removed.",
        tags=tuple(_candidate_tags(risk_item, extra=["risk-pattern"])),
        do_not_overapply="Do not reuse this pattern for isolated bugs without the same dependency or rollout shape.",
        source="risk_item",
    )


def _build_clarification_candidate(
    *,
    run_id: str,
    answer: dict[str, Any],
    request: dict[str, Any],
    profile: dict[str, Any],
) -> MemoryCandidate:
    answer_text = str(answer.get("answer", "") or "").strip()
    question = str(answer.get("question", "") or "").strip()
    reviewer = str(answer.get("reviewer", "") or "").strip().lower()
    return MemoryCandidate(
        candidate_id=f"candidate:{run_id}:clarification:{answer.get('question_id', 'clarification')!s}",
        memory_type=MemoryType.clarification_fact.value,
        title=f"Clarified fact: {question or 'Answered review question'}",
        summary=_truncate_sentence(answer_text),
        content=f"Question: {question}\nAnswer: {answer_text}",
        scope=_build_scope(
            memory_type=MemoryType.clarification_fact.value,
            request=request,
            profile=profile,
        ),
        applicability=MemoryApplicability(
            summary="Project fact that may unblock repeated review questions for the same requirement scope.",
            conditions=[f"reviewer:{reviewer}"] if reviewer else [],
            signals=["clarification", "answered"],
        ),
        evidence=(
            MemoryEvidence(
                kind="clarification_answer",
                reference=str(answer.get("question_id", "") or "").strip(),
                summary=answer_text,
                metadata={"reviewer": reviewer},
            ),
        ),
        confidence=0.66,
        reuse_score=0.52,
        expiry_hint="Revalidate if product scope, acceptance criteria, or rollout policy changes.",
        tags=tuple(_normalize_tags(["clarification", reviewer])),
        do_not_overapply="Do not promote a project-local answer into a stable team rule without repeated evidence across reviews.",
        source="clarification",
    )


def _build_review_case_candidate(
    *,
    run_id: str,
    findings: list[dict[str, Any]],
    summary: dict[str, Any],
    request: dict[str, Any],
    profile: dict[str, Any],
) -> MemoryCandidate:
    top_findings = findings[:2]
    in_scope = [
        str(item) for item in summary.get("in_scope", []) or [] if str(item).strip()
    ]
    return MemoryCandidate(
        candidate_id=f"candidate:{run_id}:review_case:summary",
        memory_type=MemoryType.review_case.value,
        title="Review case: high-risk aggregated PRD review",
        summary="High-risk review case with multiple aggregated findings.",
        content=" ".join(
            [
                f"High-risk review with {len(findings)} findings.",
                f"In scope: {', '.join(in_scope[:3])}" if in_scope else "",
                "Key findings: "
                + "; ".join(
                    str(item.get("title", "") or "").strip()
                    for item in top_findings
                    if str(item.get("title", "") or "").strip()
                ),
            ]
        ).strip(),
        scope=_build_scope(
            memory_type=MemoryType.review_case.value, request=request, profile=profile
        ),
        applicability=MemoryApplicability(
            summary="Reference case for future reviews with similar high-risk, multi-finding profiles.",
            conditions=["overall_risk:high"],
            signals=["review-case", "high-risk"],
        ),
        evidence=tuple(
            MemoryEvidence(
                kind="finding",
                reference=str(item.get("finding_id", "") or "").strip(),
                summary=str(item.get("title", "") or "").strip(),
                metadata={
                    "severity": str(item.get("severity", "") or "").strip().lower()
                },
            )
            for item in top_findings
        ),
        confidence=0.61,
        reuse_score=0.47,
        expiry_hint="Refresh after the workflow or release gate materially changes.",
        tags=tuple(_normalize_tags(["review-case", "high-risk"])),
        do_not_overapply="Do not treat a single high-risk review as a general rule without recurring signals in later runs.",
        source="summary",
    )


def _build_scope(
    *, memory_type: str, request: dict[str, Any], profile: dict[str, Any]
) -> MemoryScope:
    team_id = str(request.get("team_id", "") or "").strip()
    project_id = str(request.get("project_id", "") or "").strip()
    requirement_types = [str(request.get("requirement_type", "") or "").strip()]
    selected_profile = str(profile.get("selected_profile", "") or "").strip()
    if selected_profile and selected_profile != "default":
        requirement_types.append(selected_profile)
    normalized_requirement_types = _normalize_tags(requirement_types)

    if memory_type == MemoryType.clarification_fact.value and project_id:
        level = MemoryScopeLevel.project
    elif (
        memory_type in {MemoryType.team_rule.value, MemoryType.risk_pattern.value}
        and team_id
    ):
        level = MemoryScopeLevel.team
    elif project_id:
        level = MemoryScopeLevel.project
    elif team_id:
        level = MemoryScopeLevel.team
    else:
        level = MemoryScopeLevel.global_

    return MemoryScope(
        level=level,
        team_id=team_id if level == MemoryScopeLevel.team else "",
        project_id=project_id if level == MemoryScopeLevel.project else "",
        requirement_type=normalized_requirement_types,
    )


def _candidate_tags(
    payload: dict[str, Any], *, extra: list[str] | None = None
) -> list[str]:
    tags = list(extra or [])
    for key in ("category", "source_reviewer", "severity"):
        value = str(payload.get(key, "") or "").strip().lower()
        if value:
            tags.append(value)
    reviewers = payload.get("reviewers")
    if isinstance(reviewers, list):
        tags.extend(
            str(item).strip().lower() for item in reviewers if str(item).strip()
        )
    return _normalize_tags(tags)


def _normalize_candidate(candidate: MemoryCandidate) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate.candidate_id,
        memory_type=candidate.memory_type,
        title=_normalize_text(candidate.title, max_len=120),
        summary=_normalize_text(candidate.summary, max_len=240),
        content=_normalize_text(candidate.content, max_len=1500),
        scope=MemoryScope.model_validate(candidate.scope.model_dump(mode="json")),
        applicability=MemoryApplicability.model_validate(
            candidate.applicability.model_dump(mode="json")
        ),
        evidence=tuple(
            MemoryEvidence.model_validate(item.model_dump(mode="json"))
            for item in candidate.evidence
        ),
        confidence=round(max(0.0, min(float(candidate.confidence), 1.0)), 4),
        reuse_score=round(max(0.0, min(float(candidate.reuse_score), 1.0)), 4),
        expiry_hint=_normalize_text(candidate.expiry_hint, max_len=240),
        tags=tuple(_normalize_tags(candidate.tags)),
        do_not_overapply=_normalize_text(candidate.do_not_overapply, max_len=280),
        source=_normalize_text(candidate.source, max_len=80),
    )


def _rejection_reason(candidate: MemoryCandidate) -> str:
    content = candidate.content.lower()
    if len(candidate.summary) < 24 or len(candidate.content) < 60:
        return "too_vague"
    if "?" in candidate.summary or "?" in candidate.content:
        return "too_vague"
    if any(hint in content for hint in _EPHEMERAL_HINTS) or _DATEISH_RE.search(content):
        return "ephemeral_or_one_off"
    if len(candidate.evidence) == 0 or candidate.confidence < 0.6:
        return "weak_evidence"
    return ""


def _find_duplicate(candidate: MemoryCandidate, existing: list[Any]) -> Any | None:
    candidate_tokens = _tokens(candidate.title, candidate.summary, candidate.content)
    for item in existing:
        item_type = str(getattr(item, "memory_type", "") or "").strip()
        if item_type and item_type != candidate.memory_type:
            continue
        existing_tokens = _tokens(
            getattr(item, "title", ""),
            getattr(item, "summary", ""),
            getattr(item, "content", ""),
        )
        if _similarity(candidate_tokens, existing_tokens) >= 0.82:
            return item
    return None


def _tokens(*parts: Any) -> set[str]:
    return {
        match.group(0)
        for match in _WORD_RE.finditer(
            " ".join(str(part or "").lower() for part in parts)
        )
    }


def _similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _normalize_tags(values: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value or "").strip().lower().replace(" ", "_")
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _normalize_text(value: str, *, max_len: int) -> str:
    compact = " ".join(str(value or "").split())
    return compact[:max_len].strip()


def _truncate_sentence(value: str, *, max_len: int = 180) -> str:
    compact = _normalize_text(value, max_len=max_len)
    return (
        compact.rstrip(".") + "." if compact and not compact.endswith(".") else compact
    )


async def _load_existing_memories_async(
    memory_service: MemoryService, request: dict[str, Any]
) -> list[MemoryRecord]:
    seen: dict[str, MemoryRecord] = {}
    for record in await memory_service.list_memory_by_scope(level="global"):
        seen[record.memory_id] = record
    team_id = str(request.get("team_id", "") or "").strip()
    project_id = str(request.get("project_id", "") or "").strip()
    if team_id:
        for record in await memory_service.list_memory_by_scope(
            level="team", team_id=team_id
        ):
            seen[record.memory_id] = record
    if project_id:
        for record in await memory_service.list_memory_by_scope(
            level="project", project_id=project_id
        ):
            seen[record.memory_id] = record
    return list(seen.values())


def _audit_memory_event(
    run_dir: str,
    *,
    operation: str,
    status: str,
    run_id: str,
    audit_context: dict[str, Any],
    details: dict[str, Any],
) -> None:
    append_audit_event(
        run_dir,
        operation=operation,
        status=status,
        run_id=run_id,
        audit_context=audit_context,
        details=details,
    )
