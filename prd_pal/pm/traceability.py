"""Traceability helpers for PM Agent evidence chains."""

from __future__ import annotations

import uuid
from typing import Any

from .models import TraceLink
from .repository import PmRepository
from .schemas import PipelineRunRecord


def _new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:12]}"


def build_pipeline_trace_links(record: PipelineRunRecord) -> list[TraceLink]:
    """Build directed links for one completed (or in-progress) pipeline run."""

    links: list[TraceLink] = []

    def add(source_type: str, source_id: str, target_type: str, target_id: str, relation: str) -> None:
        if not source_id or not target_id:
            return
        links.append(
            TraceLink(
                id=_new_trace_id(),
                source_type=source_type,
                source_id=source_id,
                target_type=target_type,
                target_id=target_id,
                relation=relation,
            )
        )

    for feedback_id in record.feedback_ids:
        for insight_id in record.insight_ids:
            add("feedback", feedback_id, "insight", insight_id, "supports")
    for insight_id in record.insight_ids:
        add("insight", insight_id, "opportunity", record.opportunity_id, "promoted_to")
    add("opportunity", record.opportunity_id, "prd", record.prd_id, "drafted_as")
    add("prd", record.prd_id, "review_run", record.review_run_id, "reviewed_by")
    add("pipeline", record.id, "prd", record.prd_id, "produced")
    return links


async def persist_pipeline_traceability(
    repository: PmRepository, record: PipelineRunRecord
) -> list[TraceLink]:
    links = build_pipeline_trace_links(record)
    for link in links:
        await repository.upsert_trace_link(link)
    return links


async def get_pm_traceability(
    repository: PmRepository, root_id: str
) -> dict[str, Any]:
    """Resolve a traceability graph rooted at pipeline/opportunity/prd/feedback id."""

    root = str(root_id or "").strip()
    if not root:
        raise ValueError("root_id is required")

    pipeline_result = await repository.get_pipeline_run(root)
    if pipeline_result.ok and pipeline_result.value is not None:
        record = pipeline_result.value
        links = await persist_pipeline_traceability(repository, record)
        return {
            "root_id": root,
            "root_type": "pipeline",
            "pipeline": record.model_dump(mode="python"),
            "links": [link.model_dump(mode="python") for link in links],
        }

    links_result = await repository.list_trace_links(root_id=root)
    links = links_result.value if links_result.ok and links_result.value else []
    return {
        "root_id": root,
        "root_type": "object",
        "links": [link.model_dump(mode="python") for link in links],
    }
