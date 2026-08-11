from __future__ import annotations

from pm_pal.packs.delivery_bundle import (
    ApprovalEvent,
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)


def test_delivery_bundle_schema_instantiates_and_serializes():
    bundle = DeliveryBundle(
        bundle_id="bundle-20260307T120000Z",
        created_at="2026-03-07T12:00:00+00:00",
        status=BundleStatus.draft,
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

    payload = bundle.model_dump(mode="python")
    assert payload["bundle_id"] == "bundle-20260307T120000Z"
    assert payload["status"] == "draft"
    assert payload["artifacts"]["execution_pack"]["path"] == "execution_pack.json"


def test_bundle_status_values_are_stable():
    assert BundleStatus.draft.value == "draft"
    assert BundleStatus.need_more_info.value == "need_more_info"
    assert BundleStatus.approved.value == "approved"
    assert BundleStatus.blocked_by_risk.value == "blocked_by_risk"


def test_approval_event_accepts_valid_bundle_status_transitions():
    event = ApprovalEvent(
        event_id="approval-1",
        timestamp="2026-03-07T12:00:00+00:00",
        from_status=BundleStatus.draft,
        to_status=BundleStatus.approved,
        reviewer="alice",
        comment="ready",
    )

    assert event.from_status == "draft"
    assert event.to_status == "approved"


def test_artifact_ref_requires_basic_fields():
    ref = ArtifactRef(
        artifact_type="open_questions", path="outputs/run/open_questions.md"
    )

    assert ref.artifact_type == "open_questions"
    assert ref.path.endswith("open_questions.md")
    assert ref.content_hash == ""
