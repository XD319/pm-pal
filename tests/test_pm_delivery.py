"""Tests for PM delivery sync and launch review helpers."""

from __future__ import annotations

import pytest

from prd_pal.pm.delivery import (
    DeliveryStatusUpdate,
    DeliverySystem,
    apply_delivery_status_update,
    build_launch_review,
    create_delivery_issue,
)


def test_create_delivery_issue_local() -> None:
    issue = create_delivery_issue(
        title="Implement auth redesign",
        system=DeliverySystem.github,
        prd_id="prd-1",
        opportunity_id="opp-1",
        evidence_refs=["prd:prd-1"],
    )
    assert issue.system == DeliverySystem.github
    assert issue.external_id.startswith("github-")
    assert issue.status == "open"
    assert issue.url.startswith("local://github/")


def test_apply_delivery_status_update() -> None:
    issue = create_delivery_issue(title="Ship search latency fix", system="linear")
    updated = apply_delivery_status_update(
        issue,
        DeliveryStatusUpdate(
            issue_id=issue.id,
            status="in_progress",
            actor="eng-bot",
            detail="picked up",
        ),
    )
    assert updated.status == "in_progress"
    assert updated.metadata["status_history"][0]["to"] == "in_progress"


def test_apply_delivery_status_update_rejects_mismatch() -> None:
    issue = create_delivery_issue(title="Something", system="jira")
    with pytest.raises(ValueError, match="does not match"):
        apply_delivery_status_update(
            issue,
            DeliveryStatusUpdate(issue_id="other", status="done"),
        )


def test_build_launch_review() -> None:
    review = build_launch_review(
        prd_id="prd-1",
        pipeline_id="pipe-1",
        outcome="win",
        metrics={"activation_lift": 0.08},
        learnings=["Onboarding checklist helped"],
        follow_ups=["Expand to mobile"],
        evidence_refs=["metric:activation"],
    )
    assert review.outcome == "win"
    assert review.metrics["activation_lift"] == 0.08
    assert review.follow_ups == ["Expand to mobile"]
