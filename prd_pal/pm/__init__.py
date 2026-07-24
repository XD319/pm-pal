"""PM Agent domain: feedback capture through PRD quality gate."""

from __future__ import annotations

from pathlib import Path

from .models import (
    Decision,
    DecisionStatus,
    ProductContext,
    RoadmapHorizon,
    RoadmapItem,
    TraceLink,
)
from .repository import PmRepository
from .schemas import (
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

DEFAULT_PM_DB_PATH = Path("data") / "pm.sqlite3"

__all__ = [
    "DEFAULT_PM_DB_PATH",
    "Decision",
    "DecisionStatus",
    "FeedbackItem",
    "InsightCluster",
    "InsightExtractionOutput",
    "OpportunityBrief",
    "OpportunityBriefOutput",
    "PRDDraft",
    "PRDDraftOutput",
    "PipelineRunRecord",
    "PipelineStage",
    "PipelineStatus",
    "PmRepository",
    "ProductContext",
    "RoadmapHorizon",
    "RoadmapItem",
    "TraceLink",
    "validate_insight_extraction_output",
    "validate_opportunity_brief_output",
    "validate_prd_draft_output",
]
