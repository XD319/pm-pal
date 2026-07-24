"""Tests for prd_pal.pm.repository."""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("aiosqlite")

from prd_pal.pm.repository import PmRepository
from prd_pal.pm.schemas import (
    FeedbackItem,
    InsightCluster,
    OpportunityBrief,
    PRDDraft,
    PipelineRunRecord,
    PipelineStage,
    PipelineStatus,
)
from prd_pal.workspace.repository_support import RepositoryErrorCode


@pytest.mark.asyncio
async def test_pm_repository_initializes_wal_mode(tmp_path) -> None:
    db_path = tmp_path / "pm.sqlite3"
    repository = PmRepository(db_path)

    result = await repository.initialize()

    assert result.ok is True
    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert str(journal_mode).lower() == "wal"
    assert {"pm_feedback", "pm_pipeline_run", "pm_artifact"} <= tables


@pytest.mark.asyncio
async def test_feedback_round_trip(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()
    item = FeedbackItem(
        id="fb-1",
        text="Login is confusing",
        source="manual",
        product_hint="checkout",
        source_refs=["paste:1"],
        metadata={"channel": "slack"},
    )

    save_result = await repository.upsert_feedback(item)
    load_result = await repository.get_feedback("fb-1")
    list_result = await repository.list_feedback(product_hint="checkout")

    assert save_result.ok is True
    assert load_result.ok is True
    assert load_result.value is not None
    assert load_result.value.text == "Login is confusing"
    assert load_result.value.metadata["channel"] == "slack"
    assert list_result.ok is True
    assert [item.id for item in list_result.value or []] == ["fb-1"]


@pytest.mark.asyncio
async def test_pipeline_run_round_trip(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()
    record = PipelineRunRecord(
        id="pipe-1",
        status=PipelineStatus.running,
        stage=PipelineStage.cluster,
        product_hint="checkout",
        feedback_ids=["fb-1"],
        insight_ids=["ins-1"],
    )

    save_result = await repository.upsert_pipeline_run(record)
    load_result = await repository.get_pipeline_run("pipe-1")

    assert save_result.ok is True
    assert save_result.value is not None
    assert save_result.value.updated_at
    assert load_result.ok is True
    assert load_result.value is not None
    assert load_result.value.status == PipelineStatus.running
    assert load_result.value.stage == PipelineStage.cluster
    assert load_result.value.feedback_ids == ["fb-1"]


@pytest.mark.asyncio
async def test_artifact_typed_helpers(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()

    insight = InsightCluster(
        id="ins-1",
        title="Onboarding friction",
        feedback_ids=["fb-1"],
        source_refs=["feedback:fb-1"],
    )
    opportunity = OpportunityBrief(
        id="opp-1",
        title="Simplify onboarding",
        insight_ids=["ins-1"],
        source_refs=["insight:ins-1"],
    )
    prd = PRDDraft(
        id="prd-1",
        title="Onboarding v2",
        markdown="# Goals\n- Activate faster",
        opportunity_id="opp-1",
    )

    await repository.upsert_artifact(
        artifact_type="insight", artifact_id="ins-1", payload=insight, pipeline_id="pipe-1"
    )
    await repository.upsert_artifact(
        artifact_type="opportunity",
        artifact_id="opp-1",
        payload=opportunity,
        pipeline_id="pipe-1",
    )
    await repository.upsert_artifact(
        artifact_type="prd", artifact_id="prd-1", payload=prd, pipeline_id="pipe-1"
    )

    insight_result = await repository.get_insight("ins-1")
    opportunity_result = await repository.get_opportunity("opp-1")
    prd_result = await repository.get_prd("prd-1")

    assert insight_result.ok is True
    assert insight_result.value is not None
    assert insight_result.value.title == "Onboarding friction"
    assert opportunity_result.ok is True
    assert opportunity_result.value is not None
    assert opportunity_result.value.insight_ids == ["ins-1"]
    assert prd_result.ok is True
    assert prd_result.value is not None
    assert "Activate" in prd_result.value.markdown


@pytest.mark.asyncio
async def test_missing_feedback_returns_not_found(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()

    result = await repository.get_feedback("missing")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == RepositoryErrorCode.not_found
