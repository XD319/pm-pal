from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("aiosqlite")

from pm_pal.workspace import (
    ArtifactRepository,
    ArtifactVersion,
    ArtifactVersionStatus,
    DecisionRecord,
    RepositoryErrorCode,
    TraceLink,
    TraceRepository,
    WorkspaceRepository,
    WorkspaceState,
    WorkspaceStateStatus,
)


@pytest.mark.asyncio
async def test_trace_repository_initializes_wal_mode(tmp_path) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    repository = TraceRepository(db_path)

    result = await repository.initialize()

    assert result.ok is True
    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(journal_mode).lower() == "wal"


@pytest.mark.asyncio
async def test_artifact_repository_round_trips_version_with_trace_links(
    tmp_path,
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    repository = ArtifactRepository(db_path)
    await repository.initialize()
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
        diff_from_parent_path="outputs/ws-1/review_report.v2.diff",
        patch_from_parent_path="outputs/ws-1/review_report.v2.patch",
        trace_links=[
            TraceLink(
                trace_id="trace-1",
                source_type="artifact_version",
                source_id="ver-2",
                target_type="feishu_block",
                target_id="wiki:block:123",
                source_run_id="run-20260413-001",
            )
        ],
        metadata={"origin": "langgraph"},
    )

    save_result = await repository.upsert_version(version)
    load_result = await repository.get_version("ver-2")

    assert save_result.ok is True
    assert load_result.ok is True
    assert load_result.value is not None
    assert load_result.value.parent_version_id == "ver-1"
    assert load_result.value.trace_links[0].target_type == "feishu_block"
    assert load_result.value.metadata["origin"] == "langgraph"


@pytest.mark.asyncio
async def test_workspace_repository_round_trips_aggregate_state(tmp_path) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    repository = WorkspaceRepository(db_path)
    await repository.initialize()
    workspace = WorkspaceState(
        workspace_id="ws-1",
        name="PRD Review Workspace",
        status=WorkspaceStateStatus.awaiting_review,
        source_run_id="run-20260413-001",
        current_run_id="run-20260413-002",
        created_at="2026-04-13T07:00:00+00:00",
        updated_at="2026-04-13T08:10:00+00:00",
        current_version_ids={"review_report": "ver-2"},
        versions=[
            ArtifactVersion(
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
                diff_from_parent_path="outputs/ws-1/review_report.v2.diff",
                patch_from_parent_path="outputs/ws-1/review_report.v2.patch",
            )
        ],
        decisions=[
            DecisionRecord(
                decision_id="decision-1",
                workspace_id="ws-1",
                decision_type="accept_patch",
                summary="Accept review report patch.",
                artifact_key="review_report",
                artifact_version_id="ver-2",
                parent_version_id="ver-1",
                source_run_id="run-20260413-001",
                actor="reviewer",
                created_at="2026-04-13T08:05:00+00:00",
                trace_links=[
                    TraceLink(
                        trace_id="trace-decision-1",
                        source_type="decision_record",
                        source_id="decision-1",
                        target_type="artifact_version",
                        target_id="ver-2",
                        source_run_id="run-20260413-001",
                    )
                ],
            )
        ],
        trace_links=[
            TraceLink(
                trace_id="trace-workspace-1",
                source_type="workspace",
                source_id="ws-1",
                target_type="run",
                target_id="run-20260413-002",
                source_run_id="run-20260413-001",
            )
        ],
        rerun_targets=["review_report"],
        metadata={"channel": "feishu"},
    )

    save_result = await repository.upsert_workspace(workspace)
    load_result = await repository.get_workspace("ws-1")

    assert save_result.ok is True
    assert load_result.ok is True
    assert load_result.value is not None
    assert load_result.value.current_version_ids["review_report"] == "ver-2"
    assert load_result.value.versions[0].artifact_type == "review_markdown"
    assert load_result.value.decisions[0].decision_type == "accept_patch"
    assert load_result.value.trace_links[0].target_type == "run"
    assert load_result.value.rerun_targets == ["review_report"]


@pytest.mark.asyncio
async def test_workspace_repository_returns_controlled_not_found_error(
    tmp_path,
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    repository = WorkspaceRepository(db_path)
    await repository.initialize()

    result = await repository.get_workspace("missing")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == RepositoryErrorCode.not_found
