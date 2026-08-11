from __future__ import annotations

import json

import pytest

from pm_pal.workspace import (
    ArtifactVersion,
    ArtifactVersionStatus,
    DecisionRecord,
    TraceLink,
    WorkspaceState,
    WorkspaceStateStatus,
)


def test_artifact_version_supports_parent_patch_and_diff_fields() -> None:
    version = ArtifactVersion(
        version_id="ver-2",
        workspace_id="ws-1",
        artifact_key="review_report",
        artifact_type="review_markdown",
        status=ArtifactVersionStatus.active,
        version_number=2,
        parent_version_id="ver-1",
        source_run_id="run-20260413-001",
        created_at="2026-04-13T08:00:00+00:00",
        updated_at="2026-04-13T08:01:00+00:00",
        content_path="outputs/ws-1/review_report.v2.md",
        content_checksum="sha256:abc123",
        diff_from_parent_path="outputs/ws-1/review_report.v2.diff",
        patch_from_parent_path="outputs/ws-1/review_report.v2.patch",
        change_summary="Clarified the acceptance criteria section.",
    )

    payload = json.loads(version.model_dump_json())

    assert payload["artifact_type"] == "review_markdown"
    assert payload["status"] == "active"
    assert payload["parent_version_id"] == "ver-1"
    assert payload["source_run_id"] == "run-20260413-001"


def test_artifact_version_rejects_diff_without_parent() -> None:
    with pytest.raises(ValueError, match="diff/patch paths require parent_version_id"):
        ArtifactVersion(
            version_id="ver-1",
            workspace_id="ws-1",
            artifact_key="review_report",
            artifact_type="review_markdown",
            version_number=1,
            created_at="2026-04-13T08:00:00+00:00",
            diff_from_parent_path="outputs/ws-1/review_report.v1.diff",
        )


def test_workspace_state_tracks_current_versions_and_rerun_targets() -> None:
    trace_link = TraceLink(
        trace_id="trace-1",
        source_type="artifact_version",
        source_id="ver-2",
        target_type="feishu_block",
        target_id="wiki:block:123",
        link_type="sync_target",
        source_run_id="run-20260413-001",
    )
    version = ArtifactVersion(
        version_id="ver-2",
        workspace_id="ws-1",
        artifact_key="review_report",
        artifact_type="review_markdown",
        status=ArtifactVersionStatus.active,
        version_number=2,
        parent_version_id="ver-1",
        source_run_id="run-20260413-001",
        created_at="2026-04-13T08:00:00+00:00",
        updated_at="2026-04-13T08:01:00+00:00",
        trace_links=[trace_link],
    )
    state = WorkspaceState(
        workspace_id="ws-1",
        status=WorkspaceStateStatus.active,
        source_run_id="run-20260413-001",
        current_run_id="run-20260413-002",
        created_at="2026-04-13T07:00:00+00:00",
        trace_links=[trace_link],
    )

    state.register_version(version)
    state.mark_for_selective_rerun("review_report")
    state.mark_for_selective_rerun("review_report")

    assert state.get_current_version("review_report") is not None
    assert state.current_version_ids["review_report"] == "ver-2"
    assert state.rerun_targets == ["review_report"]
    assert (
        state.list_versions("review_report")[0].trace_links[0].target_type
        == "feishu_block"
    )


def test_workspace_state_validates_current_version_ids() -> None:
    with pytest.raises(ValueError, match="unknown version ids"):
        WorkspaceState(
            workspace_id="ws-1",
            created_at="2026-04-13T07:00:00+00:00",
            current_version_ids={"review_report": "ver-missing"},
        )


def test_decision_record_keeps_version_context_and_metadata() -> None:
    decision = DecisionRecord(
        decision_id="decision-1",
        workspace_id="ws-1",
        decision_type="accept_patch",
        summary="Accept reviewer patch for review report.",
        artifact_key="review_report",
        artifact_version_id="ver-2",
        parent_version_id="ver-1",
        source_run_id="run-20260413-001",
        actor="product_owner",
        created_at="2026-04-13T08:05:00+00:00",
        metadata={"feishu_message_id": "om_123"},
    )

    payload = json.loads(decision.model_dump_json())

    assert payload["decision_type"] == "accept_patch"
    assert payload["artifact_version_id"] == "ver-2"
    assert payload["metadata"]["feishu_message_id"] == "om_123"
