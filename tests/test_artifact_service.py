from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from pm_pal.service.artifact_service import (
    ArtifactContentNotFoundError,
    ArtifactReviewSummary,
    review_artifact_version_async,
)
from pm_pal.service.review_service import ReviewResultSummary
from pm_pal.workspace import (
    ArtifactRepository,
    ArtifactVersion,
    ArtifactVersionStatus,
    WorkspaceRepository,
    WorkspaceState,
    WorkspaceStateStatus,
)


def _build_source_version(content_path: Path) -> ArtifactVersion:
    return ArtifactVersion(
        version_id="artifact-v1",
        workspace_id="ws-1",
        artifact_key="prd_doc",
        artifact_type="prd_markdown",
        status=ArtifactVersionStatus.active,
        version_number=1,
        title="Payments PRD",
        source_run_id="seed-run-1",
        created_at="2026-04-13T08:00:00+00:00",
        updated_at="2026-04-13T08:00:00+00:00",
        content_path=str(content_path),
    )


@pytest.mark.asyncio
async def test_review_artifact_version_async_creates_review_result_and_link(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    source_path = tmp_path / "source.md"
    source_path.write_text(
        "# Login PRD\n\nThe system shall support SSO.", encoding="utf-8"
    )
    report_json_path = tmp_path / "outputs" / "run-123" / "report.json"
    report_md_path = tmp_path / "outputs" / "run-123" / "report.md"
    report_json_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_path.write_text(
        '{"status":"completed","review_results":[]}', encoding="utf-8"
    )
    report_md_path.write_text("# Review Report", encoding="utf-8")

    artifact_repository = ArtifactRepository(db_path)
    workspace_repository = WorkspaceRepository(db_path)
    await artifact_repository.initialize()
    await workspace_repository.initialize()

    source_version = _build_source_version(source_path)
    await artifact_repository.upsert_version(source_version)
    await workspace_repository.upsert_workspace(
        WorkspaceState(
            workspace_id="ws-1",
            name="Workspace 1",
            status=WorkspaceStateStatus.active,
            source_run_id="seed-run-1",
            current_run_id="seed-run-1",
            created_at="2026-04-13T08:00:00+00:00",
            updated_at="2026-04-13T08:00:00+00:00",
            versions=[source_version],
            current_version_ids={"prd_doc": "artifact-v1"},
        )
    )

    async def fake_review_prd_text_async(
        prd_text: str | None = None,
        *,
        prd_path: str | None = None,
        source: str | None = None,
        run_id: str | None = None,
        config_overrides: dict | None = None,
    ) -> ReviewResultSummary:
        assert "SSO" in str(prd_text or "")
        assert prd_path is None
        assert source is None
        assert config_overrides == {"outputs_root": str(tmp_path / "outputs")}
        return ReviewResultSummary(
            run_id=run_id or "run-123",
            report_md_path=str(report_md_path),
            report_json_path=str(report_json_path),
            high_risk_ratio=0.2,
            coverage_ratio=0.9,
            revision_round=1,
            status="completed",
        )

    monkeypatch.setattr(
        "pm_pal.service.artifact_service.review_prd_text_async",
        fake_review_prd_text_async,
    )

    result = await review_artifact_version_async(
        "artifact-v1",
        {
            "workspace_db_path": str(db_path),
            "outputs_root": str(tmp_path / "outputs"),
            "run_id": "run-123",
        },
    )

    assert isinstance(result, ArtifactReviewSummary)
    assert result.run_id == "run-123"
    assert result.workspace_id == "ws-1"
    assert result.artifact_version_id == "artifact-v1"
    assert result.review_result_version_id
    assert result.review_result_artifact_key == "prd_doc.review_result"

    loaded_review_result = await artifact_repository.get_version(
        result.review_result_version_id
    )
    assert loaded_review_result.ok is True
    assert loaded_review_result.value is not None
    assert loaded_review_result.value.content_path == str(report_json_path)
    assert (
        loaded_review_result.value.metadata["source_artifact_version_id"]
        == "artifact-v1"
    )

    workspace_result = await workspace_repository.get_workspace("ws-1")
    assert workspace_result.ok is True
    assert workspace_result.value is not None
    assert workspace_result.value.current_run_id == "run-123"
    assert (
        workspace_result.value.current_version_ids["prd_doc.review_result"]
        == result.review_result_version_id
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT run_id, workspace_id, artifact_version_id, review_result_version_id
            FROM artifact_review_runs
            WHERE run_id = ?
            """,
            ("run-123",),
        ).fetchone()

    assert row == ("run-123", "ws-1", "artifact-v1", result.review_result_version_id)


@pytest.mark.asyncio
async def test_review_artifact_version_async_raises_for_missing_content_path(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    artifact_repository = ArtifactRepository(db_path)
    await artifact_repository.initialize()
    await artifact_repository.upsert_version(
        ArtifactVersion(
            version_id="artifact-missing",
            workspace_id="ws-1",
            artifact_key="prd_doc",
            artifact_type="prd_markdown",
            status=ArtifactVersionStatus.active,
            version_number=1,
            created_at="2026-04-13T08:00:00+00:00",
            content_path=str(tmp_path / "missing.md"),
        )
    )

    async def unexpected_review(*args, **kwargs):
        raise AssertionError(
            "review_prd_text_async should not be called when artifact content is missing"
        )

    monkeypatch.setattr(
        "pm_pal.service.artifact_service.review_prd_text_async",
        unexpected_review,
    )

    with pytest.raises(ArtifactContentNotFoundError):
        await review_artifact_version_async(
            "artifact-missing",
            {"workspace_db_path": str(db_path)},
        )
