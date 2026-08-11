"""Async repository for artifact versions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_models import ArtifactVersion
from .repository_support import RepositoryResult, SQLiteRepositoryBase
from .trace_repository import TraceRepository


class ArtifactRepository(SQLiteRepositoryBase):
    """Persist and query artifact versions without exposing SQLite details."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self.trace_repository = TraceRepository(db_path)

    async def initialize(self) -> RepositoryResult[bool]:
        async def operation(connection: Any) -> bool:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            await connection.commit()
            return True

        return await self._run("artifact_repository.initialize", operation)

    async def upsert_version(
        self, version: ArtifactVersion
    ) -> RepositoryResult[ArtifactVersion]:
        async def operation(connection: Any) -> ArtifactVersion:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            await self._upsert_version_with_connection(connection, version)
            await self.trace_repository._replace_links_for_source_with_connection(
                connection,
                source_type="artifact_version",
                source_id=version.version_id,
                trace_links=version.trace_links,
            )
            await connection.commit()
            return version

        return await self._run("artifact_repository.upsert_version", operation)

    async def get_version(self, version_id: str) -> RepositoryResult[ArtifactVersion]:
        async def operation(connection: Any) -> ArtifactVersion:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT version_id, workspace_id, artifact_key, artifact_type, status, version_number, title,
                       parent_version_id, source_run_id, created_at, updated_at, content_path, content_checksum,
                       diff_from_parent_path, patch_from_parent_path, change_summary, metadata_json
                FROM artifact_versions
                WHERE version_id = ?
                """,
                (version_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("artifact_version", version_id)
            return await self._row_to_version(connection, row)

        return await self._run("artifact_repository.get_version", operation)

    async def list_versions_by_workspace(
        self, workspace_id: str
    ) -> RepositoryResult[list[ArtifactVersion]]:
        async def operation(connection: Any) -> list[ArtifactVersion]:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            return await self._list_versions_by_workspace_with_connection(
                connection, workspace_id
            )

        return await self._run(
            "artifact_repository.list_versions_by_workspace", operation
        )

    async def list_versions_by_artifact(
        self,
        workspace_id: str,
        artifact_key: str,
    ) -> RepositoryResult[list[ArtifactVersion]]:
        async def operation(connection: Any) -> list[ArtifactVersion]:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            return await self._list_versions_by_artifact_with_connection(
                connection, workspace_id, artifact_key
            )

        return await self._run(
            "artifact_repository.list_versions_by_artifact", operation
        )

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifact_versions (
                version_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                artifact_key TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                status TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                parent_version_id TEXT,
                source_run_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                content_path TEXT NOT NULL DEFAULT '',
                content_checksum TEXT NOT NULL DEFAULT '',
                diff_from_parent_path TEXT NOT NULL DEFAULT '',
                patch_from_parent_path TEXT NOT NULL DEFAULT '',
                change_summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_artifact_versions_workspace
                ON artifact_versions (workspace_id, artifact_key, version_number);

            CREATE INDEX IF NOT EXISTS idx_artifact_versions_run
                ON artifact_versions (source_run_id);
            """
        )

    async def _upsert_version_with_connection(
        self, connection: Any, version: ArtifactVersion
    ) -> None:
        await connection.execute(
            """
            INSERT INTO artifact_versions (
                version_id,
                workspace_id,
                artifact_key,
                artifact_type,
                status,
                version_number,
                title,
                parent_version_id,
                source_run_id,
                created_at,
                updated_at,
                content_path,
                content_checksum,
                diff_from_parent_path,
                patch_from_parent_path,
                change_summary,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                artifact_key = excluded.artifact_key,
                artifact_type = excluded.artifact_type,
                status = excluded.status,
                version_number = excluded.version_number,
                title = excluded.title,
                parent_version_id = excluded.parent_version_id,
                source_run_id = excluded.source_run_id,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                content_path = excluded.content_path,
                content_checksum = excluded.content_checksum,
                diff_from_parent_path = excluded.diff_from_parent_path,
                patch_from_parent_path = excluded.patch_from_parent_path,
                change_summary = excluded.change_summary,
                metadata_json = excluded.metadata_json
            """,
            (
                version.version_id,
                version.workspace_id,
                version.artifact_key,
                version.artifact_type,
                version.status,
                version.version_number,
                version.title,
                version.parent_version_id,
                version.source_run_id,
                version.created_at,
                version.updated_at,
                version.content_path,
                version.content_checksum,
                version.diff_from_parent_path,
                version.patch_from_parent_path,
                version.change_summary,
                self._dump_json(version.metadata),
            ),
        )

    async def _list_versions_by_workspace_with_connection(
        self,
        connection: Any,
        workspace_id: str,
    ) -> list[ArtifactVersion]:
        cursor = await connection.execute(
            """
            SELECT version_id, workspace_id, artifact_key, artifact_type, status, version_number, title,
                   parent_version_id, source_run_id, created_at, updated_at, content_path, content_checksum,
                   diff_from_parent_path, patch_from_parent_path, change_summary, metadata_json
            FROM artifact_versions
            WHERE workspace_id = ?
            ORDER BY artifact_key, version_number
            """,
            (workspace_id,),
        )
        rows = await cursor.fetchall()
        return [await self._row_to_version(connection, row) for row in rows]

    async def _list_versions_by_artifact_with_connection(
        self,
        connection: Any,
        workspace_id: str,
        artifact_key: str,
    ) -> list[ArtifactVersion]:
        cursor = await connection.execute(
            """
            SELECT version_id, workspace_id, artifact_key, artifact_type, status, version_number, title,
                   parent_version_id, source_run_id, created_at, updated_at, content_path, content_checksum,
                   diff_from_parent_path, patch_from_parent_path, change_summary, metadata_json
            FROM artifact_versions
            WHERE workspace_id = ? AND artifact_key = ?
            ORDER BY version_number
            """,
            (workspace_id, artifact_key),
        )
        rows = await cursor.fetchall()
        return [await self._row_to_version(connection, row) for row in rows]

    async def _row_to_version(self, connection: Any, row: Any) -> ArtifactVersion:
        trace_links = await self.trace_repository._list_by_source_with_connection(
            connection,
            "artifact_version",
            str(row["version_id"]),
        )
        return ArtifactVersion(
            version_id=str(row["version_id"]),
            workspace_id=str(row["workspace_id"]),
            artifact_key=str(row["artifact_key"]),
            artifact_type=str(row["artifact_type"]),
            status=str(row["status"]),
            version_number=int(row["version_number"]),
            title=str(row["title"]),
            parent_version_id=str(row["parent_version_id"])
            if row["parent_version_id"] is not None
            else None,
            source_run_id=str(row["source_run_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            content_path=str(row["content_path"]),
            content_checksum=str(row["content_checksum"]),
            diff_from_parent_path=str(row["diff_from_parent_path"]),
            patch_from_parent_path=str(row["patch_from_parent_path"]),
            change_summary=str(row["change_summary"]),
            trace_links=trace_links,
            metadata=self._load_json_object(row["metadata_json"]),
        )
