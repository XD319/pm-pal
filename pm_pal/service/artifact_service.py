"""Artifact-aware review service that composes the primitive text review API."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pm_pal.service.review_service import ReviewResultSummary, review_prd_text_async
from pm_pal.workspace import (
    ArtifactRepository,
    ArtifactVersion,
    ArtifactVersionStatus,
    RepositoryResult,
    TraceLink,
    WorkspaceRepository,
    WorkspaceState,
    WorkspaceStateStatus,
)

_DEFAULT_WORKSPACE_DB_PATH = Path("data") / "workspace.sqlite3"
_LINK_TABLE_NAME = "artifact_review_runs"
T = TypeVar("T")
_SERVICE_OPTION_KEYS = {
    "artifact_output_root",
    "run_id",
    "review_result_artifact_key",
    "review_result_artifact_type",
    "workspace_db_path",
}


@dataclass(slots=True)
class ArtifactReviewSummary:
    run_id: str
    workspace_id: str
    artifact_version_id: str
    review_result_version_id: str
    review_result_artifact_key: str
    status: str
    report_md_path: str
    report_json_path: str
    review: ReviewResultSummary

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["review"] = self.review.to_dict()
        return payload


class ArtifactReviewError(RuntimeError):
    """Base error raised by the artifact-aware review service."""


class ArtifactContentNotFoundError(FileNotFoundError, ArtifactReviewError):
    """Raised when the requested artifact content cannot be loaded."""


class ArtifactReviewPersistenceError(ArtifactReviewError):
    """Raised when SQLite persistence fails after the review run succeeds."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    return dict(options or {})


def _resolve_workspace_db_path(options: dict[str, Any]) -> Path:
    raw_path = str(options.get("workspace_db_path") or "").strip()
    return Path(raw_path) if raw_path else _DEFAULT_WORKSPACE_DB_PATH


def _build_review_overrides(options: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in options.items()
        if key not in _SERVICE_OPTION_KEYS and value is not None
    }


def _build_review_result_artifact_key(
    source_version: ArtifactVersion,
    options: dict[str, Any],
) -> str:
    configured = str(options.get("review_result_artifact_key") or "").strip()
    if configured:
        return configured
    return f"{source_version.artifact_key}.review_result"


def _resolve_artifact_content_path(raw_path: str, *, cwd: Path | None = None) -> Path:
    normalized = str(raw_path or "").strip()
    if not normalized:
        raise ArtifactContentNotFoundError("artifact version content_path is empty")
    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate
    return (cwd or Path.cwd()) / candidate


def _load_artifact_text(version: ArtifactVersion) -> str:
    content_path = _resolve_artifact_content_path(version.content_path)
    if not content_path.exists() or not content_path.is_file():
        raise ArtifactContentNotFoundError(
            f"artifact content not found for version_id={version.version_id}: {content_path}"
        )
    return content_path.read_text(encoding="utf-8")


def _compute_sha256(path: str) -> str:
    candidate = _resolve_artifact_content_path(path)
    if not candidate.exists() or not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _require_repository_value(result: RepositoryResult[T], action: str) -> T:
    if result.ok and result.value is not None:
        return result.value
    if result.error is not None:
        raise ArtifactReviewPersistenceError(
            f"{action} failed: {result.error.message} ({result.error.code})"
        )
    raise ArtifactReviewPersistenceError(f"{action} failed unexpectedly")


async def _ensure_link_table(db_path: Path) -> None:
    repository = ArtifactRepository(db_path)
    async with repository._open_connection() as connection:
        await connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {_LINK_TABLE_NAME} (
                run_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                artifact_version_id TEXT NOT NULL,
                review_result_version_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{{}}'
            );

            CREATE INDEX IF NOT EXISTS idx_artifact_review_runs_workspace
                ON {_LINK_TABLE_NAME} (workspace_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_artifact_review_runs_artifact
                ON {_LINK_TABLE_NAME} (artifact_version_id, created_at);
            """
        )
        await connection.commit()


async def _upsert_run_link(
    *,
    db_path: Path,
    run_id: str,
    workspace_id: str,
    artifact_version_id: str,
    review_result_version_id: str,
    metadata: dict[str, Any],
) -> None:
    repository = ArtifactRepository(db_path)
    timestamp = _utc_now_iso()
    async with repository._open_connection() as connection:
        await connection.execute(
            f"""
            INSERT INTO {_LINK_TABLE_NAME} (
                run_id,
                workspace_id,
                artifact_version_id,
                review_result_version_id,
                created_at,
                updated_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                artifact_version_id = excluded.artifact_version_id,
                review_result_version_id = excluded.review_result_version_id,
                updated_at = excluded.updated_at,
                metadata_json = excluded.metadata_json
            """,
            (
                run_id,
                workspace_id,
                artifact_version_id,
                review_result_version_id,
                timestamp,
                timestamp,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ),
        )
        await connection.commit()


def _merge_workspace_version(
    workspace: WorkspaceState,
    version: ArtifactVersion,
    *,
    make_current: bool = True,
) -> None:
    for index, existing in enumerate(workspace.versions):
        if existing.version_id == version.version_id:
            workspace.versions[index] = version
            if make_current:
                workspace.current_version_ids[version.artifact_key] = version.version_id
            workspace.updated_at = version.updated_at or version.created_at
            return
    workspace.register_version(version, make_current=make_current)


def _build_workspace_state(
    *,
    source_version: ArtifactVersion,
    review_result_version: ArtifactVersion,
    run_id: str,
    existing_workspace: WorkspaceState | None,
) -> WorkspaceState:
    timestamp = review_result_version.updated_at or review_result_version.created_at
    if existing_workspace is None:
        workspace = WorkspaceState(
            workspace_id=source_version.workspace_id,
            name=source_version.title or source_version.artifact_key,
            status=WorkspaceStateStatus.active,
            source_run_id=source_version.source_run_id,
            current_run_id=run_id,
            created_at=source_version.created_at or timestamp,
            updated_at=timestamp,
            versions=[source_version],
            current_version_ids={
                source_version.artifact_key: source_version.version_id
            },
            metadata={"artifact_review_enabled": True},
        )
    else:
        workspace = existing_workspace
        workspace.current_run_id = run_id
        workspace.updated_at = timestamp
        if not workspace.source_run_id and source_version.source_run_id:
            workspace.source_run_id = source_version.source_run_id
        metadata = dict(workspace.metadata)
        metadata["artifact_review_enabled"] = True
        workspace.metadata = metadata
        _merge_workspace_version(workspace, source_version, make_current=False)

    _merge_workspace_version(workspace, review_result_version, make_current=True)
    return workspace


async def _load_workspace_or_none(
    repository: WorkspaceRepository,
    workspace_id: str,
) -> WorkspaceState | None:
    result = await repository.get_workspace(workspace_id)
    if result.ok:
        return result.value
    if result.error is not None and str(result.error.code) == "not_found":
        return None
    _require_repository_value(result, "workspace_repository.get_workspace")
    return None


async def _build_review_result_version(
    *,
    artifact_repository: ArtifactRepository,
    source_version: ArtifactVersion,
    review_summary: ReviewResultSummary,
    options: dict[str, Any],
) -> ArtifactVersion:
    artifact_key = _build_review_result_artifact_key(source_version, options)
    existing_versions = _require_repository_value(
        await artifact_repository.list_versions_by_artifact(
            source_version.workspace_id, artifact_key
        ),
        "artifact_repository.list_versions_by_artifact",
    )
    latest_version = existing_versions[-1] if existing_versions else None
    version_number = (latest_version.version_number + 1) if latest_version else 1
    timestamp = _utc_now_iso()
    review_result_artifact_type = str(
        options.get("review_result_artifact_type") or "review_result"
    ).strip()
    return ArtifactVersion(
        version_id=f"avr-{uuid4().hex}",
        workspace_id=source_version.workspace_id,
        artifact_key=artifact_key,
        artifact_type=review_result_artifact_type or "review_result",
        status=ArtifactVersionStatus.active,
        version_number=version_number,
        title=f"Review result for {source_version.title or source_version.artifact_key}",
        parent_version_id=latest_version.version_id
        if latest_version is not None
        else None,
        source_run_id=review_summary.run_id,
        created_at=timestamp,
        updated_at=timestamp,
        content_path=review_summary.report_json_path,
        content_checksum=_compute_sha256(review_summary.report_json_path),
        change_summary=f"Generated review_result from artifact version {source_version.version_id}",
        trace_links=[
            TraceLink(
                trace_id=f"trace-{uuid4().hex}",
                source_type="artifact_version",
                source_id="",
                target_type="artifact_version",
                target_id=source_version.version_id,
                link_type="derived_from",
                source_run_id=review_summary.run_id,
                metadata={"workspace_id": source_version.workspace_id},
            ),
            TraceLink(
                trace_id=f"trace-{uuid4().hex}",
                source_type="artifact_version",
                source_id="",
                target_type="review_run",
                target_id=review_summary.run_id,
                link_type="generated_by",
                source_run_id=review_summary.run_id,
                metadata={"workspace_id": source_version.workspace_id},
            ),
        ],
        metadata={
            "source_artifact_version_id": source_version.version_id,
            "report_md_path": review_summary.report_md_path,
            "report_json_path": review_summary.report_json_path,
            "high_risk_ratio": review_summary.high_risk_ratio,
            "coverage_ratio": review_summary.coverage_ratio,
            "status": review_summary.status,
        },
    )


def _finalize_trace_links(version: ArtifactVersion) -> ArtifactVersion:
    finalized_links: list[TraceLink] = []
    for link in version.trace_links:
        finalized_links.append(
            TraceLink(
                trace_id=link.trace_id,
                source_type=link.source_type,
                source_id=version.version_id,
                target_type=link.target_type,
                target_id=link.target_id,
                link_type=link.link_type,
                status=link.status,
                source_run_id=link.source_run_id,
                metadata=link.metadata,
            )
        )
    version.trace_links = finalized_links
    return version


async def review_artifact_version_async(
    artifact_version_id: str,
    options: dict[str, Any] | None = None,
) -> ArtifactReviewSummary:
    normalized_artifact_version_id = str(artifact_version_id or "").strip()
    if not normalized_artifact_version_id:
        raise ValueError("artifact_version_id is required")

    resolved_options = _normalize_options(options)
    workspace_db_path = _resolve_workspace_db_path(resolved_options)
    artifact_repository = ArtifactRepository(workspace_db_path)
    workspace_repository = WorkspaceRepository(workspace_db_path)

    _require_repository_value(
        await artifact_repository.initialize(), "artifact_repository.initialize"
    )
    _require_repository_value(
        await workspace_repository.initialize(), "workspace_repository.initialize"
    )
    await _ensure_link_table(workspace_db_path)

    source_version = _require_repository_value(
        await artifact_repository.get_version(normalized_artifact_version_id),
        "artifact_repository.get_version",
    )
    artifact_text = _load_artifact_text(source_version)

    review_overrides = _build_review_overrides(resolved_options)
    explicit_run_id = str(resolved_options.get("run_id") or "").strip() or None
    review_summary = await review_prd_text_async(
        prd_text=artifact_text,
        run_id=explicit_run_id,
        config_overrides=review_overrides,
    )

    try:
        review_result_version = _finalize_trace_links(
            await _build_review_result_version(
                artifact_repository=artifact_repository,
                source_version=source_version,
                review_summary=review_summary,
                options=resolved_options,
            )
        )
        _require_repository_value(
            await artifact_repository.upsert_version(review_result_version),
            "artifact_repository.upsert_version",
        )

        existing_workspace = await _load_workspace_or_none(
            workspace_repository, source_version.workspace_id
        )
        workspace_state = _build_workspace_state(
            source_version=source_version,
            review_result_version=review_result_version,
            run_id=review_summary.run_id,
            existing_workspace=existing_workspace,
        )
        _require_repository_value(
            await workspace_repository.upsert_workspace(workspace_state),
            "workspace_repository.upsert_workspace",
        )

        await _upsert_run_link(
            db_path=workspace_db_path,
            run_id=review_summary.run_id,
            workspace_id=source_version.workspace_id,
            artifact_version_id=source_version.version_id,
            review_result_version_id=review_result_version.version_id,
            metadata={
                "review_result_artifact_key": review_result_version.artifact_key,
                "review_result_artifact_type": review_result_version.artifact_type,
                "source_artifact_key": source_version.artifact_key,
            },
        )
    except ArtifactReviewError:
        raise
    except Exception as exc:
        raise ArtifactReviewPersistenceError(
            f"artifact-aware review persistence failed for run_id={review_summary.run_id}: {exc}"
        ) from exc

    return ArtifactReviewSummary(
        run_id=review_summary.run_id,
        workspace_id=source_version.workspace_id,
        artifact_version_id=source_version.version_id,
        review_result_version_id=review_result_version.version_id,
        review_result_artifact_key=review_result_version.artifact_key,
        status=review_summary.status,
        report_md_path=review_summary.report_md_path,
        report_json_path=review_summary.report_json_path,
        review=review_summary,
    )
