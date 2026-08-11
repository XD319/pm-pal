"""SQLite-backed repository for lightweight PRD review memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pm_pal.utils.time import utc_now_iso as _utc_now_iso
from pm_pal.workspace.repository_support import RepositoryResult, SQLiteRepositoryBase

from .models import (
    MemoryApplicability,
    MemoryEvidence,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    MemoryScopeLevel,
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


class MemoryRepository(SQLiteRepositoryBase):
    """Persist structured review memory and append-only write audit rows."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)

    async def initialize(self) -> RepositoryResult[bool]:
        async def operation(connection: Any) -> bool:
            await self._ensure_schema(connection)
            await connection.commit()
            return True

        return await self._run("memory_repository.initialize", operation)

    async def save_memory(
        self, record: MemoryRecord, *, actor: str = "system"
    ) -> RepositoryResult[MemoryRecord]:
        async def operation(connection: Any) -> MemoryRecord:
            await self._ensure_schema(connection)
            saved = await self._upsert_memory_with_connection(connection, record)
            await self._append_audit_row_with_connection(
                connection, saved, actor=actor, operation="save"
            )
            await connection.commit()
            return saved

        return await self._run("memory_repository.save_memory", operation)

    async def list_by_scope(
        self,
        *,
        level: MemoryScopeLevel,
        team_id: str | None = None,
        project_id: str | None = None,
    ) -> RepositoryResult[list[MemoryRecord]]:
        async def operation(connection: Any) -> list[MemoryRecord]:
            await self._ensure_schema(connection)
            where_clauses = ["scope_level = ?"]
            params: list[Any] = [_enum_value(level)]
            if level == MemoryScopeLevel.team:
                where_clauses.append("scope_team_id = ?")
                params.append(str(team_id or "").strip())
            if level == MemoryScopeLevel.project:
                where_clauses.append("scope_project_id = ?")
                params.append(str(project_id or "").strip())
            cursor = await connection.execute(
                f"""
                SELECT *
                FROM memories
                WHERE {" AND ".join(where_clauses)}
                ORDER BY updated_at DESC, memory_id
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
            return [await self._row_to_memory(connection, row) for row in rows]

        return await self._run("memory_repository.list_by_scope", operation)

    async def query_memories(
        self, query: MemoryQuery
    ) -> RepositoryResult[list[MemoryRecord]]:
        async def operation(connection: Any) -> list[MemoryRecord]:
            await self._ensure_schema(connection)
            clauses = ["1 = 1"]
            params: list[Any] = []

            if query.scope_level is not None:
                clauses.append("m.scope_level = ?")
                params.append(_enum_value(query.scope_level))
            if query.team_id.strip():
                clauses.append("m.scope_team_id = ?")
                params.append(query.team_id.strip())
            if query.project_id.strip():
                clauses.append("m.scope_project_id = ?")
                params.append(query.project_id.strip())
            if query.memory_type is not None:
                clauses.append("m.memory_type = ?")
                params.append(_enum_value(query.memory_type))
            if query.tag.strip():
                clauses.append("m.tags_json LIKE ?")
                params.append(f'%"{query.tag.strip()}"%')
            join_clause = ""
            if query.requirement_type.strip():
                join_clause = """
                INNER JOIN memory_scope_requirement_types req
                    ON req.memory_id = m.memory_id
                """
                clauses.append("req.requirement_type = ?")
                params.append(query.requirement_type.strip())

            cursor = await connection.execute(
                f"""
                SELECT DISTINCT m.*
                FROM memories m
                {join_clause}
                WHERE {" AND ".join(clauses)}
                ORDER BY m.updated_at DESC, m.memory_id
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
            return [await self._row_to_memory(connection, row) for row in rows]

        return await self._run("memory_repository.query_memories", operation)

    async def list_audit_rows(
        self, memory_id: str
    ) -> RepositoryResult[list[dict[str, Any]]]:
        async def operation(connection: Any) -> list[dict[str, Any]]:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT audit_id, memory_id, operation, actor, created_at, payload_json
                FROM memory_write_audit
                WHERE memory_id = ?
                ORDER BY created_at, audit_id
                """,
                (memory_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "audit_id": str(row["audit_id"]),
                    "memory_id": str(row["memory_id"]),
                    "operation": str(row["operation"]),
                    "actor": str(row["actor"]),
                    "created_at": str(row["created_at"]),
                    "payload": self._load_json_object(row["payload_json"]),
                }
                for row in rows
            ]

        return await self._run("memory_repository.list_audit_rows", operation)

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                content TEXT NOT NULL,
                scope_level TEXT NOT NULL,
                scope_team_id TEXT NOT NULL DEFAULT '',
                scope_project_id TEXT NOT NULL DEFAULT '',
                applicability_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0,
                reuse_score REAL NOT NULL DEFAULT 0,
                expiry_hint TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                do_not_overapply TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS memory_scope_requirement_types (
                memory_id TEXT NOT NULL,
                requirement_type TEXT NOT NULL,
                PRIMARY KEY (memory_id, requirement_type),
                FOREIGN KEY (memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memory_write_audit (
                audit_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (memory_id) REFERENCES memories(memory_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories (scope_level, scope_team_id, scope_project_id, updated_at);

            CREATE INDEX IF NOT EXISTS idx_memories_type
                ON memories (memory_type, updated_at);

            CREATE INDEX IF NOT EXISTS idx_memory_req_types
                ON memory_scope_requirement_types (requirement_type, memory_id);

            CREATE INDEX IF NOT EXISTS idx_memory_audit_memory
                ON memory_write_audit (memory_id, created_at);
            """
        )

    async def _upsert_memory_with_connection(
        self, connection: Any, record: MemoryRecord
    ) -> MemoryRecord:
        normalized = MemoryRecord.model_validate(record.model_dump())
        if not normalized.memory_id.strip():
            self._raise_validation_error("memory_id is required")
        if not normalized.title.strip():
            self._raise_validation_error("title is required")
        if not normalized.summary.strip():
            self._raise_validation_error("summary is required")
        if not normalized.content.strip():
            self._raise_validation_error("content is required")

        timestamp = _utc_now_iso()
        created_at = normalized.created_at or timestamp
        updated_at = timestamp

        await connection.execute(
            """
            INSERT INTO memories (
                memory_id,
                memory_type,
                title,
                summary,
                content,
                scope_level,
                scope_team_id,
                scope_project_id,
                applicability_json,
                evidence_json,
                confidence,
                reuse_score,
                expiry_hint,
                tags_json,
                do_not_overapply,
                created_at,
                updated_at,
                created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                memory_type = excluded.memory_type,
                title = excluded.title,
                summary = excluded.summary,
                content = excluded.content,
                scope_level = excluded.scope_level,
                scope_team_id = excluded.scope_team_id,
                scope_project_id = excluded.scope_project_id,
                applicability_json = excluded.applicability_json,
                evidence_json = excluded.evidence_json,
                confidence = excluded.confidence,
                reuse_score = excluded.reuse_score,
                expiry_hint = excluded.expiry_hint,
                tags_json = excluded.tags_json,
                do_not_overapply = excluded.do_not_overapply,
                updated_at = excluded.updated_at,
                created_by = excluded.created_by
            """,
            (
                normalized.memory_id,
                _enum_value(normalized.memory_type),
                normalized.title,
                normalized.summary,
                normalized.content,
                _enum_value(normalized.scope.level),
                normalized.scope.team_id,
                normalized.scope.project_id,
                self._dump_json(normalized.applicability.model_dump()),
                self._dump_json([item.model_dump() for item in normalized.evidence]),
                float(normalized.confidence),
                float(normalized.reuse_score),
                normalized.expiry_hint,
                self._dump_json(normalized.tags),
                normalized.do_not_overapply,
                created_at,
                updated_at,
                normalized.created_by,
            ),
        )
        await connection.execute(
            "DELETE FROM memory_scope_requirement_types WHERE memory_id = ?",
            (normalized.memory_id,),
        )
        for requirement_type in normalized.scope.requirement_type:
            await connection.execute(
                """
                INSERT INTO memory_scope_requirement_types (memory_id, requirement_type)
                VALUES (?, ?)
                """,
                (normalized.memory_id, requirement_type),
            )
        return normalized.model_copy(
            update={"created_at": created_at, "updated_at": updated_at}
        )

    async def _append_audit_row_with_connection(
        self,
        connection: Any,
        record: MemoryRecord,
        *,
        actor: str,
        operation: str,
    ) -> None:
        audit_timestamp = _utc_now_iso()
        audit_id = f"{record.memory_id}:{operation}:{audit_timestamp}"
        await connection.execute(
            """
            INSERT INTO memory_write_audit (
                audit_id,
                memory_id,
                operation,
                actor,
                created_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                record.memory_id,
                operation,
                str(actor or "").strip(),
                audit_timestamp,
                self._dump_json(record.model_dump(mode="json")),
            ),
        )

    async def _row_to_memory(self, connection: Any, row: Any) -> MemoryRecord:
        cursor = await connection.execute(
            """
            SELECT requirement_type
            FROM memory_scope_requirement_types
            WHERE memory_id = ?
            ORDER BY requirement_type
            """,
            (str(row["memory_id"]),),
        )
        requirement_rows = await cursor.fetchall()
        evidence_payload = self._load_json_list(row["evidence_json"])
        return MemoryRecord(
            memory_id=str(row["memory_id"]),
            memory_type=str(row["memory_type"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            content=str(row["content"]),
            scope=MemoryScope(
                level=str(row["scope_level"]),
                team_id=str(row["scope_team_id"]),
                project_id=str(row["scope_project_id"]),
                requirement_type=[
                    str(item["requirement_type"]) for item in requirement_rows
                ],
            ),
            applicability=MemoryApplicability.model_validate(
                self._load_json_object(row["applicability_json"])
            ),
            evidence=[
                MemoryEvidence.model_validate(item)
                for item in evidence_payload
                if isinstance(item, dict)
            ],
            confidence=float(row["confidence"]),
            reuse_score=float(row["reuse_score"]),
            expiry_hint=str(row["expiry_hint"]),
            tags=[str(item) for item in self._load_json_list(row["tags_json"])],
            do_not_overapply=str(row["do_not_overapply"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            created_by=str(row["created_by"]),
        )
