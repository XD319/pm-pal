"""Lightweight structured memory retrieval and reviewer injection policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import MemoryRecord
from .service import MemoryService

_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_TYPE_PRIORITY = {
    "team_rule": 0,
    "risk_pattern": 1,
    "clarification_fact": 2,
    "review_case": 3,
}


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    memory_id: str
    memory_type: str
    title: str
    summary: str
    content: str
    score: float
    reasons: tuple[str, ...]
    scope_level: str
    team_id: str
    project_id: str
    requirement_type: tuple[str, ...]
    confidence: float
    reuse_score: float
    usage_note: str
    tags: tuple[str, ...]
    do_not_overapply: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "scope_level": self.scope_level,
            "team_id": self.team_id,
            "project_id": self.project_id,
            "requirement_type": list(self.requirement_type),
            "confidence": round(self.confidence, 4),
            "reuse_score": round(self.reuse_score, 4),
            "usage_note": self.usage_note,
            "tags": list(self.tags),
            "do_not_overapply": self.do_not_overapply,
        }


@dataclass(frozen=True, slots=True)
class RejectedMemoryCandidate:
    memory_id: str
    title: str
    reason: str
    score: float
    threshold: float
    memory_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "title": self.title,
            "reason": self.reason,
            "score": round(self.score, 4),
            "threshold": round(self.threshold, 4),
            "memory_type": self.memory_type,
        }


@dataclass(frozen=True, slots=True)
class MemoryRetrievalDiagnostics:
    selected: tuple[RetrievedMemory, ...]
    rejected_candidates: tuple[RejectedMemoryCandidate, ...]
    considered_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "considered_count": int(self.considered_count),
            "selected": [item.to_dict() for item in self.selected],
            "rejected_candidates": [
                item.to_dict() for item in self.rejected_candidates
            ],
        }


def format_memory_block_for_reviewer(
    reviewer: str,
    memories: list[RetrievedMemory],
    *,
    memory_mode: str,
) -> str:
    if not memories:
        return ""
    lines = [
        "Memory Assistance:",
        "- Treat retrieved memory as advisory context, not as a source of truth.",
        "- Verify every recalled item against the current PRD before using it.",
        f"- Memory mode: {memory_mode}",
    ]
    for item in memories:
        lines.append(
            f"- [{item.memory_type}] {item.title} | note={item.usage_note} | score={item.score:.2f}"
        )
        lines.append(f"  Summary: {item.summary}")
        if item.do_not_overapply:
            lines.append(f"  Boundary: {item.do_not_overapply}")
    return "\n".join(lines)


async def retrieve_memories_async(
    *,
    memory_service: MemoryService,
    canonical_review_request: dict[str, Any] | None,
    normalized_requirement: dict[str, Any],
    memory_mode: str,
    limit: int = 3,
) -> list[RetrievedMemory]:
    diagnostics = await retrieve_memories_with_diagnostics_async(
        memory_service=memory_service,
        canonical_review_request=canonical_review_request,
        normalized_requirement=normalized_requirement,
        memory_mode=memory_mode,
        limit=limit,
    )
    return list(diagnostics.selected)


async def retrieve_memories_with_diagnostics_async(
    *,
    memory_service: MemoryService,
    canonical_review_request: dict[str, Any] | None,
    normalized_requirement: dict[str, Any],
    memory_mode: str,
    limit: int = 3,
) -> MemoryRetrievalDiagnostics:
    normalized_mode = str(memory_mode or "off").strip().lower() or "off"
    if normalized_mode == "off":
        return MemoryRetrievalDiagnostics(
            selected=(), rejected_candidates=(), considered_count=0
        )

    request = dict(canonical_review_request or {})
    candidates = await _load_candidates(memory_service, request)
    rejected: list[RejectedMemoryCandidate] = []
    scored: list[RetrievedMemory] = []
    for memory in candidates:
        if normalized_mode == "strict" and str(memory.memory_type) != "team_rule":
            rejected.append(
                RejectedMemoryCandidate(
                    memory_id=memory.memory_id,
                    title=memory.title,
                    reason="strict_mode_only_allows_team_rule",
                    score=0.0,
                    threshold=0.7,
                    memory_type=str(memory.memory_type),
                )
            )
            continue
        score, reasons = _score_memory(
            memory,
            request=request,
            normalized_requirement=normalized_requirement,
            memory_mode=normalized_mode,
        )
        threshold = (
            0.7
            if normalized_mode == "strict"
            else 0.66
            if normalized_mode == "assist"
            else 0.6
        )
        if score < threshold:
            rejected.append(
                RejectedMemoryCandidate(
                    memory_id=memory.memory_id,
                    title=memory.title,
                    reason="score_below_threshold",
                    score=score,
                    threshold=threshold,
                    memory_type=str(memory.memory_type),
                )
            )
            continue
        scored.append(
            RetrievedMemory(
                memory_id=memory.memory_id,
                memory_type=str(memory.memory_type),
                title=memory.title,
                summary=memory.summary,
                content=memory.content,
                score=score,
                reasons=tuple(reasons),
                scope_level=str(memory.scope.level),
                team_id=memory.scope.team_id,
                project_id=memory.scope.project_id,
                requirement_type=tuple(memory.scope.requirement_type),
                confidence=float(memory.confidence),
                reuse_score=float(memory.reuse_score),
                usage_note=_usage_note(memory, reasons),
                tags=tuple(memory.tags),
                do_not_overapply=memory.do_not_overapply,
            )
        )
    ranked = sorted(
        scored,
        key=lambda item: (
            -item.score,
            _TYPE_PRIORITY.get(item.memory_type, 99),
            -item.reuse_score,
            -item.confidence,
            item.title,
        ),
    )
    selected = tuple(ranked[: max(0, int(limit))])
    selected_ids = {item.memory_id for item in selected}
    trimmed_rejected = [item for item in rejected if item.memory_id not in selected_ids]
    return MemoryRetrievalDiagnostics(
        selected=selected,
        rejected_candidates=tuple(trimmed_rejected[:20]),
        considered_count=len(candidates),
    )


async def _load_candidates(
    memory_service: MemoryService, request: dict[str, Any]
) -> list[MemoryRecord]:
    seen: dict[str, MemoryRecord] = {}
    project_id = str(request.get("project_id", "") or "").strip()
    team_id = str(request.get("team_id", "") or "").strip()
    requirement_type = str(request.get("requirement_type", "") or "").strip()

    if project_id:
        for item in await memory_service.list_memory_by_scope(
            level="project", project_id=project_id
        ):
            seen[item.memory_id] = item
    if team_id:
        for item in await memory_service.list_memory_by_scope(
            level="team", team_id=team_id
        ):
            seen[item.memory_id] = item
    for item in await memory_service.list_memory_by_scope(level="global"):
        seen[item.memory_id] = item
    if requirement_type:
        for item in await memory_service.find_memories(
            requirement_type=requirement_type
        ):
            seen[item.memory_id] = item
    return list(seen.values())


def _score_memory(
    memory: MemoryRecord,
    *,
    request: dict[str, Any],
    normalized_requirement: dict[str, Any],
    memory_mode: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    project_id = str(request.get("project_id", "") or "").strip()
    team_id = str(request.get("team_id", "") or "").strip()
    requirement_type = str(request.get("requirement_type", "") or "").strip()

    if project_id and memory.scope.project_id == project_id:
        score += 1.0
        reasons.append("same_project")
    elif team_id and memory.scope.team_id == team_id:
        score += 0.75
        reasons.append("same_team")
    elif requirement_type and requirement_type in set(memory.scope.requirement_type):
        score += 0.45
        reasons.append("same_requirement_type")

    risk_similarity = _risk_surface_similarity(memory, normalized_requirement)
    if risk_similarity >= 0.18:
        score += min(0.4, risk_similarity + 0.08)
        reasons.append("similar_risk_surface")

    score += float(memory.reuse_score) * 0.18
    if float(memory.reuse_score) >= 0.7:
        reasons.append("high_reuse_score")
    score += float(memory.confidence) * 0.14
    if float(memory.confidence) >= 0.7:
        reasons.append("high_confidence")

    if memory_mode == "strict" and str(memory.memory_type) == "team_rule":
        score += 0.1
        reasons.append("strict_prefers_team_rule")
    elif memory_mode == "hybrid" and str(memory.memory_type) in {
        "team_rule",
        "risk_pattern",
    }:
        score += 0.08
        reasons.append("hybrid_prefers_reusable_memory")

    return score, reasons


def _risk_surface_similarity(
    memory: MemoryRecord, normalized_requirement: dict[str, Any]
) -> float:
    memory_tokens = _tokens(
        memory.title,
        memory.summary,
        memory.content,
        " ".join(memory.tags),
        " ".join(memory.scope.requirement_type),
        memory.applicability.summary,
        " ".join(memory.applicability.conditions),
        " ".join(memory.applicability.signals),
    )
    requirement_tokens = _tokens(
        normalized_requirement.get("summary", ""),
        " ".join(normalized_requirement.get("risk_hints", []) or []),
        " ".join(normalized_requirement.get("dependency_hints", []) or []),
        " ".join(normalized_requirement.get("modules", []) or []),
        " ".join(normalized_requirement.get("in_scope", []) or []),
    )
    if not memory_tokens or not requirement_tokens:
        return 0.0
    return len(memory_tokens & requirement_tokens) / len(requirement_tokens)


def _usage_note(memory: MemoryRecord, reasons: list[str]) -> str:
    if "same_project" in reasons:
        return "Project-local precedent; verify it still applies to the current PRD."
    if "same_team" in reasons and str(memory.memory_type) == "team_rule":
        return "Potential team rule; apply only after checking it matches the current requirement."
    if "similar_risk_surface" in reasons:
        return "Risk pattern hint; use it to expand review coverage, not as proof that the same issue exists."
    return "Advisory memory only; confirm against the current requirement before using."


def _tokens(*parts: Any) -> set[str]:
    return {
        match.group(0)
        for match in _WORD_RE.finditer(
            " ".join(str(part or "").lower() for part in parts)
        )
    }
