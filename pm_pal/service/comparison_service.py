"""Comparison and trend analysis services for review runs."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from pm_pal.service.review_service import (
    _load_json_object,
    _resolve_outputs_root,
    _resolve_run_dir,
)
from pm_pal.service.report_service import RUN_ID_PATTERN


class NumericDelta(BaseModel):
    before: float
    after: float
    delta: float


class FindingDiffItem(BaseModel):
    requirement_id: str
    status: str
    before: list[dict[str, Any]] = Field(default_factory=list)
    after: list[dict[str, Any]] = Field(default_factory=list)


class RiskDiffItem(BaseModel):
    match_key: str
    status: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class OpenQuestionDiff(BaseModel):
    added: list[dict[str, Any]] = Field(default_factory=list)
    resolved: list[dict[str, Any]] = Field(default_factory=list)
    unchanged: list[dict[str, Any]] = Field(default_factory=list)


class ComparisonResult(BaseModel):
    run_a: str
    run_b: str
    findings: list[FindingDiffItem] = Field(default_factory=list)
    risks: list[RiskDiffItem] = Field(default_factory=list)
    metrics: dict[str, NumericDelta]
    open_questions: OpenQuestionDiff
    summary: dict[str, int]


class TrendPoint(BaseModel):
    run_id: str
    timestamp: str
    total_findings: int
    high_severity_count: int
    risk_score: float
    coverage_pct: float


class TrendData(BaseModel):
    count: int
    points: list[TrendPoint] = Field(default_factory=list)


class IssueTypeCount(BaseModel):
    issue_type: str
    count: int


class StatsSummary(BaseModel):
    total_runs: int
    average_findings: float
    top_issue_types: list[IssueTypeCount] = Field(default_factory=list)
    average_review_duration_ms: float


def _load_report(run_id: str, outputs_root: str | Path) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id, outputs_root)
    report = _load_json_object(run_dir / "report.json")
    if not report:
        raise FileNotFoundError(f"report.json not found for run_id={run_id}")
    return report


def _extract_parallel_review(report: dict[str, Any]) -> dict[str, Any]:
    payload = report.get("parallel_review")
    if isinstance(payload, dict):
        return payload
    return report


def _extract_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _extract_parallel_review(report)
    findings = payload.get("findings")
    return (
        [item for item in findings if isinstance(item, dict)]
        if isinstance(findings, list)
        else []
    )


def _extract_risks(report: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _extract_parallel_review(report)
    risks = (
        payload.get("risk_items")
        or report.get("review_risk_items")
        or report.get("risks")
    )
    return (
        [item for item in risks if isinstance(item, dict)]
        if isinstance(risks, list)
        else []
    )


def _extract_open_questions(report: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _extract_parallel_review(report)
    questions = payload.get("open_questions") or report.get("review_open_questions")
    return (
        [item for item in questions if isinstance(item, dict)]
        if isinstance(questions, list)
        else []
    )


def _normalize_text(value: Any) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    return text


def _finding_requirement_id(finding: dict[str, Any], index: int) -> str:
    for key in ("requirement_id", "id", "requirementId"):
        value = str(finding.get(key, "") or "").strip()
        if value:
            return value
    return f"unknown:{index}"


def _group_findings_by_requirement(
    findings: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for index, finding in enumerate(findings):
        grouped.setdefault(_finding_requirement_id(finding, index), []).append(finding)
    return grouped


def _stable_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable_jsonish(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_stable_jsonish(item) for item in value]
    return value


def _groups_equal(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return _stable_jsonish(left) == _stable_jsonish(right)


def _risk_key(risk: dict[str, Any], index: int) -> str:
    for key in ("id", "risk_id"):
        value = str(risk.get(key, "") or "").strip()
        if value:
            return value
    title = _normalize_text(risk.get("title", ""))
    description = _normalize_text(risk.get("description", "") or risk.get("detail", ""))
    if title or description:
        return f"{title}|{description}".strip("|")
    return f"risk:{index}"


def _question_key(question: dict[str, Any], index: int) -> str:
    text = _normalize_text(question.get("question", "") or question.get("title", ""))
    return text or f"question:{index}"


def _metric_number(report: dict[str, Any], metric_name: str) -> float:
    metrics = report.get("metrics")
    parallel_meta = report.get("parallel_review_meta")
    dashed_meta = report.get("parallel-review_meta")

    if metric_name == "finding_count":
        candidates = [
            report.get("finding_count"),
            report.get("meta", {}).get("finding_count")
            if isinstance(report.get("meta"), dict)
            else None,
            parallel_meta.get("finding_count")
            if isinstance(parallel_meta, dict)
            else None,
            dashed_meta.get("finding_count") if isinstance(dashed_meta, dict) else None,
            len(_extract_findings(report)),
        ]
    elif metric_name == "coverage":
        candidates = [
            metrics.get("coverage_pct") if isinstance(metrics, dict) else None,
            metrics.get("coverage_ratio") * 100
            if isinstance(metrics, dict)
            and isinstance(metrics.get("coverage_ratio"), (int, float))
            else None,
            report.get("coverage_pct"),
            report.get("coverage_ratio") * 100
            if isinstance(report.get("coverage_ratio"), (int, float))
            else None,
        ]
    elif metric_name == "risk_score":
        candidates = [
            metrics.get("risk_score") if isinstance(metrics, dict) else None,
            report.get("risk_score"),
            report.get("summary", {}).get("risk_score")
            if isinstance(report.get("summary"), dict)
            else None,
        ]
    else:
        candidates = []

    for candidate in candidates:
        if isinstance(candidate, (int, float)):
            return float(candidate)
    return 0.0


def _extract_timestamp(run_dir: Path, report: dict[str, Any]) -> str:
    for key in ("created_at", "timestamp"):
        value = str(report.get(key, "") or "").strip()
        if value:
            return value

    run_dt: datetime | None = None
    if RUN_ID_PATTERN.fullmatch(run_dir.name):
        run_dt = datetime.strptime(run_dir.name, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    if run_dt is None:
        run_dt = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
    return run_dt.isoformat()


def _extract_duration_ms(report: dict[str, Any]) -> float | None:
    metrics = report.get("metrics")
    parallel_meta = report.get("parallel_review_meta")
    dashed_meta = report.get("parallel-review_meta")
    trace = report.get("trace")
    candidates = [
        metrics.get("total_latency_ms") if isinstance(metrics, dict) else None,
        metrics.get("duration_ms") if isinstance(metrics, dict) else None,
        parallel_meta.get("duration_ms") if isinstance(parallel_meta, dict) else None,
        dashed_meta.get("duration_ms") if isinstance(dashed_meta, dict) else None,
        trace.get("reporter", {}).get("duration_ms")
        if isinstance(trace, dict) and isinstance(trace.get("reporter"), dict)
        else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, (int, float)):
            return float(candidate)
    return None


def _iter_run_dirs(outputs_root: str | Path) -> list[Path]:
    root = _resolve_outputs_root(outputs_root)
    if not root.exists() or not root.is_dir():
        return []

    run_dirs = [
        item
        for item in root.iterdir()
        if item.is_dir() and RUN_ID_PATTERN.fullmatch(item.name)
    ]
    run_dirs.sort(
        key=lambda path: (
            datetime.strptime(path.name, "%Y%m%dT%H%M%SZ")
            .replace(tzinfo=timezone.utc)
            .timestamp(),
            path.name,
        ),
        reverse=True,
    )
    return run_dirs


def _find_best_risk_match(
    source_risk: dict[str, Any],
    source_key: str,
    candidates: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    if source_key in candidates:
        return source_key, candidates[source_key]

    best_key = ""
    best_candidate: dict[str, Any] | None = None
    best_score = 0.0
    source_desc = _normalize_text(
        source_risk.get("description", "")
        or source_risk.get("detail", "")
        or source_risk.get("title", "")
    )
    if not source_desc:
        return None

    for candidate_key, candidate in candidates.items():
        candidate_desc = _normalize_text(
            candidate.get("description", "")
            or candidate.get("detail", "")
            or candidate.get("title", "")
        )
        if not candidate_desc:
            continue
        score = SequenceMatcher(a=source_desc, b=candidate_desc).ratio()
        if score > best_score:
            best_score = score
            best_key = candidate_key
            best_candidate = candidate

    if best_candidate is None or best_score < 0.72:
        return None
    return best_key, best_candidate


def compare_runs(
    run_id_a: str, run_id_b: str, outputs_root: str = "outputs"
) -> ComparisonResult:
    report_a = _load_report(run_id_a, outputs_root)
    report_b = _load_report(run_id_b, outputs_root)

    findings_a = _group_findings_by_requirement(_extract_findings(report_a))
    findings_b = _group_findings_by_requirement(_extract_findings(report_b))
    finding_diffs: list[FindingDiffItem] = []
    for requirement_id in sorted(set(findings_a) | set(findings_b)):
        before = findings_a.get(requirement_id, [])
        after = findings_b.get(requirement_id, [])
        if before and after:
            status = "unchanged" if _groups_equal(before, after) else "changed"
        elif after:
            status = "added"
        else:
            status = "removed"
        finding_diffs.append(
            FindingDiffItem(
                requirement_id=requirement_id, status=status, before=before, after=after
            )
        )

    risks_a = {
        _risk_key(risk, index): risk
        for index, risk in enumerate(_extract_risks(report_a))
    }
    remaining_risks_b = {
        _risk_key(risk, index): risk
        for index, risk in enumerate(_extract_risks(report_b))
    }
    risk_diffs: list[RiskDiffItem] = []
    for key, risk in risks_a.items():
        match = _find_best_risk_match(risk, key, remaining_risks_b)
        if match is None:
            risk_diffs.append(
                RiskDiffItem(match_key=key, status="removed", before=risk, after=None)
            )
            continue
        matched_key, matched_risk = match
        del remaining_risks_b[matched_key]
        status = (
            "unchanged"
            if _stable_jsonish(risk) == _stable_jsonish(matched_risk)
            else "changed"
        )
        risk_diffs.append(
            RiskDiffItem(
                match_key=matched_key or key,
                status=status,
                before=risk,
                after=matched_risk,
            )
        )
    for key, risk in remaining_risks_b.items():
        risk_diffs.append(
            RiskDiffItem(match_key=key, status="added", before=None, after=risk)
        )

    questions_a = {
        _question_key(question, index): question
        for index, question in enumerate(_extract_open_questions(report_a))
    }
    questions_b = {
        _question_key(question, index): question
        for index, question in enumerate(_extract_open_questions(report_b))
    }
    open_question_diff = OpenQuestionDiff(
        added=[questions_b[key] for key in sorted(set(questions_b) - set(questions_a))],
        resolved=[
            questions_a[key] for key in sorted(set(questions_a) - set(questions_b))
        ],
        unchanged=[
            questions_b[key] for key in sorted(set(questions_a) & set(questions_b))
        ],
    )

    metrics = {
        name: NumericDelta(
            before=_metric_number(report_a, name),
            after=_metric_number(report_b, name),
            delta=_metric_number(report_b, name) - _metric_number(report_a, name),
        )
        for name in ("coverage", "risk_score", "finding_count")
    }

    summary = {
        "findings_added": sum(1 for item in finding_diffs if item.status == "added"),
        "findings_removed": sum(
            1 for item in finding_diffs if item.status == "removed"
        ),
        "findings_changed": sum(
            1 for item in finding_diffs if item.status == "changed"
        ),
        "findings_unchanged": sum(
            1 for item in finding_diffs if item.status == "unchanged"
        ),
        "risks_added": sum(1 for item in risk_diffs if item.status == "added"),
        "risks_removed": sum(1 for item in risk_diffs if item.status == "removed"),
        "risks_changed": sum(1 for item in risk_diffs if item.status == "changed"),
        "risks_unchanged": sum(1 for item in risk_diffs if item.status == "unchanged"),
        "open_questions_added": len(open_question_diff.added),
        "open_questions_resolved": len(open_question_diff.resolved),
    }

    return ComparisonResult(
        run_a=run_id_a,
        run_b=run_id_b,
        findings=finding_diffs,
        risks=sorted(risk_diffs, key=lambda item: item.match_key),
        metrics=metrics,
        open_questions=open_question_diff,
        summary=summary,
    )


def get_trend_data(outputs_root: str = "outputs", limit: int = 20) -> TrendData:
    if limit <= 0:
        return TrendData(count=0, points=[])

    points: list[TrendPoint] = []
    for run_dir in _iter_run_dirs(outputs_root)[:limit]:
        report = _load_json_object(run_dir / "report.json")
        if not report:
            continue
        findings = _extract_findings(report)
        points.append(
            TrendPoint(
                run_id=run_dir.name,
                timestamp=_extract_timestamp(run_dir, report),
                total_findings=len(findings),
                high_severity_count=sum(
                    1
                    for item in findings
                    if _normalize_text(item.get("severity", "")) == "high"
                ),
                risk_score=_metric_number(report, "risk_score"),
                coverage_pct=_metric_number(report, "coverage"),
            )
        )

    return TrendData(count=len(points), points=points)


def get_run_stats_summary(outputs_root: str = "outputs") -> StatsSummary:
    run_dirs = _iter_run_dirs(outputs_root)
    if not run_dirs:
        return StatsSummary(
            total_runs=0,
            average_findings=0.0,
            top_issue_types=[],
            average_review_duration_ms=0.0,
        )

    finding_counts: list[int] = []
    duration_values: list[float] = []
    issue_counter: Counter[str] = Counter()

    for run_dir in run_dirs:
        report = _load_json_object(run_dir / "report.json")
        if not report:
            continue
        findings = _extract_findings(report)
        finding_counts.append(len(findings))
        duration_ms = _extract_duration_ms(report)
        if duration_ms is not None:
            duration_values.append(duration_ms)
        for finding in findings:
            issue_type = str(
                finding.get("category")
                or finding.get("type")
                or finding.get("title")
                or "unknown"
            ).strip()
            if issue_type:
                issue_counter[issue_type] += 1

    total_runs = len(run_dirs)
    average_findings = (
        (sum(finding_counts) / len(finding_counts)) if finding_counts else 0.0
    )
    average_duration_ms = (
        (sum(duration_values) / len(duration_values)) if duration_values else 0.0
    )
    top_issue_types = [
        IssueTypeCount(issue_type=issue_type, count=count)
        for issue_type, count in issue_counter.most_common(5)
    ]

    return StatsSummary(
        total_runs=total_runs,
        average_findings=average_findings,
        top_issue_types=top_issue_types,
        average_review_duration_ms=average_duration_ms,
    )
