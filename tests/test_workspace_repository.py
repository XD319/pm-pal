from __future__ import annotations

import json

from pm_pal.packs.delivery_bundle import (
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)
from pm_pal.workspace import (
    ApprovalRecord,
    ReviewWorkspaceRepository,
    StatusSnapshot,
    WorkspaceStatus,
    bundle_status_from_workspace_status,
    workspace_status_from_bundle_status,
)


def _bundle(*, status: BundleStatus = BundleStatus.draft) -> DeliveryBundle:
    return DeliveryBundle(
        bundle_id="bundle-20260308T120000Z",
        created_at="2026-03-08T12:00:00+00:00",
        status=status,
        source_run_id="20260308T120000Z",
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


def test_workspace_repository_initializes_empty_repository(tmp_path) -> None:
    run_dir = tmp_path / "20260308T120000Z"
    repository = ReviewWorkspaceRepository(run_dir)

    workspace = repository.load_workspace()

    assert workspace.run_id == "20260308T120000Z"
    assert workspace.bundle_id == ""
    assert workspace.bundle_status is None
    assert workspace.workspace_status == "deferred"
    assert workspace.approval_history == []
    assert workspace.approval_records == []
    assert workspace.status_snapshot is None
    assert repository.load_approval_records() == []
    assert repository.load_status_snapshot() is None


def test_workspace_repository_save_round_trips_records_and_snapshot(tmp_path) -> None:
    run_dir = tmp_path / "20260308T120001Z"
    repository = ReviewWorkspaceRepository(run_dir)
    approval_record = ApprovalRecord(
        record_id="approval-record-1",
        run_id="20260308T120001Z",
        bundle_id="bundle-20260308T120001Z",
        timestamp="2026-03-08T12:00:00+00:00",
        action="approve",
        from_bundle_status=BundleStatus.draft,
        to_bundle_status=BundleStatus.approved,
        workspace_status=WorkspaceStatus.confirmed,
        reviewer="alice",
        comment="ready to ship",
    )
    status_snapshot = StatusSnapshot(
        run_id="20260308T120001Z",
        bundle_id="bundle-20260308T120001Z",
        bundle_status=BundleStatus.approved,
        workspace_status=WorkspaceStatus.confirmed,
        updated_at="2026-03-08T12:00:00+00:00",
    )

    approval_path = repository.save_approval_records([approval_record])
    snapshot_path = repository.save_status_snapshot(status_snapshot)

    reloaded = ReviewWorkspaceRepository(run_dir)
    loaded_records = reloaded.load_approval_records()
    loaded_snapshot = reloaded.load_status_snapshot()
    workspace = reloaded.load_workspace()

    assert approval_path.name == "approval_records.json"
    assert snapshot_path.name == "status_snapshot.json"
    assert len(loaded_records) == 1
    assert loaded_records[0].record_id == "approval-record-1"
    assert loaded_records[0].to_bundle_status == "approved"
    assert loaded_records[0].workspace_status == "confirmed"
    assert loaded_snapshot is not None
    assert loaded_snapshot.bundle_status == "approved"
    assert loaded_snapshot.workspace_status == "confirmed"
    assert workspace.run_id == "20260308T120001Z"
    assert workspace.bundle_id == "bundle-20260308T120001Z"
    assert workspace.workspace_status == "confirmed"
    assert workspace.status_snapshot is not None


def test_workspace_status_enum_values_serialize_as_strings(tmp_path) -> None:
    run_dir = tmp_path / "20260308T120002Z"
    repository = ReviewWorkspaceRepository(run_dir)
    approval_record = ApprovalRecord(
        record_id="approval-record-2",
        run_id="20260308T120002Z",
        bundle_id="bundle-20260308T120002Z",
        timestamp="2026-03-08T12:05:00+00:00",
        action="block_by_risk",
        from_bundle_status=BundleStatus.draft,
        to_bundle_status=BundleStatus.blocked_by_risk,
        workspace_status=WorkspaceStatus.blocked_by_risk,
        reviewer="bob",
        comment="security issue",
    )
    status_snapshot = StatusSnapshot(
        run_id="20260308T120002Z",
        bundle_id="bundle-20260308T120002Z",
        bundle_status=BundleStatus.blocked_by_risk,
        workspace_status=WorkspaceStatus.blocked_by_risk,
        updated_at="2026-03-08T12:05:00+00:00",
    )

    repository.save_approval_records([approval_record])
    repository.save_status_snapshot(status_snapshot)

    approval_payload = json.loads(
        repository.approval_records_path.read_text(encoding="utf-8")
    )
    snapshot_payload = json.loads(
        repository.status_snapshot_path.read_text(encoding="utf-8")
    )

    assert approval_payload["approval_records"][0]["from_bundle_status"] == "draft"
    assert (
        approval_payload["approval_records"][0]["to_bundle_status"] == "blocked_by_risk"
    )
    assert (
        approval_payload["approval_records"][0]["workspace_status"] == "blocked_by_risk"
    )
    assert snapshot_payload["bundle_status"] == "blocked_by_risk"
    assert snapshot_payload["workspace_status"] == "blocked_by_risk"


def test_workspace_repository_maps_bundle_status_into_workspace_status(
    tmp_path,
) -> None:
    run_dir = tmp_path / "20260308T120003Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    bundle = _bundle(status=BundleStatus.approved)
    bundle_path = run_dir / "delivery_bundle.json"
    bundle_path.write_text(
        json.dumps(bundle.model_dump(mode="python"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    workspace = ReviewWorkspaceRepository(run_dir).load_workspace()

    assert workspace.bundle_status == "approved"
    assert workspace.workspace_status == "confirmed"
    assert (
        workspace_status_from_bundle_status(BundleStatus.draft)
        == WorkspaceStatus.deferred
    )
    assert (
        bundle_status_from_workspace_status(WorkspaceStatus.confirmed)
        == BundleStatus.approved
    )
    assert bundle_status_from_workspace_status(WorkspaceStatus.out_of_scope) is None
