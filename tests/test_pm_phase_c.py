"""Tests for PM scoring, roadmap, and Feishu helpers."""

from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from prd_pal.pm.feishu_pm import (
    build_feedback_card,
    build_quality_gate_notice,
    capture_feishu_feedback,
)
from prd_pal.pm.repository import PmRepository
from prd_pal.pm.roadmap import build_roadmap_item_from_opportunity, diff_roadmap_items
from prd_pal.pm.scoring import assign_horizon, score_ice, score_rice
from prd_pal.pm.models import RoadmapHorizon, RoadmapItem


def test_score_rice_and_horizon() -> None:
    score = score_rice(reach=100, impact=2, confidence=0.8, effort=5)
    assert score.method == "rice"
    assert score.score == 32.0
    assert assign_horizon(score.score) == "now"


def test_score_ice() -> None:
    score = score_ice(impact=3, confidence=2, ease=2)
    assert score.score == 12.0


def test_roadmap_diff_detects_moves() -> None:
    old_items = [
        RoadmapItem(id="rm-1", title="Auth", horizon=RoadmapHorizon.next, score=4.0)
    ]
    new_items = [
        RoadmapItem(id="rm-1", title="Auth", horizon=RoadmapHorizon.now, score=8.0),
        RoadmapItem(id="rm-2", title="Search", horizon=RoadmapHorizon.later, score=1.0),
    ]
    diff = diff_roadmap_items(old_items, new_items)
    assert diff["added_count"] == 1
    assert diff["moved_count"] == 1
    assert diff["moved"][0]["to_horizon"] == "now"


def test_build_roadmap_item_from_rice() -> None:
    item, priority = build_roadmap_item_from_opportunity(
        opportunity_id="opp-1",
        title="Simplify auth",
        rice={"reach": 50, "impact": 2, "confidence": 0.7, "effort": 4},
    )
    assert item.opportunity_id == "opp-1"
    assert item.horizon in {RoadmapHorizon.now, RoadmapHorizon.next, RoadmapHorizon.later}
    assert priority.method == "rice"


@pytest.mark.asyncio
async def test_capture_feishu_feedback(tmp_path) -> None:
    repository = PmRepository(tmp_path / "pm.sqlite3")
    await repository.initialize()
    result = await capture_feishu_feedback(
        texts=["Login is confusing"],
        product_hint="commerce",
        open_id="ou_1",
        tenant_key="tenant_a",
        repository=repository,
    )
    assert result["count"] == 1
    assert "card" in result
    card = build_feedback_card(product_hint="commerce", feedback_count=1)
    assert card["header"]["title"] == "PM Agent feedback inbox"
    notice = build_quality_gate_notice(
        pipeline_id="pipe-1",
        prd_id="prd-1",
        review_run_id="run-1",
        findings_count=2,
    )
    assert notice["msg_type"] == "interactive"
