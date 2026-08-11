"""Aggregate multi-role reviewer outputs into unified artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .clarification_gate import build_clarification_payload
from .reviewer_agents.base import ReviewFinding, ReviewerResult, normalize_severity
from .reviewer_agents.delivery_reviewer import arbitrate_conflict

_SEMANTIC_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "scope_inclusion": (
        "scope included",
        "scope is included",
        "scope covered",
        "scope is complete",
        "within scope",
        "in scope",
    ),
    "dependency_blocker": (
        "dependency blocker",
        "blocked by dependency",
        "blocked by dependencies",
        "implementation boundaries",
        "cross system sequencing risk",
        "shared dependencies",
    ),
    "acceptance_complete": (
        "acceptance complete",
        "acceptance criteria complete",
        "acceptance criteria are complete",
        "acceptance coverage is complete",
        "ready for qa sign off",
        "test ready acceptance",
    ),
    "testability_gap": (
        "testability gap",
        "test oracle is missing",
        "cannot derive pass fail",
        "edge case coverage looks thin",
        "acceptance coverage is thinner",
        "pass fail expectations",
    ),
    "release_ok": (
        "release ok",
        "release is ok",
        "ready for release",
        "release can proceed",
        "approved for release",
        "good to release",
    ),
    "approval_blocker": (
        "approval blocker",
        "release gate required",
        "security review gate required",
        "requires approval before release",
        "reviewed before release approval",
        "cannot release until approval",
    ),
}

_SEMANTIC_SIGNAL_REVIEWERS: dict[str, tuple[str, ...]] = {
    "scope_inclusion": ("product",),
    "dependency_blocker": ("engineering",),
    "acceptance_complete": ("product", "qa"),
    "testability_gap": ("qa",),
    "release_ok": ("product", "engineering", "security"),
    "approval_blocker": ("security", "product"),
}

_SEMANTIC_CONFLICT_RULES: tuple[dict[str, str], ...] = (
    {
        "type": "scope_inclusion_vs_dependency_blocker",
        "left_signal": "scope_inclusion",
        "right_signal": "dependency_blocker",
        "description_template": "{left_subject} {left_verb} the requested scope is already covered, but {right_subject} {right_verb} dependency blockers that can stop or delay implementation.",
    },
    {
        "type": "acceptance_complete_vs_testability_gap",
        "left_signal": "acceptance_complete",
        "right_signal": "testability_gap",
        "description_template": "{left_subject} {left_verb} the acceptance criteria are complete, but {right_subject} {right_verb} a testability gap that leaves QA without reliable pass/fail coverage.",
    },
    {
        "type": "release_ok_vs_approval_blocker",
        "left_signal": "release_ok",
        "right_signal": "approval_blocker",
        "description_template": "{left_subject} {left_verb} the release as ready to proceed, but {right_subject} still {right_verb} an approval gate before release.",
    },
)


@dataclass(frozen=True, slots=True)
class AggregatedReviewArtifacts:
    review_result_json: str
    review_report_md: str
    risk_items_json: str
    open_questions_json: str
    review_report_json: str
    review_summary_md: str


@dataclass(frozen=True, slots=True)
class AggregatedReview:
    review_mode: str
    findings: tuple[dict[str, Any], ...]
    risk_items: tuple[dict[str, Any], ...]
    open_questions: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    reviewer_summaries: tuple[dict[str, Any], ...]
    tool_calls: tuple[dict[str, Any], ...]
    partial_review: bool
    reviewers_completed: tuple[str, ...]
    reviewers_failed: tuple[dict[str, str], ...]
    reviewers_used: tuple[str, ...]
    reviewers_skipped: tuple[dict[str, str], ...]
    reviewer_count: int
    gating: dict[str, Any]
    clarification: dict[str, Any]
    summary: dict[str, Any]
    meta: dict[str, Any]
    memory_hits: tuple[dict[str, Any], ...]
    similar_reviews_referenced: tuple[str, ...]
    normalizer_cache_hit: bool
    rag_enabled: bool
    artifacts: AggregatedReviewArtifacts

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_mode": self.review_mode,
            "findings": list(self.findings),
            "risk_items": list(self.risk_items),
            "open_questions": list(self.open_questions),
            "conflicts": list(self.conflicts),
            "reviewer_summaries": list(self.reviewer_summaries),
            "tool_calls": list(self.tool_calls),
            "partial_review": self.partial_review,
            "reviewers_completed": list(self.reviewers_completed),
            "reviewers_failed": list(self.reviewers_failed),
            "reviewers_used": list(self.reviewers_used),
            "reviewers_skipped": list(self.reviewers_skipped),
            "reviewer_count": self.reviewer_count,
            "gating": dict(self.gating),
            "clarification": dict(self.clarification),
            "summary": dict(self.summary),
            "meta": dict(self.meta),
            "memory_hits": list(self.memory_hits),
            "similar_reviews_referenced": list(self.similar_reviews_referenced),
            "normalizer_cache_hit": self.normalizer_cache_hit,
            "rag_enabled": self.rag_enabled,
            "artifacts": asdict(self.artifacts),
        }


def aggregate_review_results(
    reviewer_results: Iterable[ReviewerResult],
    output_dir: str | Path,
    *,
    selected_mode: str = "full",
    gating_decision: dict[str, Any] | None = None,
    gating_reasons: Iterable[str] = (),
    reviewers_used: Iterable[str] = (),
    reviewers_skipped: Iterable[dict[str, str]] = (),
    normalized_requirement: dict[str, Any] | None = None,
    memory_hits: list[dict[str, Any]] | None = None,
    similar_reviews_referenced: list[str] | None = None,
    normalizer_cache_hit: bool = False,
    rag_enabled: bool = False,
) -> AggregatedReview:
    results = tuple(reviewer_results)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    reviewers_used_list = _merge_unique([], [str(item) for item in reviewers_used])
    reviewers_skipped_list = [
        dict(item) for item in reviewers_skipped if isinstance(item, dict)
    ]
    selected_memory_hits = [
        dict(item) for item in memory_hits or [] if isinstance(item, dict)
    ]
    referenced_review_ids = [
        str(item) for item in similar_reviews_referenced or [] if str(item).strip()
    ]
    meta = _build_review_meta(
        results,
        selected_mode=selected_mode,
        gating_decision=gating_decision,
        gating_reasons=gating_reasons,
        reviewers_used=reviewers_used_list,
        reviewers_skipped=reviewers_skipped_list,
    )
    findings = _aggregate_findings(results)
    risk_items = _aggregate_risks(results)
    open_questions = _aggregate_open_questions(results)
    conflicts = _detect_conflicts(results)
    tool_calls = _aggregate_tool_calls(results)
    reviewer_summaries = tuple(
        {
            "reviewer": result.reviewer,
            "summary": result.summary,
            "status": str(result.status or "completed").strip().lower() or "completed",
            "status_detail": str(result.reviewer_status_detail or "").strip(),
            "ambiguity_type": str(result.ambiguity_type or "").strip(),
            "clarification_question": str(result.clarification_question or "").strip(),
            "notes": list(result.notes),
        }
        for result in results
        if result.summary or result.reviewer_status_detail or result.notes
    )
    findings = _apply_clarification_metadata(findings, list(reviewer_summaries))
    clarification = build_clarification_payload(findings, list(reviewer_summaries))
    partial_review = bool(meta.get("partial_review", False))
    reviewers_completed = list(meta.get("reviewers_completed", []) or [])
    reviewers_failed = list(meta.get("reviewers_failed", []) or [])
    manual_review_message = _build_manual_review_message(
        partial_review=partial_review,
        reviewers_failed=reviewers_failed,
        findings=findings,
        risk_items=risk_items,
    )
    summary = _build_summary_payload(
        normalized_requirement=normalized_requirement or {},
        findings=findings,
        risk_items=risk_items,
        reviewers_used=reviewers_used_list,
        reviewers_skipped=reviewers_skipped_list,
    )
    gating_payload = _build_gating_payload(
        gating_decision, selected_mode, gating_reasons
    )
    meta = {
        **meta,
        "manual_review_required": bool(manual_review_message),
        "manual_review_message": manual_review_message,
        "review_mode": selected_mode,
        "gating": gating_payload,
        "reviewers_used": reviewers_used_list,
        "reviewers_skipped": reviewers_skipped_list,
        "tool_calls": tool_calls,
        "reviewer_notes": [
            item
            for item in reviewer_summaries
            if item.get("status_detail") or item.get("notes")
        ],
        "memory_hits": selected_memory_hits,
        "memory_hit_count": len(selected_memory_hits),
        "similar_reviews_referenced": referenced_review_ids,
        "normalizer_cache_hit": bool(normalizer_cache_hit),
        "rag_enabled": bool(rag_enabled),
    }

    report_payload = {
        "review_mode": selected_mode,
        "reviewer_count": len(results),
        "partial_review": partial_review,
        "reviewers_completed": reviewers_completed,
        "reviewers_failed": reviewers_failed,
        "reviewers_used": reviewers_used_list,
        "reviewers_skipped": reviewers_skipped_list,
        "manual_review_required": bool(manual_review_message),
        "manual_review_message": manual_review_message,
        "gating": gating_payload,
        "clarification": clarification,
        "summary": summary,
        "meta": meta,
        "findings": findings,
        "risk_items": risk_items,
        "open_questions": open_questions,
        "conflicts": conflicts,
        "reviewer_summaries": list(reviewer_summaries),
        "tool_calls": tool_calls,
        "memory_hits": selected_memory_hits,
        "similar_reviews_referenced": referenced_review_ids,
        "normalizer_cache_hit": bool(normalizer_cache_hit),
        "rag_enabled": bool(rag_enabled),
    }

    review_result_path = target_dir / "review_result.json"
    review_report_path = target_dir / "review_report.md"
    legacy_review_report_path = target_dir / "review_report.json"
    risk_items_path = target_dir / "risk_items.json"
    open_questions_path = target_dir / "open_questions.json"
    legacy_summary_path = target_dir / "review_summary.md"

    report_json = json.dumps(report_payload, ensure_ascii=False, indent=2)
    review_result_path.write_text(report_json, encoding="utf-8")
    legacy_review_report_path.write_text(report_json, encoding="utf-8")
    risk_items_path.write_text(
        json.dumps({"risk_items": risk_items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    open_questions_path.write_text(
        json.dumps({"open_questions": open_questions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    review_report_path.write_text(
        _render_review_report(report_payload), encoding="utf-8"
    )
    legacy_summary_path.write_text(_render_summary(report_payload), encoding="utf-8")

    artifacts = AggregatedReviewArtifacts(
        review_result_json=str(review_result_path),
        review_report_md=str(review_report_path),
        risk_items_json=str(risk_items_path),
        open_questions_json=str(open_questions_path),
        review_report_json=str(legacy_review_report_path),
        review_summary_md=str(legacy_summary_path),
    )
    return AggregatedReview(
        review_mode=selected_mode,
        findings=tuple(findings),
        risk_items=tuple(risk_items),
        open_questions=tuple(open_questions),
        conflicts=tuple(conflicts),
        reviewer_summaries=reviewer_summaries,
        tool_calls=tuple(tool_calls),
        partial_review=partial_review,
        reviewers_completed=tuple(reviewers_completed),
        reviewers_failed=tuple(reviewers_failed),
        reviewers_used=tuple(reviewers_used_list),
        reviewers_skipped=tuple(reviewers_skipped_list),
        reviewer_count=len(results),
        gating=gating_payload,
        clarification=clarification,
        summary=summary,
        meta=meta,
        memory_hits=tuple(selected_memory_hits),
        similar_reviews_referenced=tuple(referenced_review_ids),
        normalizer_cache_hit=bool(normalizer_cache_hit),
        rag_enabled=bool(rag_enabled),
        artifacts=artifacts,
    )


def _build_review_meta(
    results: tuple[ReviewerResult, ...],
    *,
    selected_mode: str,
    gating_decision: dict[str, Any] | None,
    gating_reasons: Iterable[str],
    reviewers_used: list[str],
    reviewers_skipped: list[dict[str, str]],
) -> dict[str, Any]:
    reviewers_completed: list[str] = []
    reviewers_failed: list[dict[str, str]] = []

    for result in results:
        reviewer = str(result.reviewer or "").strip()
        if not reviewer:
            continue
        status = str(result.status or "completed").strip().lower() or "completed"
        if status in {"completed", "ok", "success"}:
            reviewers_completed = _merge_unique(reviewers_completed, [reviewer])
            continue
        reviewers_failed.append(
            {
                "reviewer": reviewer,
                "status": status,
                "reason": str(result.error_message or "").strip(),
            }
        )

    return {
        "review_mode": selected_mode,
        "partial_review": bool(reviewers_failed),
        "reviewers_completed": reviewers_completed,
        "reviewers_failed": reviewers_failed,
        "reviewers_used": reviewers_used,
        "reviewers_skipped": reviewers_skipped,
        "gating_reasons": list(gating_reasons),
        "gating": _build_gating_payload(gating_decision, selected_mode, gating_reasons),
    }


def _build_gating_payload(
    gating_decision: dict[str, Any] | None,
    selected_mode: str,
    gating_reasons: Iterable[str],
) -> dict[str, Any]:
    payload = dict(gating_decision) if isinstance(gating_decision, dict) else {}
    reasons = _merge_unique([], [str(item) for item in gating_reasons])
    existing_reasons = (
        [str(item) for item in payload.get("reasons", [])]
        if isinstance(payload.get("reasons"), list)
        else []
    )
    payload["reasons"] = _merge_unique(existing_reasons, reasons)
    payload["selected_mode"] = str(payload.get("selected_mode") or selected_mode)
    payload["skipped"] = bool(payload.get("skipped", False))
    return payload


def _build_summary_payload(
    *,
    normalized_requirement: dict[str, Any],
    findings: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
    reviewers_used: list[str],
    reviewers_skipped: list[dict[str, str]],
) -> dict[str, Any]:
    severity_pool = [
        str(item.get("severity", "")).strip().lower()
        for item in [*findings, *risk_items]
        if isinstance(item, dict)
    ]
    if any(level == "high" for level in severity_pool):
        overall_risk = "high"
    elif any(level == "medium" for level in severity_pool):
        overall_risk = "medium"
    else:
        overall_risk = "low"

    in_scope = _merge_unique(
        [],
        [
            *[str(item) for item in normalized_requirement.get("in_scope", []) or []],
            *[str(item) for item in normalized_requirement.get("modules", []) or []],
            *[str(item) for item in normalized_requirement.get("scenarios", []) or []],
        ],
    )
    if not in_scope:
        summary_text = str(normalized_requirement.get("summary", "")).strip()
        if summary_text:
            in_scope = [summary_text]

    out_of_scope = _merge_unique(
        [],
        [
            *[
                str(item)
                for item in normalized_requirement.get("out_of_scope", []) or []
            ],
            *[
                f"{str(item.get('reviewer', '')).strip()} reviewer skipped: {str(item.get('reason', '')).strip()}"
                for item in reviewers_skipped
                if isinstance(item, dict) and str(item.get("reviewer", "")).strip()
            ],
        ],
    )

    return {
        "overall_risk": overall_risk,
        "in_scope": in_scope,
        "out_of_scope": out_of_scope,
        "reviewers_used": reviewers_used,
    }


def _aggregate_findings(results: tuple[ReviewerResult, ...]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        for finding in result.findings:
            key = _normalize_topic_key(finding.category, finding.title, finding.detail)
            source_reviewer = _resolve_source_reviewer(
                result.reviewer, finding.reviewer
            )
            default_suggested_action = _resolve_suggested_action(finding)
            default_assignee = _resolve_assignee(finding, source_reviewer)
            bucket = merged.setdefault(
                key,
                {
                    "finding_id": _build_finding_id(
                        finding.category, finding.title, finding.detail
                    ),
                    "title": finding.title,
                    "detail": finding.detail,
                    "description": finding.detail,
                    "severity": normalize_severity(finding.severity),
                    "category": finding.category,
                    "source_reviewer": source_reviewer,
                    "suggested_action": str(finding.suggested_action or "").strip()
                    or default_suggested_action,
                    "assignee": str(finding.assignee or "").strip() or default_assignee,
                    "reviewers": [],
                    "requirement_refs": [],
                    "evidence": [],
                    "ambiguity_type": "",
                    "clarification_applied": False,
                    "original_severity": "",
                    "user_clarification": "",
                },
            )
            bucket["severity"] = _max_severity(
                bucket["severity"], normalize_severity(finding.severity)
            )
            bucket["reviewers"] = _merge_unique(bucket["reviewers"], [source_reviewer])
            bucket["requirement_refs"] = _merge_unique(
                bucket["requirement_refs"], list(finding.requirement_refs)
            )
            incoming_action = (
                str(finding.suggested_action or "").strip() or default_suggested_action
            )
            bucket["evidence"] = _merge_evidence(
                bucket.get("evidence", []),
                [item.to_dict() for item in finding.evidence],
            )
            if len(incoming_action) > len(str(bucket.get("suggested_action", ""))):
                bucket["suggested_action"] = incoming_action
            if not str(bucket.get("assignee", "")).strip():
                bucket["assignee"] = (
                    str(finding.assignee or "").strip() or default_assignee
                )
            if not str(bucket.get("source_reviewer", "")).strip():
                bucket["source_reviewer"] = source_reviewer
    return list(merged.values())


def _apply_clarification_metadata(
    findings: list[dict[str, Any]],
    reviewer_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    updated_findings: list[dict[str, Any]] = []
    for finding in findings:
        updated = dict(finding)
        updated.setdefault("ambiguity_type", "")
        updated.setdefault("clarification_applied", False)
        updated.setdefault("original_severity", "")
        updated.setdefault("user_clarification", "")
        updated_findings.append(updated)

    for summary in reviewer_summaries:
        reviewer = str(summary.get("reviewer", "") or "").strip().lower()
        ambiguity_type = str(summary.get("ambiguity_type", "") or "").strip()
        clarification_question = str(
            summary.get("clarification_question", "") or ""
        ).strip()
        if not reviewer or not ambiguity_type or not clarification_question:
            continue
        for finding in updated_findings:
            severity = str(finding.get("severity", "") or "").strip().lower()
            source_reviewer = (
                str(finding.get("source_reviewer", "") or "").strip().lower()
            )
            reviewers = {
                str(item).strip().lower()
                for item in finding.get("reviewers", [])
                if str(item).strip()
            }
            if severity == "high" and (
                reviewer == source_reviewer or reviewer in reviewers
            ):
                finding["ambiguity_type"] = "unanswerable"
    return updated_findings


def _aggregate_tool_calls(results: tuple[ReviewerResult, ...]) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for result in results:
        for tool_call in result.tool_calls:
            payload = tool_call.to_dict()
            payload["reviewer"] = payload.get("reviewer") or result.reviewer
            tool_calls.append(payload)
    return tool_calls


def _aggregate_risks(results: tuple[ReviewerResult, ...]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        for risk in result.risk_items:
            key = _normalize_topic_key(risk.title, risk.detail)
            bucket = merged.setdefault(
                key,
                {
                    "title": risk.title,
                    "detail": risk.detail,
                    "severity": normalize_severity(risk.severity),
                    "category": risk.category,
                    "mitigation": risk.mitigation,
                    "reviewers": [],
                },
            )
            bucket["severity"] = _max_severity(
                bucket["severity"], normalize_severity(risk.severity)
            )
            if risk.mitigation and len(risk.mitigation) > len(
                str(bucket.get("mitigation", ""))
            ):
                bucket["mitigation"] = risk.mitigation
            bucket["reviewers"] = _merge_unique(
                bucket["reviewers"], [result.reviewer or risk.reviewer]
            )
    return list(merged.values())


def _aggregate_open_questions(
    results: tuple[ReviewerResult, ...],
) -> list[dict[str, Any]]:
    reviewer_memory_refs = _reviewer_memory_refs(results)
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        refs = reviewer_memory_refs.get(str(result.reviewer or "").strip(), [])
        for question in result.open_questions:
            key = _normalize_topic_key(question)
            bucket = merged.setdefault(
                key,
                {
                    "question": question,
                    "reviewers": [],
                    "memory_refs": [],
                },
            )
            bucket["reviewers"] = _merge_unique(bucket["reviewers"], [result.reviewer])
            bucket["memory_refs"] = _merge_unique(bucket["memory_refs"], refs)
    return list(merged.values())


def _reviewer_memory_refs(results: tuple[ReviewerResult, ...]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for result in results:
        reviewer = str(result.reviewer or "").strip()
        if not reviewer:
            continue
        refs: list[str] = []
        for evidence in result.evidence:
            if str(evidence.source or "").strip().lower() != "review_memory":
                continue
            ref = str(evidence.ref or "").strip()
            if ref:
                refs.append(ref)
        mapping[reviewer] = _merge_unique(mapping.get(reviewer, []), refs)
    return mapping


def _detect_conflicts(results: tuple[ReviewerResult, ...]) -> list[dict[str, Any]]:
    conflicts = _detect_severity_conflicts(results)
    conflicts.extend(_detect_semantic_conflicts(results))
    return _arbitrate_high_severity_conflicts(conflicts, results)


def _arbitrate_high_severity_conflicts(
    conflicts: list[dict[str, Any]],
    results: tuple[ReviewerResult, ...],
) -> list[dict[str, Any]]:
    perspectives = _build_reviewer_perspectives(results)
    resolved_conflicts: list[dict[str, Any]] = []
    for conflict in conflicts:
        resolved = dict(conflict)
        resolved["conflict_severity"] = _infer_conflict_severity(resolved)
        resolution = resolved.get("resolution")
        if resolved["conflict_severity"] == "high":
            resolution = arbitrate_conflict(
                resolved,
                product_summary=perspectives.get("product", ""),
                engineering_summary=perspectives.get("engineering", ""),
                qa_summary=perspectives.get("qa", ""),
                security_summary=perspectives.get("security", ""),
            ).to_dict()
        if isinstance(resolution, dict):
            resolved["resolution"] = dict(resolution)
            resolved["requires_manual_resolution"] = bool(
                resolution.get("needs_human", True)
            )
        else:
            resolved["resolution"] = None
            resolved["requires_manual_resolution"] = True
        resolved_conflicts.append(resolved)
    return resolved_conflicts


def _build_reviewer_perspectives(results: tuple[ReviewerResult, ...]) -> dict[str, str]:
    perspectives: dict[str, list[str]] = {
        "product": [],
        "engineering": [],
        "qa": [],
        "security": [],
    }
    for result in results:
        reviewer = str(result.reviewer or "").strip().lower()
        if reviewer not in perspectives:
            continue
        parts = [
            str(result.summary or "").strip(),
            str(result.reviewer_status_detail or "").strip(),
            *[str(note).strip() for note in result.notes if str(note).strip()],
        ]
        perspectives[reviewer] = _merge_unique(
            perspectives[reviewer], [part for part in parts if part]
        )
    return {
        reviewer: " ".join(parts).strip() for reviewer, parts in perspectives.items()
    }


def _infer_conflict_severity(conflict: dict[str, Any]) -> str:
    raw_conflict_severity = (
        str(conflict.get("conflict_severity", "") or "").strip().lower()
    )
    if raw_conflict_severity:
        return normalize_severity(raw_conflict_severity)

    severities: list[str] = []
    for key in ("finding_severity", "risk_severity"):
        raw_value = str(conflict.get(key, "") or "").strip()
        if not raw_value:
            continue
        for part in [
            segment.strip() for segment in raw_value.split(",") if segment.strip()
        ]:
            severities.append(normalize_severity(part))

    if conflict.get("type") in {
        "scope_inclusion_vs_dependency_blocker",
        "acceptance_complete_vs_testability_gap",
        "release_ok_vs_approval_blocker",
    }:
        severities.append("high")

    if not severities:
        return "medium"
    return max(
        severities, key=lambda value: {"low": 0, "medium": 1, "high": 2}.get(value, 1)
    )


def _detect_severity_conflicts(
    results: tuple[ReviewerResult, ...],
) -> list[dict[str, Any]]:
    topic_map: dict[str, dict[str, Any]] = {}
    for result in results:
        for finding in result.findings:
            key = _normalize_topic_key(finding.title, finding.detail)
            bucket = topic_map.setdefault(
                key,
                {
                    "topic": finding.title,
                    "finding_severities": set(),
                    "risk_severities": set(),
                    "reviewers": set(),
                },
            )
            bucket["finding_severities"].add(normalize_severity(finding.severity))
            bucket["reviewers"].add(
                _resolve_source_reviewer(result.reviewer, finding.reviewer)
            )
        for risk in result.risk_items:
            key = _normalize_topic_key(risk.title, risk.detail)
            bucket = topic_map.setdefault(
                key,
                {
                    "topic": risk.title,
                    "finding_severities": set(),
                    "risk_severities": set(),
                    "reviewers": set(),
                },
            )
            bucket["risk_severities"].add(normalize_severity(risk.severity))
            bucket["reviewers"].add(
                str(result.reviewer or risk.reviewer or "").strip() or "unknown"
            )

    conflicts: list[dict[str, Any]] = []
    for value in topic_map.values():
        finding_levels = _sort_severities(value["finding_severities"])
        risk_levels = _sort_severities(value["risk_severities"])
        topic = str(value["topic"] or "shared topic").strip() or "shared topic"
        reviewers = sorted(value["reviewers"])
        if len(finding_levels) > 1:
            finding_severity = ", ".join(finding_levels)
            risk_severity = ", ".join(risk_levels) if risk_levels else ""
            description = (
                f"Severity mismatch on '{topic}': findings use {finding_severity}"
                + (f" while risks use {risk_severity}." if risk_severity else ".")
            )
            conflicts.append(
                _build_conflict(
                    conflict_type="severity_mismatch",
                    description=description,
                    reviewers=reviewers,
                    topic=topic,
                    finding_severity=finding_severity,
                    risk_severity=risk_severity,
                    conflict_severity=_infer_conflict_severity(
                        {
                            "finding_severity": finding_severity,
                            "risk_severity": risk_severity,
                        }
                    ),
                )
            )
            continue
        if finding_levels and risk_levels and finding_levels[0] != risk_levels[0]:
            conflicts.append(
                _build_conflict(
                    conflict_type="severity_mismatch",
                    description=(
                        f"Severity mismatch on '{topic}': findings use {finding_levels[0]} while risks use {risk_levels[0]}."
                    ),
                    reviewers=reviewers,
                    topic=topic,
                    finding_severity=finding_levels[0],
                    risk_severity=risk_levels[0],
                    conflict_severity=_max_severity(finding_levels[0], risk_levels[0]),
                )
            )
    return conflicts


def _detect_semantic_conflicts(
    results: tuple[ReviewerResult, ...],
) -> list[dict[str, Any]]:
    signals = _collect_semantic_signals(results)
    conflicts: list[dict[str, Any]] = []

    for rule in _SEMANTIC_CONFLICT_RULES:
        left_matches = signals.get(rule["left_signal"], [])
        right_matches = signals.get(rule["right_signal"], [])
        if not left_matches or not right_matches:
            continue

        left_reviewers = _merge_unique(
            [], [match["reviewer"] for match in left_matches]
        )
        right_reviewers = _merge_unique(
            [], [match["reviewer"] for match in right_matches]
        )
        if not left_reviewers or not right_reviewers:
            continue
        if set(left_reviewers) == set(right_reviewers):
            continue

        reviewers = _merge_unique(left_reviewers, right_reviewers)
        left_subject, left_verb = _format_reviewer_subject(
            left_reviewers, singular_verb="indicates", plural_verb="indicate"
        )
        right_subject, right_verb = _format_reviewer_subject(
            right_reviewers, singular_verb="flags", plural_verb="flag"
        )
        if rule["type"] == "acceptance_complete_vs_testability_gap":
            right_subject, right_verb = _format_reviewer_subject(
                right_reviewers, singular_verb="identifies", plural_verb="identify"
            )
        elif rule["type"] == "release_ok_vs_approval_blocker":
            left_subject, left_verb = _format_reviewer_subject(
                left_reviewers, singular_verb="marks", plural_verb="mark"
            )
            right_subject, right_verb = _format_reviewer_subject(
                right_reviewers, singular_verb="requires", plural_verb="require"
            )
        description = rule["description_template"].format(
            left_subject=left_subject,
            left_verb=left_verb,
            right_subject=right_subject,
            right_verb=right_verb,
        )
        conflicts.append(
            _build_conflict(
                conflict_type=rule["type"],
                description=description,
                reviewers=reviewers,
                conflict_severity="high",
            )
        )

    return conflicts


def _render_review_report(report_payload: dict[str, Any]) -> str:
    findings = (
        report_payload.get("findings", [])
        if isinstance(report_payload.get("findings"), list)
        else []
    )
    risk_items = (
        report_payload.get("risk_items", [])
        if isinstance(report_payload.get("risk_items"), list)
        else []
    )
    open_questions = (
        report_payload.get("open_questions", [])
        if isinstance(report_payload.get("open_questions"), list)
        else []
    )
    conflicts = (
        report_payload.get("conflicts", [])
        if isinstance(report_payload.get("conflicts"), list)
        else []
    )
    summaries = (
        report_payload.get("reviewer_summaries", [])
        if isinstance(report_payload.get("reviewer_summaries"), list)
        else []
    )
    tool_calls = (
        report_payload.get("tool_calls", [])
        if isinstance(report_payload.get("tool_calls"), list)
        else []
    )
    meta = (
        report_payload.get("meta", {})
        if isinstance(report_payload.get("meta"), dict)
        else {}
    )

    completed = ", ".join(meta.get("reviewers_completed", []) or []) or "none"
    failed = _format_failed_reviewers(meta.get("reviewers_failed", []))
    manual_review_message = str(meta.get("manual_review_message", "") or "").strip()
    lines = [
        "# Review Report",
        "",
        "## Meta",
        "",
        f"- Review mode: {meta.get('review_mode', 'parallel_review')}",
        f"- Partial review: {'yes' if meta.get('partial_review') else 'no'}",
        f"- Reviewers completed: {completed}",
        f"- Reviewers failed: {failed}",
        f"- Findings: {len(findings)}",
        f"- Risk Items: {len(risk_items)}",
        f"- Open Questions: {len(open_questions)}",
        f"- Conflicts: {len(conflicts)}",
        f"- Tool Calls: {len(tool_calls)}",
    ]
    if manual_review_message:
        lines.extend(
            [
                f"- Follow-up: {manual_review_message}",
            ]
        )
    lines.extend(
        [
            "",
            "## Findings",
            "",
        ]
    )
    lines.extend(_finding_lines(findings))
    lines.extend(
        [
            "",
            "## Risks",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [
                f"[{item['severity']}] {item['title']} -> {item.get('mitigation', '')}".strip()
                for item in risk_items
            ],
            "No risk items.",
        )
    )
    lines.extend(
        [
            "",
            "## Open Questions",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [item["question"] for item in open_questions], "No open questions."
        )
    )
    lines.extend(
        [
            "",
            "## Reviewer Notes",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [
                _format_reviewer_note(item)
                for item in summaries
                if _format_reviewer_note(item)
            ],
            "No reviewer notes.",
        )
    )
    lines.extend(
        [
            "",
            "## Tool Trace",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [_format_tool_call_line(item) for item in tool_calls],
            "No tool calls.",
        )
    )
    lines.extend(_conflict_section_lines(conflicts))
    return "\n".join(lines).strip() + "\n"


def _render_summary(report_payload: dict[str, Any]) -> str:
    findings = (
        report_payload.get("findings", [])
        if isinstance(report_payload.get("findings"), list)
        else []
    )
    risk_items = (
        report_payload.get("risk_items", [])
        if isinstance(report_payload.get("risk_items"), list)
        else []
    )
    open_questions = (
        report_payload.get("open_questions", [])
        if isinstance(report_payload.get("open_questions"), list)
        else []
    )
    conflicts = (
        report_payload.get("conflicts", [])
        if isinstance(report_payload.get("conflicts"), list)
        else []
    )
    summaries = (
        report_payload.get("reviewer_summaries", [])
        if isinstance(report_payload.get("reviewer_summaries"), list)
        else []
    )
    tool_calls = (
        report_payload.get("tool_calls", [])
        if isinstance(report_payload.get("tool_calls"), list)
        else []
    )
    meta = (
        report_payload.get("meta", {})
        if isinstance(report_payload.get("meta"), dict)
        else {}
    )

    manual_review_message = str(meta.get("manual_review_message", "") or "").strip()
    lines = [
        "# Review Summary",
        "",
        f"- Review mode: {meta.get('review_mode', 'parallel_review')}",
        f"- Reviewers: {report_payload.get('reviewer_count', 0)}",
        f"- Partial review: {'yes' if meta.get('partial_review') else 'no'}",
        f"- Findings: {len(findings)}",
        f"- Risk Items: {len(risk_items)}",
        f"- Open Questions: {len(open_questions)}",
        f"- Conflicts: {len(conflicts)}",
        f"- Tool Calls: {len(tool_calls)}",
    ]
    if manual_review_message:
        lines.extend(
            [
                f"- Follow-up: {manual_review_message}",
            ]
        )
    lines.extend(
        [
            "",
            "## Findings",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [f"[{item['severity']}] {item['title']}" for item in findings],
            "No findings.",
        )
    )
    lines.extend(
        [
            "",
            "## Risks",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [f"[{item['severity']}] {item['title']}" for item in risk_items],
            "No risk items.",
        )
    )
    lines.extend(
        [
            "",
            "## Open Questions",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [item["question"] for item in open_questions], "No open questions."
        )
    )
    lines.extend(
        [
            "",
            "## Reviewer Notes",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [
                _format_reviewer_note(item)
                for item in summaries
                if _format_reviewer_note(item)
            ],
            "No reviewer notes.",
        )
    )
    lines.extend(
        [
            "",
            "## Tool Trace",
            "",
        ]
    )
    lines.extend(
        _bullet_lines(
            [_format_tool_call_line(item) for item in tool_calls],
            "No tool calls.",
        )
    )
    lines.extend(_conflict_section_lines(conflicts))
    return "\n".join(lines).strip() + "\n"


def _finding_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- No findings."]

    lines: list[str] = []
    for item in items:
        source_reviewer = str(item.get("source_reviewer", "") or "unknown")
        lines.extend(
            [
                f"- [{item.get('severity', 'medium')}] {item.get('finding_id', 'finding')} {item.get('title', 'Finding')}",
                f"  - Description: {item.get('description', item.get('detail', ''))}",
                f"  - Category: {item.get('category', 'general')}",
                f"  - Source reviewer: {source_reviewer}",
                f"  - Suggested action: {item.get('suggested_action', '') or 'Clarify the requirement and define an owner.'}",
                f"  - Assignee: {item.get('assignee', '') or source_reviewer}",
            ]
        )
        evidence_items = (
            item.get("evidence", []) if isinstance(item.get("evidence"), list) else []
        )
        if evidence_items:
            lines.append(
                f"  - Evidence: {', '.join(_format_evidence_label(evidence) for evidence in evidence_items)}"
            )
    return lines


def _format_failed_reviewers(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "none"

    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            reviewer = str(item.get("reviewer", "") or "unknown")
            status = str(item.get("status", "failed") or "failed")
            reason = str(item.get("reason", "") or "").strip()
            if reason:
                parts.append(f"{reviewer} ({status}: {reason})")
            else:
                parts.append(f"{reviewer} ({status})")
        else:
            parts.append(str(item))
    return ", ".join(parts) or "none"


def _build_manual_review_message(
    *,
    partial_review: bool,
    reviewers_failed: list[dict[str, str]],
    findings: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
) -> str:
    if not partial_review or not reviewers_failed:
        return ""

    has_high_risk = any(
        str(item.get("severity", "")).strip().lower() == "high"
        for item in [*findings, *risk_items]
        if isinstance(item, dict)
    )
    if not has_high_risk:
        return ""

    failed_reviewers = _format_failed_reviewers(reviewers_failed)
    return f"Manual review required: high-risk findings exist while these reviewers were partial or unavailable: {failed_reviewers}"


def _build_finding_id(category: str, title: str, detail: str) -> str:
    normalized = _normalize_topic_key(category, title, detail)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"finding-{digest}"


def _resolve_source_reviewer(result_reviewer: str, finding_reviewer: str) -> str:
    reviewer = str(result_reviewer or finding_reviewer or "").strip()
    return reviewer or "unknown"


def _resolve_suggested_action(finding: ReviewFinding) -> str:
    explicit = str(finding.suggested_action or "").strip()
    if explicit:
        return explicit

    category = str(finding.category or "").strip().lower()
    suggestions = {
        "scope": "Add concrete user scenarios and business flows to the PRD before implementation starts.",
        "acceptance": "Add explicit, testable acceptance criteria that product and QA can sign off on.",
        "architecture": "Document impacted modules, integration boundaries, and ownership before build starts.",
        "testability": "Expand pass/fail criteria and edge-case coverage so QA can validate the requirement.",
        "security": "Add explicit security, compliance, and release-control expectations to the PRD.",
    }
    return suggestions.get(
        category,
        "Clarify the finding and capture a concrete follow-up action in the PRD.",
    )


def _resolve_assignee(finding: ReviewFinding, source_reviewer: str) -> str:
    explicit = str(finding.assignee or "").strip()
    if explicit:
        return explicit

    category = str(finding.category or "").strip().lower()
    default_assignees = {
        "scope": "product",
        "acceptance": "product",
        "architecture": "engineering",
        "integration": "engineering",
        "testability": "qa",
        "quality": "qa",
        "security": "security",
    }
    return default_assignees.get(
        category, str(source_reviewer or "product").strip() or "product"
    )


def _collect_semantic_signals(
    results: tuple[ReviewerResult, ...],
) -> dict[str, list[dict[str, str]]]:
    signals: dict[str, list[dict[str, str]]] = {
        signal: [] for signal in _SEMANTIC_SIGNAL_PATTERNS
    }
    for result in results:
        reviewer = str(result.reviewer or "").strip().lower()
        for signal, patterns in _SEMANTIC_SIGNAL_PATTERNS.items():
            allowed_reviewers = _SEMANTIC_SIGNAL_REVIEWERS.get(signal, ())
            if allowed_reviewers and reviewer not in allowed_reviewers:
                continue
            for source, text in _iter_result_texts(result):
                normalized = _normalize_topic_key(text)
                if not normalized:
                    continue
                if any(pattern in normalized for pattern in patterns):
                    signals[signal].append(
                        {
                            "reviewer": reviewer or "unknown",
                            "source": source,
                            "text": str(text or "").strip(),
                        }
                    )
                    break
    return signals


def _iter_result_texts(result: ReviewerResult) -> Iterable[tuple[str, str]]:
    if result.summary:
        yield "summary", result.summary

    for question in result.open_questions:
        if question:
            yield "open_question", question

    for finding in result.findings:
        if finding.title:
            yield "finding_title", finding.title
        if finding.detail:
            yield "finding_detail", finding.detail
        if finding.suggested_action:
            yield "finding_action", finding.suggested_action

    for risk in result.risk_items:
        if risk.title:
            yield "risk_title", risk.title
        if risk.detail:
            yield "risk_detail", risk.detail
        if risk.mitigation:
            yield "risk_mitigation", risk.mitigation


def _build_conflict(
    *,
    conflict_type: str,
    description: str,
    reviewers: list[str],
    topic: str = "",
    finding_severity: str = "",
    risk_severity: str = "",
    conflict_severity: str = "medium",
) -> dict[str, Any]:
    normalized_reviewers = _merge_unique([], reviewers)
    conflict_id = _build_conflict_id(
        conflict_type, topic, description, ",".join(normalized_reviewers)
    )
    payload = {
        "conflict_id": conflict_id,
        "type": conflict_type,
        "description": description,
        "reviewers": normalized_reviewers,
        "conflict_severity": normalize_severity(conflict_severity),
        "resolution": None,
        "requires_manual_resolution": True,
        "status": conflict_type,
    }
    if topic:
        payload["topic"] = topic
    if finding_severity:
        payload["finding_severity"] = finding_severity
    if risk_severity:
        payload["risk_severity"] = risk_severity
    return payload


def _build_conflict_id(*parts: str) -> str:
    normalized = _normalize_topic_key(*parts)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"conflict-{digest}"


def _conflict_section_lines(conflicts: list[dict[str, Any]]) -> list[str]:
    if not conflicts:
        return ["", "## Conflicts", "", "- No conflicts."]

    resolved = [
        item
        for item in conflicts
        if not bool(item.get("requires_manual_resolution", True))
    ]
    unresolved = [
        item for item in conflicts if bool(item.get("requires_manual_resolution", True))
    ]
    lines = ["", "## Conflicts", ""]
    lines.append(f"- Resolved conflicts: {len(resolved)}")
    lines.append(f"- Unresolved conflicts: {len(unresolved)}")
    lines.extend(["", "### Resolved Conflicts", ""])
    lines.extend(
        _bullet_lines(
            [_format_conflict_line(item) for item in resolved], "No resolved conflicts."
        )
    )
    lines.extend(["", "### Unresolved Conflicts", ""])
    lines.extend(
        _bullet_lines(
            [_format_conflict_line(item) for item in unresolved],
            "No unresolved conflicts.",
        )
    )
    return lines


def _format_conflict_line(item: dict[str, Any]) -> str:
    description = str(item.get("description", "") or "").strip()
    reviewers = _format_reviewer_list(item.get("reviewers", []))
    conflict_severity = str(item.get("conflict_severity", "") or "").strip().lower()
    resolution = (
        item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
    )
    recommendation = str(resolution.get("recommendation", "") or "").strip()
    reasoning = str(resolution.get("reasoning", "") or "").strip()
    decided_by = str(resolution.get("decided_by", "") or "").strip()
    status = (
        "needs human" if item.get("requires_manual_resolution", True) else "resolved"
    )

    parts = [description or "Conflict requires manual resolution."]
    if reviewers:
        parts.append(f"Reviewers: {reviewers}.")
    if conflict_severity:
        parts.append(f"Severity: {conflict_severity}.")
    parts.append(f"Status: {status}.")
    if recommendation:
        parts.append(f"Recommendation: {recommendation}")
    if reasoning:
        parts.append(f"Reasoning: {reasoning}")
    if decided_by:
        parts.append(f"Decided by: {decided_by}")
    return " ".join(parts).strip()


def _format_reviewer_note(item: dict[str, Any]) -> str:
    reviewer = str(item.get("reviewer", "") or "unknown").strip()
    summary = str(item.get("summary", "") or "").strip()
    status = str(item.get("status", "completed") or "completed").strip()
    status_detail = str(item.get("status_detail", "") or "").strip()
    clarification = str(item.get("clarification_question", "") or "").strip()
    ambiguity_type = str(item.get("ambiguity_type", "") or "").strip()
    notes = (
        [str(note).strip() for note in item.get("notes", []) if str(note).strip()]
        if isinstance(item.get("notes"), list)
        else []
    )
    parts = [f"{reviewer} [{status}]"]
    if summary:
        parts.append(summary)
    if status_detail:
        parts.append(status_detail)
    if ambiguity_type:
        parts.append(f"ambiguity={ambiguity_type}")
    if clarification:
        parts.append(f"clarify: {clarification}")
    if notes:
        parts.append(f"notes: {' | '.join(notes)}")
    return "; ".join(parts)


def _format_tool_call_line(item: dict[str, Any]) -> str:
    reviewer = str(item.get("reviewer", "") or "unknown").strip()
    tool_name = str(item.get("tool_name", "tool") or "tool").strip()
    status = str(item.get("status", "unknown") or "unknown").strip()
    output_summary = str(item.get("output_summary", "") or "").strip()
    degraded_reason = str(item.get("degraded_reason", "") or "").strip()
    if degraded_reason:
        output_summary = f"{output_summary} ({degraded_reason})".strip()
    return f"{reviewer} -> {tool_name} [{status}] {output_summary}".strip()


def _format_evidence_label(item: dict[str, Any]) -> str:
    title = str(item.get("title", "evidence") or "evidence").strip()
    source = str(item.get("source", "") or "").strip()
    ref = str(item.get("ref", "") or "").strip()
    parts = [title]
    if source:
        parts.append(source)
    if ref:
        parts.append(ref)
    return " / ".join(parts)


def _format_reviewer_list(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return ""
    return ", ".join(str(item).strip() for item in items if str(item or "").strip())


_REVIEWER_DISPLAY_NAMES = {
    "product": "Product",
    "engineering": "Engineering",
    "qa": "QA",
    "security": "Security",
}


def _format_reviewer_subject(
    items: list[str], *, singular_verb: str, plural_verb: str
) -> tuple[str, str]:
    names = [
        _REVIEWER_DISPLAY_NAMES.get(
            str(item).strip().lower(), str(item).strip().title()
        )
        for item in items
        if str(item or "").strip()
    ]
    if not names:
        return "Reviewers", plural_verb
    subject = ", ".join(names)
    return subject, singular_verb if len(names) == 1 else plural_verb


def _bullet_lines(items: list[str], empty_message: str) -> list[str]:
    if not items:
        return [f"- {empty_message}"]
    return [f"- {item}" for item in items]


def _normalize_topic_key(*parts: str) -> str:
    text = " ".join(str(part or "") for part in parts).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing)
    seen = {item for item in merged if item}
    for item in incoming:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _merge_evidence(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        if not isinstance(item, dict):
            continue
        key = "|".join(
            [
                str(item.get("source", "") or ""),
                str(item.get("ref", "") or ""),
                str(item.get("title", "") or ""),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(item))
    return merged


def _sort_severities(values: set[str]) -> list[str]:
    order = {"low": 0, "medium": 1, "high": 2}
    return sorted(values, key=lambda value: (order.get(value, 1), value))


def _max_severity(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return left if order.get(left, 1) >= order.get(right, 1) else right
