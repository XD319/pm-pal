import asyncio
import json

import pytest

from pm_pal.review import parallel_review_manager as manager
from pm_pal.review.parallel_review_manager import run_parallel_review_async
from pm_pal.review.reviewer_agents.base import (
    EvidenceItem,
    ReviewerResult,
    RiskItem,
    ToolCall,
)

_MANUAL_REVIEW_TEXT = "Manual review required"


@pytest.mark.asyncio
async def test_parallel_review_manager_selects_reviewers_dynamically_and_writes_outputs(
    tmp_path,
):
    prd_text = """
# Recruiter profile export

Allow admin users to export recruiter profiles to a CSV file. This touches FE, BE, QA, and Security.

## Modules
- `admin-portal`
- `profile-service`
- `audit-service`

## Scenarios
- Admin exports a filtered list of recruiter profiles.
- The backend retries an external audit webhook when delivery fails.

## Acceptance Criteria
- Export completes within the admin portal flow.
- Each export writes an audit record.
- Rollback guidance exists for malformed exports.
"""

    result = await run_parallel_review_async(prd_text, tmp_path)

    assert result.normalized_requirement["summary"]
    assert set(result.reviewer_inputs) == {"product", "engineering", "qa", "security"}
    assert len(result.reviewer_results) == 4

    aggregated = result.aggregated
    artifacts = aggregated["artifacts"]
    assert (tmp_path / "review_result.json").exists()
    assert (tmp_path / "review_report.md").exists()
    assert (tmp_path / "review_report.json").exists()
    assert artifacts["review_result_json"].endswith("review_result.json")
    assert aggregated["review_mode"] == "full"
    assert list(aggregated["reviewers_used"]) == [
        "product",
        "engineering",
        "qa",
        "security",
    ]
    assert list(aggregated["reviewers_skipped"]) == []
    assert aggregated["gating"]["selected_mode"] == "full"
    assert aggregated["summary"]["overall_risk"] in {"low", "medium", "high"}
    assert isinstance(aggregated["summary"]["in_scope"], list)
    assert isinstance(aggregated["summary"]["out_of_scope"], list)
    assert isinstance(aggregated["tool_calls"], list)
    assert isinstance(aggregated["reviewer_summaries"], list)

    review_result = json.loads(
        (tmp_path / "review_result.json").read_text(encoding="utf-8")
    )
    assert review_result["review_mode"] == "full"
    assert review_result["meta"]["reviewers_used"] == [
        "product",
        "engineering",
        "qa",
        "security",
    ]
    assert "gating" in review_result["meta"]
    assert "tool_calls" in review_result["meta"]
    assert "reviewer_summaries" in review_result


@pytest.mark.asyncio
async def test_parallel_review_manager_skips_irrelevant_reviewers_and_handles_partial_failures(
    monkeypatch, tmp_path
):
    async def fake_product(_requirement, config=None):
        return ReviewerResult(
            reviewer="product",
            summary="Product completed.",
            reviewer_status_detail="Product reviewer completed cleanly.",
        )

    async def slow_engineering(_requirement, config=None):
        await asyncio.sleep(0.05)
        return ReviewerResult(reviewer="engineering", summary="Engineering completed.")

    async def fake_qa(_requirement, config=None):
        raise RuntimeError("qa boom")

    async def fake_security(_requirement, config=None):
        return ReviewerResult(
            reviewer="security",
            findings=(),
            risk_items=(
                RiskItem(
                    title="Security gate required",
                    detail="Sensitive export requires explicit release approval.",
                    severity="high",
                    category="security",
                    mitigation="Add manual release approval.",
                    reviewer="security",
                ),
            ),
            evidence=(
                EvidenceItem(
                    source="risk_catalog",
                    title="Sensitive export release gate",
                    snippet="Sensitive export flows require approval before release.",
                    ref="RC-900",
                ),
            ),
            tool_calls=(
                ToolCall(
                    tool_name="risk_catalog.search",
                    status="completed",
                    reviewer="security",
                    output_summary="hits=1",
                    evidence_count=1,
                ),
            ),
            summary="Security completed.",
            reviewer_status_detail="Security reviewer completed with evidence.",
        )

    monkeypatch.setattr(
        manager,
        "_REVIEWER_FUNCTIONS",
        {
            "product": fake_product,
            "engineering": slow_engineering,
            "qa": fake_qa,
            "security": fake_security,
        },
    )

    result = await run_parallel_review_async(
        "Sensitive export with payment audit webhook and acceptance criteria",
        tmp_path,
        reviewer_timeouts={
            "product": 1.0,
            "engineering": 0.01,
            "qa": 1.0,
            "security": 1.0,
        },
    )

    reviewer_results = {item["reviewer"]: item for item in result.reviewer_results}
    assert reviewer_results["product"]["status"] == "completed"
    assert reviewer_results["security"]["status"] == "completed"
    assert reviewer_results["engineering"]["status"] == "timeout"
    assert reviewer_results["qa"]["status"] == "error"
    assert reviewer_results["engineering"]["reviewer_status_detail"].startswith(
        "Engineering reviewer ended with status"
    )

    aggregated = result.aggregated
    assert aggregated["partial_review"] is True
    assert aggregated["meta"]["partial_review"] is True
    assert aggregated["meta"]["manual_review_required"] is True
    assert _MANUAL_REVIEW_TEXT in aggregated["meta"]["manual_review_message"]
    assert list(aggregated["reviewers_used"]) == [
        "product",
        "engineering",
        "qa",
        "security",
    ]
    assert aggregated["tool_calls"][0]["tool_name"] == "risk_catalog.search"
    assert aggregated["findings"] == []
    assert aggregated["risk_items"][0]["title"] == "Security gate required"
    assert (
        aggregated["reviewer_summaries"][0]["status_detail"]
        == "Product reviewer completed cleanly."
    )

    summary_text = (tmp_path / "review_summary.md").read_text(encoding="utf-8")
    report_text = (tmp_path / "review_report.md").read_text(encoding="utf-8")
    assert _MANUAL_REVIEW_TEXT in summary_text
    assert _MANUAL_REVIEW_TEXT in report_text
    assert "Tool Trace" in report_text


@pytest.mark.asyncio
async def test_parallel_review_manager_skips_security_when_scope_is_not_sensitive(
    tmp_path,
):
    prd_text = """
# Login copy update

## Scenarios
- Update the login helper text.

## Acceptance Criteria
- New text appears on the page.
"""

    result = await run_parallel_review_async(prd_text, tmp_path)

    assert "security" not in result.reviewer_inputs
    skipped = result.aggregated["reviewers_skipped"]
    assert any(item["reviewer"] == "security" for item in skipped)
