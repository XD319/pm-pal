"""Post-review clarification gate helpers."""

from __future__ import annotations

import hashlib
from typing import Any

MAX_CLARIFICATION_QUESTIONS = 3


def build_clarification_payload(
    findings: list[dict[str, Any]],
    reviewer_summaries: list[dict[str, Any]],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing_payload = dict(existing) if isinstance(existing, dict) else {}
    existing_questions = existing_payload.get("questions")
    if isinstance(existing_questions, list) and existing_questions:
        questions = [
            dict(item) for item in existing_questions if isinstance(item, dict)
        ]
    else:
        questions = identify_clarification_questions(findings, reviewer_summaries)

    answers_applied = [
        dict(item)
        for item in existing_payload.get("answers_applied", [])
        if isinstance(item, dict)
    ]
    findings_updated = [
        dict(item)
        for item in existing_payload.get("findings_updated", [])
        if isinstance(item, dict)
    ]
    triggered = bool(questions)

    if existing_payload.get("status") == "answered":
        status = "answered"
    elif triggered:
        answered_ids = {
            str(item.get("question_id", "")).strip()
            for item in answers_applied
            if str(item.get("question_id", "")).strip()
        }
        pending_questions = [
            item
            for item in questions
            if str(item.get("id", "")).strip() not in answered_ids
        ]
        status = "pending" if pending_questions else "answered"
    else:
        status = "not_needed"

    return {
        "triggered": triggered,
        "status": status,
        "questions": questions,
        "answers_applied": answers_applied,
        "findings_updated": findings_updated,
    }


def identify_clarification_questions(
    findings: list[dict[str, Any]],
    reviewer_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eligible_findings = [
        dict(item) for item in findings if _is_clarification_candidate(item)
    ]
    if not eligible_findings:
        return []

    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for summary in reviewer_summaries:
        reviewer = str(summary.get("reviewer", "")).strip()
        question = str(summary.get("clarification_question", "")).strip()
        ambiguity_type = str(summary.get("ambiguity_type", "")).strip()
        if not reviewer or not question or not ambiguity_type:
            continue

        matching_finding_ids = [
            str(item.get("finding_id", "")).strip()
            for item in eligible_findings
            if _reviewer_matches(item, reviewer)
        ]
        matching_finding_ids = [item for item in matching_finding_ids if item]
        if not matching_finding_ids:
            continue

        key = f"{reviewer}:{question}"
        if key in seen:
            continue
        seen.add(key)
        questions.append(
            {
                "id": _build_question_id(reviewer, question),
                "question": question,
                "reviewer": reviewer,
                "ambiguity_type": "unanswerable",
                "source_ambiguity_type": ambiguity_type,
                "finding_ids": matching_finding_ids,
            }
        )
        if len(questions) >= MAX_CLARIFICATION_QUESTIONS:
            break

    return questions


def apply_clarification_answers(
    findings: list[dict[str, Any]],
    clarification: dict[str, Any],
    answers: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    questions = [
        dict(item)
        for item in clarification.get("questions", [])
        if isinstance(item, dict)
    ]
    answers_by_id = {
        str(item.get("question_id", "")).strip(): str(item.get("answer", "")).strip()
        for item in answers
        if str(item.get("question_id", "")).strip()
        and str(item.get("answer", "")).strip()
    }
    if not answers_by_id:
        return [dict(item) for item in findings], build_clarification_payload(
            findings, [], clarification
        )

    updated_findings = [dict(item) for item in findings]
    findings_updated: list[dict[str, Any]] = [
        dict(item)
        for item in clarification.get("findings_updated", [])
        if isinstance(item, dict)
    ]
    answers_applied: list[dict[str, Any]] = [
        dict(item)
        for item in clarification.get("answers_applied", [])
        if isinstance(item, dict)
    ]

    for question in questions:
        question_id = str(question.get("id", "")).strip()
        answer = answers_by_id.get(question_id, "")
        if not answer:
            continue
        answers_applied = _upsert_answers_applied(
            answers_applied,
            {
                "question_id": question_id,
                "question": str(question.get("question", "")).strip(),
                "answer": answer,
                "reviewer": str(question.get("reviewer", "")).strip(),
            },
        )
        target_finding_ids = {
            str(item).strip()
            for item in question.get("finding_ids", [])
            if str(item).strip()
        }
        for finding in updated_findings:
            finding_id = str(finding.get("finding_id", "")).strip()
            if finding_id not in target_finding_ids:
                continue
            original_severity = (
                str(
                    finding.get("original_severity", "")
                    or finding.get("severity", "medium")
                )
                .strip()
                .lower()
                or "medium"
            )
            finding["original_severity"] = original_severity
            finding["user_clarification"] = answer
            finding["clarification_applied"] = True
            finding["severity"] = _resolved_severity(
                answer=answer, current_severity=original_severity
            )
            finding["detail"] = _append_clarification_text(
                str(
                    finding.get("detail", "") or finding.get("description", "")
                ).strip(),
                answer,
            )
            finding["description"] = finding["detail"]
            findings_updated = _upsert_findings_updated(
                findings_updated,
                {
                    "finding_id": finding_id,
                    "question_id": question_id,
                    "reviewer": str(question.get("reviewer", "")).strip(),
                    "severity_before": original_severity,
                    "severity_after": str(finding.get("severity", "medium"))
                    .strip()
                    .lower(),
                },
            )

    updated_clarification = build_clarification_payload(
        updated_findings,
        [],
        {
            **clarification,
            "questions": questions,
            "answers_applied": answers_applied,
            "findings_updated": findings_updated,
            "status": "answered",
        },
    )
    return updated_findings, updated_clarification


def _is_clarification_candidate(finding: dict[str, Any]) -> bool:
    severity = str(finding.get("severity", "")).strip().lower()
    ambiguity_type = str(finding.get("ambiguity_type", "")).strip().lower()
    return severity == "high" and ambiguity_type == "unanswerable"


def _reviewer_matches(finding: dict[str, Any], reviewer: str) -> bool:
    normalized_reviewer = str(reviewer or "").strip().lower()
    source_reviewer = str(finding.get("source_reviewer", "")).strip().lower()
    reviewers = {
        str(item).strip().lower()
        for item in finding.get("reviewers", [])
        if str(item).strip()
    }
    return normalized_reviewer == source_reviewer or normalized_reviewer in reviewers


def _build_question_id(reviewer: str, question: str) -> str:
    digest = hashlib.sha1(f"{reviewer}:{question}".encode("utf-8")).hexdigest()[:12]
    return f"clarify-{digest}"


def _upsert_answers_applied(
    existing: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> list[dict[str, Any]]:
    question_id = str(incoming.get("question_id", "")).strip()
    merged: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        if str(item.get("question_id", "")).strip() == question_id:
            merged.append(dict(incoming))
            replaced = True
        else:
            merged.append(dict(item))
    if not replaced:
        merged.append(dict(incoming))
    return merged


def _upsert_findings_updated(
    existing: list[dict[str, Any]],
    incoming: dict[str, Any],
) -> list[dict[str, Any]]:
    finding_id = str(incoming.get("finding_id", "")).strip()
    question_id = str(incoming.get("question_id", "")).strip()
    merged: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        same_item = (
            str(item.get("finding_id", "")).strip() == finding_id
            and str(item.get("question_id", "")).strip() == question_id
        )
        if same_item:
            merged.append(dict(incoming))
            replaced = True
        else:
            merged.append(dict(item))
    if not replaced:
        merged.append(dict(incoming))
    return merged


def _append_clarification_text(detail: str, answer: str) -> str:
    clarification_line = f"User clarification: {answer}"
    if clarification_line in detail:
        return detail
    if not detail:
        return clarification_line
    return f"{detail}\n\n{clarification_line}"


def _resolved_severity(answer: str, current_severity: str) -> str:
    normalized = str(answer or "").strip()
    if len(normalized) >= 24 or len(normalized.split()) >= 5:
        return "medium" if current_severity == "high" else current_severity
    return current_severity or "high"
