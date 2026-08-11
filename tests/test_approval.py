from __future__ import annotations

import pytest

from pm_pal.packs.approval import (
    InvalidTransitionError,
    approve_bundle,
    block_by_risk,
    build_approval_record,
    request_more_info,
    reset_to_draft,
)
from pm_pal.packs.delivery_bundle import (
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)


def _bundle(status: BundleStatus = BundleStatus.draft) -> DeliveryBundle:
    return DeliveryBundle(
        bundle_id="bundle-20260307T120000Z",
        created_at="2026-03-07T12:00:00+00:00",
        status=status,
        source_run_id="20260307T120000Z",
        artifacts=DeliveryArtifacts(
            prd_review_report=ArtifactRef(
                artifact_type="prd_review_report", path="prd_review_report.md"
            ),
            open_questions=ArtifactRef(
                artifact_type="open_questions", path="open_questions.md"
            ),
            scope_boundary=ArtifactRef(
                artifact_type="scope_boundary", path="scope_boundary.md"
            ),
            tech_design_draft=ArtifactRef(
                artifact_type="tech_design_draft", path="tech_design_draft.md"
            ),
            test_checklist=ArtifactRef(
                artifact_type="test_checklist", path="test_checklist.md"
            ),
            implementation_pack=ArtifactRef(
                artifact_type="implementation_pack", path="implementation_pack.json"
            ),
            test_pack=ArtifactRef(artifact_type="test_pack", path="test_pack.json"),
            execution_pack=ArtifactRef(
                artifact_type="execution_pack", path="execution_pack.json"
            ),
        ),
    )


def test_all_valid_transitions_work():
    bundle = request_more_info(_bundle(), "alice", "Need more details")
    assert bundle.status == "need_more_info"

    bundle = reset_to_draft(bundle, "alice", "Clarified")
    assert bundle.status == "draft"

    bundle = block_by_risk(bundle, "bob", "Security risk")
    assert bundle.status == "blocked_by_risk"

    bundle = reset_to_draft(bundle, "bob", "Risk mitigated")
    assert bundle.status == "draft"

    bundle = approve_bundle(bundle, "carol", "Approved")
    assert bundle.status == "approved"


def test_invalid_transition_raises():
    with pytest.raises(InvalidTransitionError):
        approve_bundle(_bundle(BundleStatus.approved), "alice", "again")


def test_approval_event_records_expected_fields():
    bundle = approve_bundle(_bundle(), "alice", "ship it")

    event = bundle.approval_history[0]
    assert event.from_status == "draft"
    assert event.to_status == "approved"
    assert event.reviewer == "alice"
    assert event.comment == "ship it"
    assert event.timestamp


def test_build_approval_record_maps_workspace_status_and_action():
    bundle = request_more_info(_bundle(), "alice", "Need clarification")

    record = build_approval_record(
        bundle, bundle.approval_history[-1], action="need_more_info"
    )

    assert record.record_id == bundle.approval_history[-1].event_id
    assert record.run_id == bundle.source_run_id
    assert record.bundle_id == bundle.bundle_id
    assert record.action == "need_more_info"
    assert record.from_bundle_status == "draft"
    assert record.to_bundle_status == "need_more_info"
    assert record.workspace_status == "need_more_info"


def test_approved_is_terminal():
    approved = approve_bundle(_bundle(), "alice", "done")

    with pytest.raises(InvalidTransitionError):
        reset_to_draft(approved, "alice", "reopen")


def test_history_length_tracks_multiple_transitions():
    bundle = request_more_info(_bundle(), "alice", "Need clarification")
    bundle = reset_to_draft(bundle, "alice", "Clarified")
    bundle = block_by_risk(bundle, "bob", "Risk")

    assert len(bundle.approval_history) == 3
