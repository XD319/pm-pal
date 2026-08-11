from pm_pal.review.reviewer_agents.base import (
    EvidenceItem,
    ReviewerResult,
    ReviewFinding,
    ToolCall,
)


def test_reviewer_result_to_dict_exposes_evidence_tool_calls_and_status_fields():
    result = ReviewerResult(
        reviewer="engineering",
        findings=(
            ReviewFinding(
                title="Dependency contract missing",
                detail="Export path lacks downstream contract notes.",
                severity="high",
                category="architecture",
                reviewer="engineering",
                evidence=(
                    EvidenceItem(
                        source="risk_catalog",
                        title="Shared dependency risk",
                        snippet="Cross-system changes drift without an explicit contract owner.",
                        ref="RC-303",
                    ),
                ),
            ),
        ),
        evidence=(
            EvidenceItem(
                source="risk_catalog",
                title="Shared dependency risk",
                snippet="Cross-system changes drift without an explicit contract owner.",
                ref="RC-303",
            ),
        ),
        tool_calls=(
            ToolCall(
                tool_name="risk_catalog.search",
                status="completed",
                reviewer="engineering",
                output_summary="hits=1",
                evidence_count=1,
            ),
        ),
        ambiguity_type="missing_implementation_boundaries",
        clarification_question="Which systems and owners are impacted?",
        reviewer_status_detail="Engineering reviewer completed with local evidence.",
    )

    payload = result.to_dict()

    assert payload["evidence"][0]["ref"] == "RC-303"
    assert payload["tool_calls"][0]["tool_name"] == "risk_catalog.search"
    assert payload["ambiguity_type"] == "missing_implementation_boundaries"
    assert payload["clarification_question"] == "Which systems and owners are impacted?"
    assert (
        payload["reviewer_status_detail"]
        == "Engineering reviewer completed with local evidence."
    )
    assert payload["findings"][0]["evidence"][0]["title"] == "Shared dependency risk"
