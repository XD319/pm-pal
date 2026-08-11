"""Generate revision-oriented PRD draft artifacts from completed review outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

GENERATOR_VERSION = "draft_generator_v1"


@dataclass(frozen=True, slots=True)
class DraftArtifact:
    artifact_key: str
    path: str
    trace: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DraftGeneratorInput:
    run_id: str
    requirement_doc: str
    result_payload: dict[str, Any]
    report_payload: dict[str, Any]
    review_report_payload: dict[str, Any]
    findings: tuple[dict[str, Any], ...]
    open_questions: tuple[dict[str, Any], ...]
    risk_items: tuple[dict[str, Any], ...]
    source_artifacts: tuple[str, ...]
    generated_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_json_dict(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    return payload if isinstance(payload, dict) else {}


def _load_named_list(path: Path, key: str) -> list[dict[str, Any]]:
    payload = _load_json(path)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw_items = payload.get(key, [])
        if isinstance(raw_items, list):
            return [item for item in raw_items if isinstance(item, dict)]
    return []


def _copy_dict_list(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _derive_findings(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for payload in payloads:
        direct_findings = _copy_dict_list(payload.get("findings"))
        if direct_findings:
            findings.extend(direct_findings)
            continue

        review_results = _copy_dict_list(payload.get("review_results"))
        for item in review_results:
            issues = [
                str(issue).strip()
                for issue in list(item.get("issues", []) or [])
                if str(issue).strip()
            ]
            suggestions = str(item.get("suggestions", "") or "").strip()
            needs_revision = bool(
                issues
                or suggestions
                or not bool(item.get("is_clear", True))
                or not bool(item.get("is_testable", True))
                or bool(item.get("is_ambiguous", False))
            )
            if not needs_revision:
                continue
            findings.append(
                {
                    "title": str(item.get("id", "") or "review-finding"),
                    "detail": "; ".join(issues)
                    if issues
                    else suggestions or str(item.get("description", "") or "").strip(),
                    "suggestion": suggestions,
                    "requirement_id": str(item.get("id", "") or "").strip(),
                    "description": str(item.get("description", "") or "").strip(),
                }
            )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in findings:
        key = (
            str(item.get("title", "") or "").strip(),
            str(item.get("detail", "") or item.get("suggestion", "") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _derive_open_questions(
    report_payload: dict[str, Any],
    result_payload: dict[str, Any],
    artifact_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if artifact_questions:
        return artifact_questions
    for candidate in (
        report_payload.get("open_questions"),
        report_payload.get("review_open_questions"),
        result_payload.get("open_questions"),
        result_payload.get("review_open_questions"),
    ):
        questions = _copy_dict_list(candidate)
        if questions:
            return questions
    return []


def _derive_risk_items(
    report_payload: dict[str, Any],
    result_payload: dict[str, Any],
    artifact_risks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if artifact_risks:
        return artifact_risks
    for candidate in (
        report_payload.get("risk_items"),
        report_payload.get("review_risk_items"),
        result_payload.get("risk_items"),
        result_payload.get("review_risk_items"),
    ):
        items = _copy_dict_list(candidate)
        if items:
            return items

    risks = _copy_dict_list(report_payload.get("risks")) or _copy_dict_list(
        result_payload.get("risks")
    )
    derived: list[dict[str, Any]] = []
    for risk in risks:
        derived.append(
            {
                "title": str(
                    risk.get("id", "") or risk.get("title", "") or "review-risk"
                ).strip(),
                "detail": str(
                    risk.get("description", "") or risk.get("detail", "") or ""
                ).strip(),
                "severity": str(
                    risk.get("severity", "") or risk.get("impact", "") or "medium"
                )
                .strip()
                .lower(),
                "mitigation": str(risk.get("mitigation", "") or "").strip(),
            }
        )
    return derived


def build_draft_generator_input(run_output: dict[str, Any]) -> DraftGeneratorInput:
    run_id = str(run_output.get("run_id", "") or "").strip()
    run_dir = Path(str(run_output.get("run_dir", "") or "")).resolve()
    report_paths = (
        dict(run_output.get("report_paths", {}) or {})
        if isinstance(run_output.get("report_paths"), dict)
        else {}
    )
    result_payload = (
        dict(run_output.get("result", {}) or {})
        if isinstance(run_output.get("result"), dict)
        else {}
    )

    report_payload = _load_json_dict(
        Path(str(report_paths.get("report_json", "") or ""))
    )
    if not report_payload:
        report_payload = dict(result_payload)

    review_report_payload = _load_json_dict(run_dir / "review_report.json")
    open_questions = _load_named_list(run_dir / "open_questions.json", "open_questions")
    risk_items = _load_named_list(run_dir / "risk_items.json", "risk_items")

    requirement_doc = str(
        report_payload.get("requirement_doc", "")
        or result_payload.get("requirement_doc", "")
        or run_output.get("requirement_doc", "")
        or ""
    )
    if not requirement_doc.strip():
        raise ValueError("requirement_doc is required for PRD draft generation")

    source_artifacts: list[str] = []
    for artifact_name in (
        "report.json",
        "review_report.json",
        "open_questions.json",
        "risk_items.json",
    ):
        if (run_dir / artifact_name).exists():
            source_artifacts.append(artifact_name)
    if not source_artifacts:
        source_artifacts.append("ReviewState.result")

    findings = _derive_findings([review_report_payload, report_payload, result_payload])
    derived_questions = _derive_open_questions(
        report_payload, result_payload, open_questions
    )
    derived_risks = _derive_risk_items(report_payload, result_payload, risk_items)
    return DraftGeneratorInput(
        run_id=run_id,
        requirement_doc=requirement_doc,
        result_payload=result_payload,
        report_payload=report_payload,
        review_report_payload=review_report_payload,
        findings=tuple(findings),
        open_questions=tuple(derived_questions),
        risk_items=tuple(derived_risks),
        source_artifacts=tuple(source_artifacts),
        generated_at=_utc_now_iso(),
    )


def _bullet_lines(items: list[str], *, empty_message: str) -> str:
    if not items:
        return f"- {empty_message}"
    return "\n".join(f"- {item}" for item in items)


def _unique_strings(items: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _build_scope_items(source: DraftGeneratorInput) -> list[str]:
    parsed_items = _copy_dict_list(
        source.report_payload.get("parsed_items")
    ) or _copy_dict_list(source.result_payload.get("parsed_items"))
    items = [
        "Retain the original requirement document below as the baseline scope and treat this draft as an additive revision.",
    ]
    if parsed_items:
        items.append(
            "Confirm the following items are explicitly in scope: "
            + "; ".join(
                str(item.get("description", "")).strip()
                for item in parsed_items[:6]
                if str(item.get("description", "")).strip()
            )
        )
    question_fragments = []
    for question in source.open_questions[:5]:
        text = str(
            question.get("question", "") or question.get("detail", "") or ""
        ).strip()
        if text:
            question_fragments.append(text)
    if question_fragments:
        items.append(
            "Clarify unresolved boundaries that affect scope definition: "
            + "; ".join(question_fragments)
        )
    items.append(
        "Add an explicit out-of-scope subsection for flows, integrations, and operational work that are intentionally excluded from this run."
    )
    return _unique_strings(items)


def _build_acceptance_items(source: DraftGeneratorInput) -> list[str]:
    parsed_items = _copy_dict_list(
        source.report_payload.get("parsed_items")
    ) or _copy_dict_list(source.result_payload.get("parsed_items"))
    acceptance: list[str] = []
    for item in parsed_items:
        requirement_id = str(item.get("id", "") or "").strip()
        for criterion in list(item.get("acceptance_criteria", []) or []):
            text = str(criterion or "").strip()
            if text:
                prefix = f"{requirement_id}: " if requirement_id else ""
                acceptance.append(prefix + text)
    for finding in source.findings[:8]:
        suggestion = str(
            finding.get("suggestion", "") or finding.get("detail", "") or ""
        ).strip()
        requirement_id = str(
            finding.get("requirement_id", "") or finding.get("title", "") or ""
        ).strip()
        if suggestion:
            acceptance.append(
                f"{requirement_id or 'Revision'}: Convert this into a measurable acceptance criterion: {suggestion}"
            )
    return _unique_strings(acceptance)


def _build_edge_case_items(source: DraftGeneratorInput) -> list[str]:
    edge_cases = list(
        (source.report_payload.get("test_plan") or {}).get("edge_cases", []) or []
    )
    items = [str(item).strip() for item in edge_cases if str(item).strip()]
    for risk in source.risk_items[:8]:
        detail = str(risk.get("detail", "") or risk.get("title", "") or "").strip()
        severity = str(risk.get("severity", "") or "medium").strip().lower()
        if detail:
            items.append(f"Validate {severity}-severity edge case: {detail}")
    if not items and source.findings:
        for finding in source.findings[:5]:
            detail = str(
                finding.get("detail", "") or finding.get("suggestion", "") or ""
            ).strip()
            if detail:
                items.append(f"Cover the ambiguous path called out in review: {detail}")
    return _unique_strings(items)


def _build_dependency_items(source: DraftGeneratorInput) -> list[str]:
    implementation_plan = (
        source.report_payload.get("implementation_plan")
        or source.result_payload.get("implementation_plan")
        or {}
    )
    dependencies = _copy_dict_list(
        source.report_payload.get("dependencies")
    ) or _copy_dict_list(source.result_payload.get("dependencies"))
    tasks = _copy_dict_list(source.report_payload.get("tasks")) or _copy_dict_list(
        source.result_payload.get("tasks")
    )
    items = (
        [
            f"Implementation touchpoints: {', '.join(str(module).strip() for module in list(implementation_plan.get('target_modules', []) or []) if str(module).strip())}"
        ]
        if implementation_plan.get("target_modules")
        else []
    )
    for dependency in dependencies[:8]:
        src = str(dependency.get("from", "") or "").strip()
        dst = str(dependency.get("to", "") or "").strip()
        dep_type = str(dependency.get("type", "") or "depends_on").strip()
        if src and dst:
            items.append(f"{src} {dep_type} {dst}")
    if not dependencies:
        for task in tasks[:8]:
            title = str(task.get("title", "") or task.get("id", "") or "").strip()
            depends_on = [
                str(item).strip()
                for item in list(task.get("depends_on", []) or [])
                if str(item).strip()
            ]
            if title and depends_on:
                items.append(f"{title} depends on {', '.join(depends_on)}")
    items.append(
        "Document external teams, systems, and approvals that must be available before implementation can start."
    )
    return _unique_strings(items)


def _build_risk_items(source: DraftGeneratorInput) -> list[str]:
    items: list[str] = []
    for risk in source.risk_items[:10]:
        severity = str(risk.get("severity", "") or "medium").strip().lower()
        title = str(risk.get("title", "") or "review-risk").strip()
        detail = str(risk.get("detail", "") or "").strip()
        mitigation = str(risk.get("mitigation", "") or "").strip()
        sentence = f"[{severity}] {title}"
        if detail:
            sentence += f": {detail}"
        if mitigation:
            sentence += f" Mitigation: {mitigation}"
        items.append(sentence)
    return _unique_strings(items)


def _build_open_question_items(source: DraftGeneratorInput) -> list[str]:
    items: list[str] = []
    for question in source.open_questions[:12]:
        text = str(
            question.get("question", "")
            or question.get("detail", "")
            or question.get("title", "")
            or ""
        ).strip()
        reviewers = [
            str(item).strip()
            for item in list(question.get("reviewers", []) or [])
            if str(item).strip()
        ]
        issues = [
            str(item).strip()
            for item in list(question.get("issues", []) or [])
            if str(item).strip()
        ]
        if not text:
            continue
        if reviewers:
            text += f" [owners: {', '.join(reviewers)}]"
        if issues:
            text += f" [context: {'; '.join(issues)}]"
        items.append(text)
    return _unique_strings(items)


def render_prd_v1_markdown(source: DraftGeneratorInput) -> str:
    metadata_lines = [
        "<!--",
        f"run_id: {source.run_id}",
        f"source_artifacts: {', '.join(source.source_artifacts)}",
        f"generated_at: {source.generated_at}",
        f"generator_version: {GENERATOR_VERSION}",
        "-->",
    ]
    sections = [
        "# PRD V1 Draft",
        "",
        f"_Source run_: `{source.run_id}`",
        "",
        "This draft keeps the original requirement document as the baseline and appends sections that should be strengthened based on review findings, open questions, and risk signals.",
        "",
        "## Original Requirement Doc",
        "",
        source.requirement_doc.strip(),
        "",
        "## Scope",
        "",
        _bullet_lines(
            _build_scope_items(source),
            empty_message="No additional scope clarification was derived from the review artifacts.",
        ),
        "",
        "## Acceptance Criteria",
        "",
        _bullet_lines(
            _build_acceptance_items(source),
            empty_message="No acceptance-criteria gaps were detected from the available review artifacts.",
        ),
        "",
        "## Edge Cases",
        "",
        _bullet_lines(
            _build_edge_case_items(source),
            empty_message="No additional edge cases were surfaced by the review outputs.",
        ),
        "",
        "## Dependencies",
        "",
        _bullet_lines(
            _build_dependency_items(source),
            empty_message="No dependency updates were derived from the available review outputs.",
        ),
        "",
        "## Risks",
        "",
        _bullet_lines(
            _build_risk_items(source),
            empty_message="No additional review risks were derived from the available review outputs.",
        ),
        "",
        "## Open Questions",
        "",
        _bullet_lines(
            _build_open_question_items(source),
            empty_message="No unresolved review questions remain in the available artifacts.",
        ),
        "",
        "## Traceability",
        "",
        f"- Source run id: `{source.run_id}`",
        f"- Generator version: `{GENERATOR_VERSION}`",
        f"- Source artifacts: {', '.join(source.source_artifacts)}",
    ]
    return "\n".join(metadata_lines + [""] + sections).strip() + "\n"


def generate_prd_v1_artifact(run_output: dict[str, Any]) -> DraftArtifact:
    run_dir = Path(str(run_output.get("run_dir", "") or "")).resolve()
    if not str(run_output.get("run_id", "") or "").strip():
        raise ValueError("run_id is required for PRD draft generation")
    if not str(run_output.get("run_dir", "") or "").strip():
        raise ValueError("run_dir is required for PRD draft generation")
    run_dir.mkdir(parents=True, exist_ok=True)

    started = perf_counter()
    source = build_draft_generator_input(run_output)
    markdown = render_prd_v1_markdown(source)
    output_path = run_dir / "prd_v1.md"
    output_path.write_text(markdown, encoding="utf-8")
    duration_ms = round((perf_counter() - started) * 1000)
    return DraftArtifact(
        artifact_key="prd_v1",
        path=str(output_path),
        trace={
            "start": source.generated_at,
            "end": _utc_now_iso(),
            "duration_ms": duration_ms,
            "status": "ok",
            "input_chars": len(source.requirement_doc),
            "output_chars": len(markdown),
            "generator_version": GENERATOR_VERSION,
            "non_blocking": True,
            "artifact_path": str(output_path),
            "output_paths": {"prd_v1": str(output_path)},
            "source_artifacts": list(source.source_artifacts),
            "error_message": "",
        },
    )
