"""Async repository for workspace aggregates and decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_models import DecisionRecord, WorkspaceState
from .artifact_repository import ArtifactRepository
from .repository_support import RepositoryResult, SQLiteRepositoryBase
from .trace_repository import TraceRepository


class WorkspaceRepository(SQLiteRepositoryBase):
    """Persist and query workspace aggregate state."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self.artifact_repository = ArtifactRepository(db_path)
        self.trace_repository = TraceRepository(db_path)

    async def initialize(self) -> RepositoryResult[bool]:
        async def operation(connection: Any) -> bool:
            await self._ensure_schema(connection)
            await self.artifact_repository._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            await connection.commit()
            return True

        return await self._run("workspace_repository.initialize", operation)

    async def upsert_workspace(
        self, workspace: WorkspaceState
    ) -> RepositoryResult[WorkspaceState]:
        async def operation(connection: Any) -> WorkspaceState:
            await self._ensure_schema(connection)
            await self.artifact_repository._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            await self._upsert_workspace_with_connection(connection, workspace)

            for version in workspace.versions:
                await self.artifact_repository._upsert_version_with_connection(
                    connection, version
                )
                await self.trace_repository._replace_links_for_source_with_connection(
                    connection,
                    source_type="artifact_version",
                    source_id=version.version_id,
                    trace_links=version.trace_links,
                )

            for decision in workspace.decisions:
                await self._upsert_decision_with_connection(connection, decision)
                await self.trace_repository._replace_links_for_source_with_connection(
                    connection,
                    source_type="decision_record",
                    source_id=decision.decision_id,
                    trace_links=decision.trace_links,
                )

            await self.trace_repository._replace_links_for_source_with_connection(
                connection,
                source_type="workspace",
                source_id=workspace.workspace_id,
                trace_links=workspace.trace_links,
            )
            await connection.commit()
            return workspace

        return await self._run("workspace_repository.upsert_workspace", operation)

    async def get_workspace(
        self, workspace_id: str
    ) -> RepositoryResult[WorkspaceState]:
        async def operation(connection: Any) -> WorkspaceState:
            await self._ensure_schema(connection)
            await self.artifact_repository._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT workspace_id, name, status, source_run_id, current_run_id, created_at, updated_at,
                       current_version_ids_json, rerun_targets_json, metadata_json
                FROM workspaces
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("workspace", workspace_id)
            return await self._row_to_workspace(connection, row)

        return await self._run("workspace_repository.get_workspace", operation)

    async def list_workspaces(
        self, *, status: str | None = None
    ) -> RepositoryResult[list[WorkspaceState]]:
        async def operation(connection: Any) -> list[WorkspaceState]:
            await self._ensure_schema(connection)
            await self.artifact_repository._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            if status:
                cursor = await connection.execute(
                    """
                    SELECT workspace_id, name, status, source_run_id, current_run_id, created_at, updated_at,
                           current_version_ids_json, rerun_targets_json, metadata_json
                    FROM workspaces
                    WHERE status = ?
                    ORDER BY updated_at DESC, workspace_id
                    """,
                    (status,),
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT workspace_id, name, status, source_run_id, current_run_id, created_at, updated_at,
                           current_version_ids_json, rerun_targets_json, metadata_json
                    FROM workspaces
                    ORDER BY updated_at DESC, workspace_id
                    """
                )
            rows = await cursor.fetchall()
            workspaces: list[WorkspaceState] = []
            for row in rows:
                workspaces.append(await self._row_to_workspace(connection, row))
            return workspaces

        return await self._run("workspace_repository.list_workspaces", operation)

    async def upsert_decision(
        self, decision: DecisionRecord
    ) -> RepositoryResult[DecisionRecord]:
        async def operation(connection: Any) -> DecisionRecord:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            await self._upsert_decision_with_connection(connection, decision)
            await self.trace_repository._replace_links_for_source_with_connection(
                connection,
                source_type="decision_record",
                source_id=decision.decision_id,
                trace_links=decision.trace_links,
            )
            await connection.commit()
            return decision

        return await self._run("workspace_repository.upsert_decision", operation)

    async def list_decisions(
        self, workspace_id: str
    ) -> RepositoryResult[list[DecisionRecord]]:
        async def operation(connection: Any) -> list[DecisionRecord]:
            await self._ensure_schema(connection)
            await self.trace_repository._ensure_schema(connection)
            return await self._list_decisions_with_connection(connection, workspace_id)

        return await self._run("workspace_repository.list_decisions", operation)

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                source_run_id TEXT NOT NULL DEFAULT '',
                current_run_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                current_version_ids_json TEXT NOT NULL DEFAULT '{}',
                rerun_targets_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS decision_records (
                decision_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                decision_type TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                artifact_key TEXT NOT NULL DEFAULT '',
                artifact_version_id TEXT NOT NULL DEFAULT '',
                parent_version_id TEXT,
                source_run_id TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_workspaces_status
                ON workspaces (status, updated_at);

            CREATE INDEX IF NOT EXISTS idx_decision_records_workspace
                ON decision_records (workspace_id, created_at);
            """
        )

    async def _upsert_workspace_with_connection(
        self, connection: Any, workspace: WorkspaceState
    ) -> None:
        await connection.execute(
            """
            INSERT INTO workspaces (
                workspace_id,
                name,
                status,
                source_run_id,
                current_run_id,
                created_at,
                updated_at,
                current_version_ids_json,
                rerun_targets_json,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id) DO UPDATE SET
                name = excluded.name,
                status = excluded.status,
                source_run_id = excluded.source_run_id,
                current_run_id = excluded.current_run_id,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                current_version_ids_json = excluded.current_version_ids_json,
                rerun_targets_json = excluded.rerun_targets_json,
                metadata_json = excluded.metadata_json
            """,
            (
                workspace.workspace_id,
                workspace.name,
                workspace.status,
                workspace.source_run_id,
                workspace.current_run_id,
                workspace.created_at,
                workspace.updated_at,
                self._dump_json(workspace.current_version_ids),
                self._dump_json(workspace.rerun_targets),
                self._dump_json(workspace.metadata),
            ),
        )

    async def _upsert_decision_with_connection(
        self, connection: Any, decision: DecisionRecord
    ) -> None:
        await connection.execute(
            """
            INSERT INTO decision_records (
                decision_id,
                workspace_id,
                decision_type,
                status,
                summary,
                reason,
                artifact_key,
                artifact_version_id,
                parent_version_id,
                source_run_id,
                actor,
                created_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET
                workspace_id = excluded.workspace_id,
                decision_type = excluded.decision_type,
                status = excluded.status,
                summary = excluded.summary,
                reason = excluded.reason,
                artifact_key = excluded.artifact_key,
                artifact_version_id = excluded.artifact_version_id,
                parent_version_id = excluded.parent_version_id,
                source_run_id = excluded.source_run_id,
                actor = excluded.actor,
                created_at = excluded.created_at,
                metadata_json = excluded.metadata_json
            """,
            (
                decision.decision_id,
                decision.workspace_id,
                decision.decision_type,
                decision.status,
                decision.summary,
                decision.reason,
                decision.artifact_key,
                decision.artifact_version_id,
                decision.parent_version_id,
                decision.source_run_id,
                decision.actor,
                decision.created_at,
                self._dump_json(decision.metadata),
            ),
        )

    async def _list_decisions_with_connection(
        self,
        connection: Any,
        workspace_id: str,
    ) -> list[DecisionRecord]:
        cursor = await connection.execute(
            """
            SELECT decision_id, workspace_id, decision_type, status, summary, reason, artifact_key,
                   artifact_version_id, parent_version_id, source_run_id, actor, created_at, metadata_json
            FROM decision_records
            WHERE workspace_id = ?
            ORDER BY created_at, decision_id
            """,
            (workspace_id,),
        )
        rows = await cursor.fetchall()
        decisions: list[DecisionRecord] = []
        for row in rows:
            decisions.append(await self._row_to_decision(connection, row))
        return decisions

    async def _row_to_decision(self, connection: Any, row: Any) -> DecisionRecord:
        trace_links = await self.trace_repository._list_by_source_with_connection(
            connection,
            "decision_record",
            str(row["decision_id"]),
        )
        return DecisionRecord(
            decision_id=str(row["decision_id"]),
            workspace_id=str(row["workspace_id"]),
            decision_type=str(row["decision_type"]),
            status=str(row["status"]),
            summary=str(row["summary"]),
            reason=str(row["reason"]),
            artifact_key=str(row["artifact_key"]),
            artifact_version_id=str(row["artifact_version_id"]),
            parent_version_id=str(row["parent_version_id"])
            if row["parent_version_id"] is not None
            else None,
            source_run_id=str(row["source_run_id"]),
            actor=str(row["actor"]),
            created_at=str(row["created_at"]),
            trace_links=trace_links,
            metadata=self._load_json_object(row["metadata_json"]),
        )

    async def _row_to_workspace(self, connection: Any, row: Any) -> WorkspaceState:
        versions = (
            await self.artifact_repository._list_versions_by_workspace_with_connection(
                connection,
                str(row["workspace_id"]),
            )
        )
        decisions = await self._list_decisions_with_connection(
            connection, str(row["workspace_id"])
        )
        trace_links = await self.trace_repository._list_by_source_with_connection(
            connection,
            "workspace",
            str(row["workspace_id"]),
        )
        return WorkspaceState(
            workspace_id=str(row["workspace_id"]),
            name=str(row["name"]),
            status=str(row["status"]),
            source_run_id=str(row["source_run_id"]),
            current_run_id=str(row["current_run_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            current_version_ids=self._load_json_object(row["current_version_ids_json"]),
            versions=versions,
            decisions=decisions,
            trace_links=trace_links,
            rerun_targets=[
                str(item) for item in self._load_json_list(row["rerun_targets_json"])
            ],
            metadata=self._load_json_object(row["metadata_json"]),
        )
