"""Roadmap helpers for Now/Next/Later planning."""

from __future__ import annotations

import uuid
from typing import Any

from .models import RoadmapHorizon, RoadmapItem
from .scoring import PriorityScore, assign_horizon, score_ice, score_rice


def _new_roadmap_id() -> str:
    return f"rm-{uuid.uuid4().hex[:12]}"


def build_roadmap_item_from_opportunity(
    *,
    opportunity_id: str,
    title: str,
    product_id: str = "",
    prd_id: str = "",
    summary: str = "",
    rice: dict[str, float] | None = None,
    ice: dict[str, float] | None = None,
    source_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> tuple[RoadmapItem, PriorityScore]:
    """Create a roadmap item and attach a RICE or ICE score."""

    if rice:
        priority = score_rice(**rice)
    elif ice:
        priority = score_ice(**ice)
    else:
        priority = PriorityScore(method="manual", score=0.0, details={})

    horizon = RoadmapHorizon(assign_horizon(priority.score))
    item = RoadmapItem(
        id=_new_roadmap_id(),
        product_id=product_id,
        title=title,
        horizon=horizon,
        opportunity_id=opportunity_id,
        prd_id=prd_id,
        score=priority.score,
        summary=summary,
        source_refs=list(source_refs or [f"opportunity:{opportunity_id}"]),
        evidence_refs=list(evidence_refs or []),
        metadata={"scoring_method": priority.method, "scoring_details": priority.details},
    )
    return item, priority


def diff_roadmap_items(
    old_items: list[RoadmapItem], new_items: list[RoadmapItem]
) -> dict[str, Any]:
    """Produce a readable roadmap diff grouped by horizon changes and additions."""

    old_by_id = {item.id: item for item in old_items}
    new_by_id = {item.id: item for item in new_items}
    added = [item.model_dump(mode="python") for item_id, item in new_by_id.items() if item_id not in old_by_id]
    removed = [
        item.model_dump(mode="python") for item_id, item in old_by_id.items() if item_id not in new_by_id
    ]
    moved: list[dict[str, Any]] = []
    for item_id, new_item in new_by_id.items():
        old_item = old_by_id.get(item_id)
        if old_item is None:
            continue
        if old_item.horizon != new_item.horizon or old_item.score != new_item.score:
            moved.append(
                {
                    "id": item_id,
                    "title": new_item.title,
                    "from_horizon": str(old_item.horizon),
                    "to_horizon": str(new_item.horizon),
                    "from_score": old_item.score,
                    "to_score": new_item.score,
                }
            )
    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "moved_count": len(moved),
        "added": added,
        "removed": removed,
        "moved": moved,
    }
