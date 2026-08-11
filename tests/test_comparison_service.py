from __future__ import annotations

import json

from pm_pal.service.comparison_service import (
    compare_runs,
    get_run_stats_summary,
    get_trend_data,
)


def _write_report(tmp_path, run_id: str, payload: dict) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_compare_runs_identifies_added_removed_and_changed_findings(tmp_path):
    _write_report(
        tmp_path,
        "20260309T010203Z",
        {
            "run_id": "20260309T010203Z",
            "created_at": "2026-03-09T01:02:03+00:00",
            "metrics": {"coverage_ratio": 0.80, "risk_score": 5.0},
            "parallel_review_meta": {"duration_ms": 1000},
            "parallel_review": {
                "findings": [
                    {
                        "requirement_id": "REQ-001",
                        "title": "Missing acceptance criteria",
                        "severity": "high",
                        "category": "clarity",
                    },
                    {
                        "requirement_id": "REQ-002",
                        "title": "Test plan incomplete",
                        "severity": "medium",
                        "category": "testability",
                    },
                ],
                "risk_items": [
                    {
                        "id": "R-1",
                        "title": "Rate limit gap",
                        "description": "No rate limit defined",
                        "severity": "high",
                    },
                ],
                "open_questions": [
                    {"question": "Who owns rollout approval?"},
                    {"question": "What is the fallback path?"},
                ],
            },
        },
    )
    _write_report(
        tmp_path,
        "20260310T010203Z",
        {
            "run_id": "20260310T010203Z",
            "created_at": "2026-03-10T01:02:03+00:00",
            "metrics": {"coverage_ratio": 0.90, "risk_score": 3.0},
            "parallel_review_meta": {"duration_ms": 2000},
            "parallel_review": {
                "findings": [
                    {
                        "requirement_id": "REQ-001",
                        "title": "Missing measurable acceptance criteria",
                        "severity": "high",
                        "category": "clarity",
                    },
                    {
                        "requirement_id": "REQ-003",
                        "title": "Audit logging unspecified",
                        "severity": "medium",
                        "category": "compliance",
                    },
                ],
                "risk_items": [
                    {
                        "id": "R-1",
                        "title": "Rate limit gap",
                        "description": "No rate limit defined",
                        "severity": "medium",
                    },
                    {
                        "id": "R-2",
                        "title": "Rollback gap",
                        "description": "Rollback path not documented",
                        "severity": "medium",
                    },
                ],
                "open_questions": [
                    {"question": "Who owns rollout approval?"},
                    {"question": "How should rollback be validated?"},
                ],
            },
        },
    )

    result = compare_runs(
        "20260309T010203Z", "20260310T010203Z", outputs_root=str(tmp_path)
    )

    statuses = {item.requirement_id: item.status for item in result.findings}
    assert statuses["REQ-001"] == "changed"
    assert statuses["REQ-002"] == "removed"
    assert statuses["REQ-003"] == "added"
    assert result.metrics["coverage"].delta == 10.0
    assert result.metrics["risk_score"].delta == -2.0
    assert result.metrics["finding_count"].delta == 0.0
    assert len(result.open_questions.added) == 1
    assert len(result.open_questions.resolved) == 1
    assert result.summary["risks_added"] == 1
    assert result.summary["risks_changed"] == 1


def test_get_trend_data_returns_descending_time_series(tmp_path):
    _write_report(
        tmp_path,
        "20260308T010203Z",
        {
            "metrics": {"coverage_ratio": 0.70, "risk_score": 8.0},
            "parallel_review": {
                "findings": [{"requirement_id": "REQ-001", "severity": "high"}],
            },
        },
    )
    _write_report(
        tmp_path,
        "20260310T010203Z",
        {
            "metrics": {"coverage_ratio": 0.95, "risk_score": 2.0},
            "parallel_review": {
                "findings": [
                    {"requirement_id": "REQ-001", "severity": "high"},
                    {"requirement_id": "REQ-002", "severity": "medium"},
                ],
            },
        },
    )
    _write_report(
        tmp_path,
        "20260309T010203Z",
        {
            "metrics": {"coverage_ratio": 0.80, "risk_score": 5.0},
            "parallel_review": {
                "findings": [{"requirement_id": "REQ-001", "severity": "medium"}],
            },
        },
    )

    trend = get_trend_data(outputs_root=str(tmp_path), limit=3)

    assert [point.run_id for point in trend.points] == [
        "20260310T010203Z",
        "20260309T010203Z",
        "20260308T010203Z",
    ]
    assert trend.points[0].total_findings == 2
    assert trend.points[0].high_severity_count == 1
    assert trend.points[0].coverage_pct == 95.0


def test_trend_and_stats_handle_empty_and_single_run_boundaries(tmp_path):
    empty_trend = get_trend_data(outputs_root=str(tmp_path), limit=20)
    empty_stats = get_run_stats_summary(outputs_root=str(tmp_path))

    assert empty_trend.count == 0
    assert empty_trend.points == []
    assert empty_stats.total_runs == 0
    assert empty_stats.average_findings == 0.0
    assert empty_stats.average_review_duration_ms == 0.0

    _write_report(
        tmp_path,
        "20260311T010203Z",
        {
            "metrics": {"coverage_ratio": 0.88, "risk_score": 4.0},
            "parallel_review_meta": {"duration_ms": 1500},
            "parallel_review": {
                "findings": [
                    {
                        "requirement_id": "REQ-001",
                        "severity": "high",
                        "category": "clarity",
                    },
                    {
                        "requirement_id": "REQ-002",
                        "severity": "medium",
                        "category": "clarity",
                    },
                    {
                        "requirement_id": "REQ-003",
                        "severity": "medium",
                        "category": "testability",
                    },
                ],
            },
        },
    )

    single_trend = get_trend_data(outputs_root=str(tmp_path), limit=20)
    single_stats = get_run_stats_summary(outputs_root=str(tmp_path))

    assert single_trend.count == 1
    assert single_trend.points[0].run_id == "20260311T010203Z"
    assert single_stats.total_runs == 1
    assert single_stats.average_findings == 3.0
    assert single_stats.average_review_duration_ms == 1500.0
    assert single_stats.top_issue_types[0].issue_type == "clarity"
    assert single_stats.top_issue_types[0].count == 2
