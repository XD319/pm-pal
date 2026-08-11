import json

from pm_pal.review.aggregator import aggregate_review_results
from pm_pal.review.reviewer_agents.base import (
    EvidenceItem,
    ReviewerResult,
    ReviewFinding,
    RiskItem,
    ToolCall,
)

_MANUAL_REVIEW_TEXT = "Manual review required"
_MANUAL_REVIEW_MESSAGE = (
    "Manual review required: high-risk findings exist while these reviewers were partial or unavailable: "
    "qa (failed: timeout)"
)


def _reviewer_results() -> list[ReviewerResult]:
    return [
        ReviewerResult(
            reviewer="product",
            findings=(
                ReviewFinding(
                    title="Security review gate required",
                    detail="Sensitive flows need a release gate.",
                    severity="medium",
                    category="security",
                    reviewer="product",
                    evidence=(
                        EvidenceItem(
                            source="risk_catalog",
                            title="Release gate precedent",
                            snippet="Similar launches required a formal release gate.",
                            ref="RC-101",
                        ),
                    ),
                ),
            ),
            open_questions=("Who approves the final release gate?",),
            summary="Product summary.",
            reviewer_status_detail="Product reviewer completed with one escalation note.",
            tool_calls=(
                ToolCall(
                    tool_name="web.search",
                    status="degraded",
                    reviewer="product",
                    output_summary="No evidence returned.",
                    degraded_reason="adapter not configured",
                ),
            ),
        ),
        ReviewerResult(
            reviewer="security",
            findings=(
                ReviewFinding(
                    title="Security review gate required",
                    detail="Sensitive flows need a release gate.",
                    severity="high",
                    category="security",
                    reviewer="security",
                    evidence=(
                        EvidenceItem(
                            source="risk_catalog",
                            title="Approval blocker control",
                            snippet="Sensitive data flows must not ship without approval.",
                            ref="RC-202",
                        ),
                    ),
                ),
            ),
            open_questions=("Who approves the final release gate?",),
            risk_items=(
                RiskItem(
                    title="Security review gate required",
                    detail="Sensitive flows need a release gate.",
                    severity="high",
                    category="security",
                    mitigation="Add a formal approval step.",
                    reviewer="security",
                ),
            ),
            summary="Security summary.",
            reviewer_status_detail="Security reviewer completed with approval-gate evidence.",
            ambiguity_type="missing_security_controls",
            clarification_question="Which controls and approvals are mandatory before release?",
            tool_calls=(
                ToolCall(
                    tool_name="risk_catalog.search",
                    status="completed",
                    reviewer="security",
                    output_summary="hits=2",
                    evidence_count=2,
                ),
            ),
            notes=("Local catalog evidence was attached to the shared finding.",),
        ),
        ReviewerResult(
            reviewer="qa",
            summary="QA reviewer timed out.",
            status="failed",
            error_message="timeout",
            reviewer_status_detail="QA reviewer timed out before evidence collection completed.",
        ),
    ]


def _find_conflict(
    conflicts: tuple[dict[str, object], ...], conflict_type: str
) -> dict[str, object]:
    for conflict in conflicts:
        if conflict["type"] == conflict_type:
            return conflict
    raise AssertionError(f"conflict not found: {conflict_type}")


def test_aggregate_review_results_writes_new_schema_and_legacy_aliases(tmp_path):
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    aggregated = aggregate_review_results(_reviewer_results(), first_output)
    aggregated_again = aggregate_review_results(_reviewer_results(), second_output)

    assert aggregated.reviewer_count == 3
    assert aggregated.partial_review is True
    assert aggregated.reviewers_completed == ("product", "security")
    assert aggregated.reviewers_failed == (
        {"reviewer": "qa", "status": "failed", "reason": "timeout"},
    )
    assert aggregated.meta["review_mode"] == "full"
    assert aggregated.meta["partial_review"] is True
    assert aggregated.meta["reviewers_completed"] == ["product", "security"]
    assert aggregated.meta["reviewers_failed"] == [
        {"reviewer": "qa", "status": "failed", "reason": "timeout"}
    ]
    assert aggregated.meta["manual_review_required"] is True
    assert aggregated.meta["manual_review_message"] == _MANUAL_REVIEW_MESSAGE
    assert aggregated.meta["gating"]["selected_mode"] == "full"
    assert len(aggregated.meta["tool_calls"]) == 2

    assert len(aggregated.findings) == 1
    finding = aggregated.findings[0]
    assert finding["finding_id"] == aggregated_again.findings[0]["finding_id"]
    assert finding["severity"] == "high"
    assert finding["source_reviewer"] == "product"
    assert finding["reviewers"] == ["product", "security"]
    assert finding["description"] == "Sensitive flows need a release gate."
    assert (
        finding["suggested_action"]
        == "Add explicit security, compliance, and release-control expectations to the PRD."
    )
    assert finding["assignee"] == "security"
    assert [item["ref"] for item in finding["evidence"]] == ["RC-101", "RC-202"]

    assert len(aggregated.open_questions) == 1
    assert aggregated.open_questions[0]["reviewers"] == ["product", "security"]
    assert len(aggregated.risk_items) == 1
    assert len(aggregated.conflicts) == 1
    assert len(aggregated.tool_calls) == 2
    assert (
        aggregated.reviewer_summaries[1]["status_detail"]
        == "Security reviewer completed with approval-gate evidence."
    )
    assert (
        aggregated.reviewer_summaries[1]["clarification_question"]
        == "Which controls and approvals are mandatory before release?"
    )
    conflict = aggregated.conflicts[0]
    assert conflict["conflict_id"] == aggregated_again.conflicts[0]["conflict_id"]
    assert conflict["type"] == "severity_mismatch"
    assert conflict["status"] == "severity_mismatch"
    assert conflict["topic"] == "Security review gate required"
    assert conflict["reviewers"] == ["product", "security"]
    assert conflict["conflict_severity"] == "high"
    assert conflict["requires_manual_resolution"] is False
    assert conflict["description"] == (
        "Severity mismatch on 'Security review gate required': findings use medium, high while risks use high."
    )
    assert conflict["resolution"] == {
        "recommendation": "Treat 'Security review gate required' as high severity until reviewers align on one shared label.",
        "reasoning": (
            "Conflicting severity labels were detected for Security review gate required. "
            "Delivery arbitration keeps the higher severity of high so downstream planning does not understate release risk. "
            "Reviewer context: product: Product summary. Product reviewer completed with one escalation note. "
            "qa: QA reviewer timed out. QA reviewer timed out before evidence collection completed. "
            "security: Security summary. Security reviewer completed with approval-gate evidence. "
            "Local catalog evidence was attached to the shared finding."
        ),
        "decided_by": "delivery_reviewer.rules",
        "needs_human": False,
    }

    review_result_payload = json.loads(
        (first_output / "review_result.json").read_text(encoding="utf-8")
    )
    legacy_report_payload = json.loads(
        (first_output / "review_report.json").read_text(encoding="utf-8")
    )
    risk_payload = json.loads(
        (first_output / "risk_items.json").read_text(encoding="utf-8")
    )
    question_payload = json.loads(
        (first_output / "open_questions.json").read_text(encoding="utf-8")
    )
    review_report_text = (first_output / "review_report.md").read_text(encoding="utf-8")
    summary_text = (first_output / "review_summary.md").read_text(encoding="utf-8")

    assert review_result_payload == legacy_report_payload
    assert review_result_payload["partial_review"] is True
    assert review_result_payload["reviewers_completed"] == ["product", "security"]
    assert review_result_payload["reviewers_failed"] == [
        {"reviewer": "qa", "status": "failed", "reason": "timeout"}
    ]
    assert review_result_payload["manual_review_required"] is True
    assert _MANUAL_REVIEW_TEXT in review_result_payload["manual_review_message"]
    assert review_result_payload["meta"]["tool_calls"][0]["tool_name"] == "web.search"
    assert review_result_payload["findings"][0]["finding_id"] == finding["finding_id"]
    assert review_result_payload["findings"][0]["evidence"][0]["ref"] == "RC-101"
    assert (
        review_result_payload["reviewer_summaries"][1]["status_detail"]
        == "Security reviewer completed with approval-gate evidence."
    )
    assert (
        review_result_payload["conflicts"][0]["conflict_id"] == conflict["conflict_id"]
    )
    assert (
        review_result_payload["conflicts"][0]["resolution"]["decided_by"]
        == "delivery_reviewer.rules"
    )
    assert len(risk_payload["risk_items"]) == 1
    assert len(question_payload["open_questions"]) == 1
    assert "# Review Report" in review_report_text
    assert finding["finding_id"] in review_report_text
    assert "## Tool Trace" in review_report_text
    assert "RC-101" in review_report_text
    assert "Resolved conflicts: 1" in review_report_text
    assert "Unresolved conflicts: 0" in review_report_text
    assert (
        "Recommendation: Treat 'Security review gate required' as high severity until reviewers align on one shared label."
        in review_report_text
    )
    assert (
        "Reasoning: Conflicting severity labels were detected for Security review gate required."
        in review_report_text
    )
    assert _MANUAL_REVIEW_TEXT in review_report_text
    assert "# Review Summary" in summary_text
    assert "Security review gate required" in summary_text
    assert "Tool Calls: 2" in summary_text
    assert "Resolved conflicts: 1" in summary_text
    assert _MANUAL_REVIEW_TEXT in summary_text

    artifacts = aggregated.artifacts
    assert artifacts.review_result_json.endswith("review_result.json")
    assert artifacts.review_report_md.endswith("review_report.md")
    assert artifacts.review_report_json.endswith("review_report.json")
    assert artifacts.review_summary_md.endswith("review_summary.md")


def test_detects_scope_inclusion_vs_dependency_blocker_conflict(tmp_path):
    aggregated = aggregate_review_results(
        [
            ReviewerResult(
                reviewer="product",
                summary="The requested scope is included in the current PRD and the scenarios are within scope.",
            ),
            ReviewerResult(
                reviewer="engineering",
                findings=(
                    ReviewFinding(
                        title="Dependency blocker remains",
                        detail="Shared dependencies still block implementation sequencing.",
                        severity="high",
                        category="architecture",
                        reviewer="engineering",
                    ),
                ),
                summary="Engineering found a dependency blocker across shared services.",
            ),
        ],
        tmp_path,
    )

    conflict = _find_conflict(
        aggregated.conflicts, "scope_inclusion_vs_dependency_blocker"
    )
    assert conflict["reviewers"] == ["product", "engineering"]
    assert conflict["conflict_severity"] == "high"
    assert conflict["requires_manual_resolution"] is True
    assert conflict["description"] == (
        "Product indicates the requested scope is already covered, but Engineering flags dependency blockers that can stop or delay implementation."
    )
    assert conflict["resolution"]["recommendation"] == (
        "Escalate to product and engineering leads to confirm scope ownership, dependency sequencing, and release impact."
    )
    assert conflict["resolution"]["needs_human"] is True
    assert (
        "Product scope confidence conflicts with engineering dependency risk."
        in conflict["resolution"]["reasoning"]
    )


def test_detects_acceptance_complete_vs_testability_gap_conflict(tmp_path):
    aggregated = aggregate_review_results(
        [
            ReviewerResult(
                reviewer="product",
                summary="Acceptance criteria are complete and ready for QA sign off.",
            ),
            ReviewerResult(
                reviewer="qa",
                findings=(
                    ReviewFinding(
                        title="Testability gap remains",
                        detail="QA cannot derive pass/fail expectations for the highest-risk edge cases.",
                        severity="medium",
                        category="testability",
                        reviewer="qa",
                    ),
                ),
                summary="QA still sees a testability gap around rollback coverage.",
            ),
        ],
        tmp_path,
    )

    conflict = _find_conflict(
        aggregated.conflicts, "acceptance_complete_vs_testability_gap"
    )
    assert conflict["reviewers"] == ["product", "qa"]
    assert conflict["conflict_severity"] == "high"
    assert conflict["requires_manual_resolution"] is True
    assert conflict["description"] == (
        "Product indicates the acceptance criteria are complete, but QA identifies a testability gap that leaves QA without reliable pass/fail coverage."
    )
    assert conflict["resolution"]["recommendation"] == (
        "Route the conflict back to product and QA to add pass/fail criteria before implementation proceeds."
    )
    assert conflict["resolution"]["needs_human"] is True


def test_detects_release_ok_vs_approval_blocker_conflict(tmp_path):
    aggregated = aggregate_review_results(
        [
            ReviewerResult(
                reviewer="product",
                summary="Release is ok and the feature is ready for release.",
            ),
            ReviewerResult(
                reviewer="security",
                risk_items=(
                    RiskItem(
                        title="Security review gate required",
                        detail="Sensitive data handling cannot release until approval is recorded.",
                        severity="high",
                        category="security",
                        mitigation="Keep the release blocked until the security approval is signed.",
                        reviewer="security",
                    ),
                ),
                summary="Security still treats this as an approval blocker before release.",
            ),
        ],
        tmp_path,
    )

    conflict = _find_conflict(aggregated.conflicts, "release_ok_vs_approval_blocker")
    assert conflict["reviewers"] == ["product", "security"]
    assert conflict["conflict_severity"] == "high"
    assert conflict["requires_manual_resolution"] is False
    assert conflict["description"] == (
        "Product marks the release as ready to proceed, but Security still requires an approval gate before release."
    )
    assert conflict["resolution"]["recommendation"] == (
        "Keep the release blocked until the required approval gate is explicitly satisfied and recorded."
    )
    assert conflict["resolution"]["needs_human"] is False
