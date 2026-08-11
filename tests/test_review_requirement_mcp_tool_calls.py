from __future__ import annotations

import json

import pytest

from pm_pal.service import review_service
from pm_pal.service.review_service import ReviewResultSummary


@pytest.mark.asyncio
async def test_review_requirement_for_mcp_async_exposes_tool_calls_and_reviewer_insights(
    tmp_path, monkeypatch
):
    run_id = "20260311T010203Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    report_json_path = run_dir / "report.json"
    run_trace_path = run_dir / "run_trace.json"
    review_result_json_path = run_dir / "review_result.json"

    report_payload = {
        "mode": "full",
        "review_mode": "full",
        "gating": {
            "selected_mode": "full",
            "reasons": [
                "cross_system_hits=2 indicates external or multi-system coordination"
            ],
            "skipped": False,
        },
        "reviewers_used": ["product", "engineering", "qa"],
        "reviewers_skipped": [
            {
                "reviewer": "security",
                "reason": "no security-sensitive scope was detected",
            }
        ],
        "summary": {
            "overall_risk": "medium",
            "in_scope": ["admin export"],
            "out_of_scope": [
                "security reviewer skipped: no security-sensitive scope was detected"
            ],
        },
        "parallel_review_meta": {
            "selected_mode": "full",
            "review_mode": "full",
            "gating": {"selected_mode": "full", "reasons": ["x"], "skipped": False},
            "reviewers_used": ["product", "engineering", "qa"],
            "reviewers_skipped": [
                {
                    "reviewer": "security",
                    "reason": "no security-sensitive scope was detected",
                }
            ],
            "artifact_paths": {"review_result_json": str(review_result_json_path)},
            "tool_calls": [
                {
                    "tool_name": "risk_catalog.search",
                    "status": "completed",
                    "reviewer": "engineering",
                    "output_summary": "hits=2",
                    "evidence_count": 2,
                }
            ],
            "reviewer_insights": [
                {
                    "reviewer": "engineering",
                    "status": "completed",
                    "summary": "Engineering summary.",
                    "status_detail": "Engineering reviewer completed with evidence.",
                    "ambiguity_type": "missing_implementation_boundaries",
                    "clarification_question": "Which service owns the export contract?",
                    "notes": ["Local evidence attached."],
                }
            ],
        },
        "parallel_review": {
            "findings": [
                {
                    "finding_id": "finding-123",
                    "title": "Dependency contract missing",
                    "detail": "Export path lacks explicit downstream contract notes.",
                    "severity": "high",
                    "category": "architecture",
                    "reviewers": ["engineering"],
                    "evidence": [
                        {
                            "source": "risk_catalog",
                            "title": "Shared dependency risk",
                            "snippet": "Cross-system changes drift without an explicit contract owner.",
                            "ref": "RC-303",
                        }
                    ],
                }
            ],
            "open_questions": [
                {
                    "question": "Who owns the export contract before release?",
                    "reviewers": ["product"],
                }
            ],
            "risk_items": [
                {
                    "title": "Contract drift",
                    "detail": "Downstream consumer may break on schema changes.",
                    "severity": "medium",
                    "category": "integration",
                    "mitigation": "Version the export contract.",
                }
            ],
            "conflicts": [],
            "tool_calls": [
                {
                    "tool_name": "risk_catalog.search",
                    "status": "completed",
                    "reviewer": "engineering",
                    "output_summary": "hits=2",
                    "evidence_count": 2,
                }
            ],
            "reviewer_summaries": [
                {
                    "reviewer": "engineering",
                    "status": "completed",
                    "summary": "Engineering summary.",
                    "status_detail": "Engineering reviewer completed with evidence.",
                    "ambiguity_type": "missing_implementation_boundaries",
                    "clarification_question": "Which service owns the export contract?",
                    "notes": ["Local evidence attached."],
                }
            ],
            "artifacts": {"review_result_json": str(review_result_json_path)},
        },
    }
    report_json_path.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    run_trace_path.write_text("{}", encoding="utf-8")
    review_result_json_path.write_text(
        json.dumps({"ok": True}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    fixed = ReviewResultSummary(
        run_id=run_id,
        report_md_path=str(run_dir / "report.md"),
        report_json_path=str(report_json_path),
        high_risk_ratio=0.5,
        coverage_ratio=0.8,
        revision_round=1,
        status="completed",
        run_trace_path=str(run_trace_path),
    )

    async def fake_review_prd_text_async(
        prd_text: str | None = None,
        *,
        prd_path: str | None = None,
        source: str | None = None,
        run_id: str | None = None,
        config_overrides: dict[str, object] | None = None,
    ) -> ReviewResultSummary:
        return fixed

    monkeypatch.setattr(
        review_service, "review_prd_text_async", fake_review_prd_text_async
    )

    result = await review_service.review_requirement_for_mcp_async(
        prd_text="Complex export PRD",
        prd_path=None,
        source=None,
        options={"outputs_root": str(tmp_path), "mode": "full"},
    )

    assert result["tool_calls"][0]["tool_name"] == "risk_catalog.search"
    assert (
        result["reviewer_insights"][0]["status_detail"]
        == "Engineering reviewer completed with evidence."
    )
    assert result["findings"][0]["evidence"][0]["ref"] == "RC-303"
    assert result["meta"]["tool_calls"][0]["reviewer"] == "engineering"
    assert (
        result["meta"]["reviewer_insights"][0]["clarification_question"]
        == "Which service owns the export contract?"
    )
