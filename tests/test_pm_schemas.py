"""Tests for prd_pal.pm.schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prd_pal.pm.schemas import (
    FeedbackItem,
    InsightCluster,
    InsightExtractionOutput,
    OpportunityBrief,
    OpportunityBriefOutput,
    PRDDraft,
    PRDDraftOutput,
    PipelineRunRecord,
    PipelineStage,
    PipelineStatus,
    validate_insight_extraction_output,
    validate_opportunity_brief_output,
    validate_prd_draft_output,
)


class TestFeedbackItem:
    def test_valid_minimal(self) -> None:
        item = FeedbackItem(id="fb-1", text="Login is confusing")
        assert item.id == "fb-1"
        assert item.source_refs == []
        assert item.metadata == {}

    def test_rejects_empty_text(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackItem(id="fb-1", text="")

    def test_ignores_unknown_fields(self) -> None:
        item = FeedbackItem.model_validate(
            {"id": "fb-1", "text": "hello", "extra_llm_field": 123}
        )
        assert item.text == "hello"


class TestInsightCluster:
    def test_valid_with_source_refs(self) -> None:
        insight = InsightCluster(
            id="ins-1",
            title="Onboarding friction",
            feedback_ids=["fb-1", "fb-2"],
            source_refs=["feedback:fb-1", "feedback:fb-2"],
        )
        assert insight.feedback_ids == ["fb-1", "fb-2"]
        assert len(insight.source_refs) == 2

    def test_safe_list_coerces_none(self) -> None:
        insight = InsightCluster.model_validate(
            {"id": "ins-1", "title": "Theme", "feedback_ids": None}
        )
        assert insight.feedback_ids == []


class TestOpportunityBrief:
    def test_valid_full(self) -> None:
        brief = OpportunityBrief(
            id="opp-1",
            title="Simplify login",
            problem="Users abandon login",
            users="New enterprise users",
            value="Higher activation",
            constraints=["No SSO rewrite"],
            open_questions=["Which IdP first?"],
            insight_ids=["ins-1"],
            source_refs=["insight:ins-1"],
            evidence_refs=["feedback:fb-1"],
        )
        assert brief.constraints == ["No SSO rewrite"]
        assert brief.evidence_refs == ["feedback:fb-1"]


class TestPRDDraft:
    def test_valid_markdown_required(self) -> None:
        draft = PRDDraft(
            id="prd-1",
            title="Login redesign",
            markdown="# Goals\n- Reduce drop-off",
            opportunity_id="opp-1",
            goals=["Reduce drop-off"],
            acceptance_criteria=["Drop-off < 10%"],
        )
        assert "Goals" in draft.markdown

    def test_rejects_empty_markdown(self) -> None:
        with pytest.raises(ValidationError):
            PRDDraft(id="prd-1", title="x", markdown="")


class TestPipelineRunRecord:
    def test_defaults(self) -> None:
        record = PipelineRunRecord(id="pipe-1")
        assert record.status == PipelineStatus.pending
        assert record.stage == PipelineStage.capture


class TestInsightExtractionOutput:
    def test_validate_helper(self) -> None:
        out = validate_insight_extraction_output(
            {
                "insights": [
                    {
                        "title": "Auth pain",
                        "summary": "Users struggle with MFA",
                        "theme": "auth",
                        "feedback_ids": ["fb-1"],
                        "evidence_quotes": ["MFA is painful"],
                    }
                ],
                "notes": "clustered 3 items",
            }
        )
        assert isinstance(out, InsightExtractionOutput)
        assert out.insights[0].title == "Auth pain"

    def test_rejects_insight_without_title(self) -> None:
        with pytest.raises(ValidationError):
            InsightExtractionOutput.model_validate(
                {"insights": [{"summary": "missing title"}]}
            )


class TestOpportunityBriefOutput:
    def test_validate_helper(self) -> None:
        out = validate_opportunity_brief_output(
            {
                "title": "Fix onboarding",
                "problem": "Drop-off",
                "users": "New users",
                "value": "Activation",
                "constraints": ["Q3 only"],
                "open_questions": ["Mobile first?"],
            }
        )
        assert isinstance(out, OpportunityBriefOutput)
        assert out.open_questions == ["Mobile first?"]


class TestPRDDraftOutput:
    def test_validate_helper(self) -> None:
        out = validate_prd_draft_output(
            {
                "title": "Onboarding v2",
                "markdown": "# PRD\n\n## Goals\n- Activate faster",
                "goals": ["Activate faster"],
                "in_scope": ["Wizard"],
                "out_of_scope": ["Billing"],
                "acceptance_criteria": ["Complete in < 3 steps"],
                "risks": ["Legacy auth"],
                "success_metrics": ["Activation +10%"],
            }
        )
        assert isinstance(out, PRDDraftOutput)
        assert out.success_metrics == ["Activation +10%"]
