"""Tests for PM long-lived context and traceability."""

from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from prd_pal.pm.models import (
    Decision,
    DecisionStatus,
    ProductContext,
    RoadmapHorizon,
    RoadmapItem,
)
from prd_pal.pm.repository import PmRepository
from prd_pal.pm.schemas import PipelineRunRecord, PipelineStage, PipelineStatus
from prd_pal.pm.traceability import build_pipeline_trace_links, get_pm_traceability


@pytest.mark.asyncio
async def test_product_decision_roadmap_round_trip(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()

    product = ProductContext(
        id="prod-1",
        name="Commerce",
        target_users="Shoppers",
        business_goals=["Increase activation"],
        constraints=["No checkout rewrite"],
    )
    decision = Decision(
        id="dec-1",
        product_id="prod-1",
        title="Prioritize auth redesign",
        status=DecisionStatus.approved,
        evidence_refs=["opportunity:opp-1"],
    )
    roadmap_item = RoadmapItem(
        id="rm-1",
        product_id="prod-1",
        title="Auth redesign",
        horizon=RoadmapHorizon.now,
        opportunity_id="opp-1",
        score=8.5,
    )

    assert (await repository.upsert_product_context(product)).ok
    assert (await repository.upsert_decision(decision)).ok
    assert (await repository.upsert_roadmap_item(roadmap_item)).ok

    loaded_product = await repository.get_product_context("prod-1")
    roadmap = await repository.list_roadmap_items(product_id="prod-1")

    assert loaded_product.ok is True
    assert loaded_product.value is not None
    assert loaded_product.value.business_goals == ["Increase activation"]
    assert roadmap.ok is True
    assert roadmap.value is not None
    assert roadmap.value[0].horizon == RoadmapHorizon.now


@pytest.mark.asyncio
async def test_pipeline_traceability_links(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()
    record = PipelineRunRecord(
        id="pipe-1",
        status=PipelineStatus.completed,
        stage=PipelineStage.complete,
        feedback_ids=["fb-1", "fb-2"],
        insight_ids=["ins-1"],
        opportunity_id="opp-1",
        prd_id="prd-1",
        review_run_id="run-1",
    )
    await repository.upsert_pipeline_run(record)

    links = build_pipeline_trace_links(record)
    assert any(
        link.source_type == "feedback" and link.target_type == "insight" for link in links
    )
    assert any(
        link.source_type == "prd" and link.target_type == "review_run" for link in links
    )

    payload = await get_pm_traceability(repository, "pipe-1")
    assert payload["root_type"] == "pipeline"
    assert len(payload["links"]) >= 4
