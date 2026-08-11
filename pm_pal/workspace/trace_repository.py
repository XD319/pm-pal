"""Async repository for trace links."""

from __future__ import annotations

from typing import Any

from .artifact_models import TraceLink
from .repository_support import RepositoryResult, SQLiteRepositoryBase


class TraceRepository(SQLiteRepositoryBase):
    """Persist and query trace links with a backend-neutral public API."""

    async def initialize(self) -> RepositoryResult[bool]:
        async def operation(connection: Any) -> bool:
            await self._ensure_schema(connection)
            await connection.commit()
            return True

        return await self._run("trace_repository.initialize", operation)

    async def upsert_link(self, trace_link: TraceLink) -> RepositoryResult[TraceLink]:
        async def operation(connection: Any) -> TraceLink:
            await self._ensure_schema(connection)
            await self._upsert_link_with_connection(connection, trace_link)
            await connection.commit()
            return trace_link

        return await self._run("trace_repository.upsert_link", operation)

    async def replace_links_for_source(
        self,
        source_type: str,
        source_id: str,
        trace_links: list[TraceLink],
    ) -> RepositoryResult[list[TraceLink]]:
        async def operation(connection: Any) -> list[TraceLink]:
            await self._ensure_schema(connection)
            await self._replace_links_for_source_with_connection(
                connection,
                source_type=source_type,
                source_id=source_id,
                trace_links=trace_links,
            )
            await connection.commit()
            return trace_links

        return await self._run("trace_repository.replace_links_for_source", operation)

    async def list_by_source(
        self, source_type: str, source_id: str
    ) -> RepositoryResult[list[TraceLink]]:
        async def operation(connection: Any) -> list[TraceLink]:
            await self._ensure_schema(connection)
            return await self._list_by_source_with_connection(
                connection, source_type, source_id
            )

        return await self._run("trace_repository.list_by_source", operation)

    async def list_by_target(
        self, target_type: str, target_id: str
    ) -> RepositoryResult[list[TraceLink]]:
        async def operation(connection: Any) -> list[TraceLink]:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT trace_id, source_type, source_id, target_type, target_id, link_type, status, source_run_id,
                       metadata_json
                FROM trace_links
                WHERE target_type = ? AND target_id = ?
                ORDER BY trace_id
                """,
                (target_type, target_id),
            )
            rows = await cursor.fetchall()
            return [self._row_to_trace_link(row) for row in rows]

        return await self._run("trace_repository.list_by_target", operation)

    async def list_by_run(
        self, source_run_id: str
    ) -> RepositoryResult[list[TraceLink]]:
        async def operation(connection: Any) -> list[TraceLink]:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT trace_id, source_type, source_id, target_type, target_id, link_type, status, source_run_id,
                       metadata_json
                FROM trace_links
                WHERE source_run_id = ?
                ORDER BY trace_id
                """,
                (source_run_id,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_trace_link(row) for row in rows]

        return await self._run("trace_repository.list_by_run", operation)

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS trace_links (
                trace_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                link_type TEXT NOT NULL,
                status TEXT NOT NULL,
                source_run_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_trace_links_source
                ON trace_links (source_type, source_id);

            CREATE INDEX IF NOT EXISTS idx_trace_links_target
                ON trace_links (target_type, target_id);

            CREATE INDEX IF NOT EXISTS idx_trace_links_run
                ON trace_links (source_run_id);
            """
        )

    async def _upsert_link_with_connection(
        self, connection: Any, trace_link: TraceLink
    ) -> None:
        await connection.execute(
            """
            INSERT INTO trace_links (
                trace_id,
                source_type,
                source_id,
                target_type,
                target_id,
                link_type,
                status,
                source_run_id,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                source_type = excluded.source_type,
                source_id = excluded.source_id,
                target_type = excluded.target_type,
                target_id = excluded.target_id,
                link_type = excluded.link_type,
                status = excluded.status,
                source_run_id = excluded.source_run_id,
                metadata_json = excluded.metadata_json
            """,
            (
                trace_link.trace_id,
                trace_link.source_type,
                trace_link.source_id,
                trace_link.target_type,
                trace_link.target_id,
                trace_link.link_type,
                trace_link.status,
                trace_link.source_run_id,
                self._dump_json(trace_link.metadata),
            ),
        )

    async def _replace_links_for_source_with_connection(
        self,
        connection: Any,
        *,
        source_type: str,
        source_id: str,
        trace_links: list[TraceLink],
    ) -> None:
        for trace_link in trace_links:
            if (
                trace_link.source_type != source_type
                or trace_link.source_id != source_id
            ):
                self._raise_validation_error(
                    "trace link source does not match replace_links_for_source target.",
                    details={
                        "expected_source_type": source_type,
                        "expected_source_id": source_id,
                        "trace_id": trace_link.trace_id,
                    },
                )

        await connection.execute(
            "DELETE FROM trace_links WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
        for trace_link in trace_links:
            await self._upsert_link_with_connection(connection, trace_link)

    async def _list_by_source_with_connection(
        self,
        connection: Any,
        source_type: str,
        source_id: str,
    ) -> list[TraceLink]:
        cursor = await connection.execute(
            """
            SELECT trace_id, source_type, source_id, target_type, target_id, link_type, status, source_run_id,
                   metadata_json
            FROM trace_links
            WHERE source_type = ? AND source_id = ?
            ORDER BY trace_id
            """,
            (source_type, source_id),
        )
        rows = await cursor.fetchall()
        return [self._row_to_trace_link(row) for row in rows]

    def _row_to_trace_link(self, row: Any) -> TraceLink:
        return TraceLink(
            trace_id=str(row["trace_id"]),
            source_type=str(row["source_type"]),
            source_id=str(row["source_id"]),
            target_type=str(row["target_type"]),
            target_id=str(row["target_id"]),
            link_type=str(row["link_type"]),
            status=str(row["status"]),
            source_run_id=str(row["source_run_id"]),
            metadata=self._load_json_object(row["metadata_json"]),
        )
