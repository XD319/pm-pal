"""SQLite repository for sources and evidence; safe for a single-team pilot."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from prd_pal.utils.time import utc_now_iso
from prd_pal.workspace.repository_support import RepositoryResult, SQLiteRepositoryBase

from .models import EvidenceRecord, EvidenceSource, SourceSyncStatus


class ProductDecisionRepository(SQLiteRepositoryBase):
    async def initialize(self) -> RepositoryResult[bool]:
        async def operation(connection: Any) -> bool:
            await self._ensure_schema(connection)
            await connection.commit()
            return True
        return await self._run("product_decision.initialize", operation)

    async def upsert_source(self, source: EvidenceSource) -> RepositoryResult[EvidenceSource]:
        async def operation(connection: Any) -> EvidenceSource:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_evidence_source
                (id, product_id, source_type, source_url, display_name, sync_status, sync_cursor, last_synced_at, last_error, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET product_id=excluded.product_id, source_type=excluded.source_type,
                source_url=excluded.source_url, display_name=excluded.display_name, sync_status=excluded.sync_status,
                sync_cursor=excluded.sync_cursor, last_synced_at=excluded.last_synced_at, last_error=excluded.last_error,
                metadata_json=excluded.metadata_json""",
                (source.id, source.product_id, source.source_type, source.source_url, source.display_name,
                 str(source.sync_status), source.sync_cursor, source.last_synced_at, source.last_error, self._dump_json(source.metadata)),
            )
            await connection.commit()
            return source
        return await self._run("product_decision.upsert_source", operation)

    async def get_source(self, source_id: str) -> RepositoryResult[EvidenceSource]:
        async def operation(connection: Any) -> EvidenceSource:
            await self._ensure_schema(connection)
            cursor = await connection.execute("SELECT * FROM decision_evidence_source WHERE id = ?", (source_id,))
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("evidence_source", source_id)
            return self._source_from_row(row)
        return await self._run("product_decision.get_source", operation)

    async def list_sources(self, product_id: str = "") -> RepositoryResult[list[EvidenceSource]]:
        async def operation(connection: Any) -> list[EvidenceSource]:
            await self._ensure_schema(connection)
            query = "SELECT * FROM decision_evidence_source"
            params: tuple[str, ...] = ()
            if product_id:
                query += " WHERE product_id = ?"
                params = (product_id,)
            query += " ORDER BY display_name, id"
            rows = await (await connection.execute(query, params)).fetchall()
            return [self._source_from_row(row) for row in rows]
        return await self._run("product_decision.list_sources", operation)

    async def sync_evidence(self, source_id: str, records: list[EvidenceRecord], *, cursor: str = "") -> RepositoryResult[list[EvidenceRecord]]:
        """Idempotently upsert source records by ``(source_id, external_id)``."""
        async def operation(connection: Any) -> list[EvidenceRecord]:
            await self._ensure_schema(connection)
            source = await self._source_exists(connection, source_id)
            await connection.execute("UPDATE decision_evidence_source SET sync_status = ?, last_error = '' WHERE id = ?", (SourceSyncStatus.syncing, source_id))
            saved: list[EvidenceRecord] = []
            for record in records:
                if record.source_id != source_id:
                    self._raise_validation_error("Evidence record source_id must match the sync source.")
                now = utc_now_iso()
                persisted = record.model_copy(update={
                    "id": record.id or f"evidence-{uuid.uuid4().hex[:12]}",
                    "product_id": record.product_id or source.product_id,
                    "created_at": record.created_at or now,
                    "updated_at": now,
                })
                await connection.execute(
                    """INSERT INTO decision_evidence
                    (id, source_id, external_id, product_id, content, summary, quote, source_url, author, occurred_at, source_version, source_refs_json, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET content=excluded.content, summary=excluded.summary,
                    quote=excluded.quote, source_url=excluded.source_url, author=excluded.author, occurred_at=excluded.occurred_at,
                    source_version=excluded.source_version, source_refs_json=excluded.source_refs_json, metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at""",
                    (persisted.id, persisted.source_id, persisted.external_id, persisted.product_id, persisted.content,
                     persisted.summary, persisted.quote or persisted.content[:280], persisted.source_url, persisted.author,
                     persisted.occurred_at, persisted.source_version, self._dump_json(persisted.source_refs),
                     self._dump_json(persisted.metadata), persisted.created_at, persisted.updated_at),
                )
                row = await (await connection.execute("SELECT * FROM decision_evidence WHERE source_id = ? AND external_id = ?", (source_id, persisted.external_id))).fetchone()
                saved.append(self._evidence_from_row(row))
            await connection.execute(
                "UPDATE decision_evidence_source SET sync_status = ?, sync_cursor = ?, last_synced_at = ?, last_error = '' WHERE id = ?",
                (SourceSyncStatus.succeeded, cursor, utc_now_iso(), source_id),
            )
            await connection.commit()
            return saved
        return await self._run("product_decision.sync_evidence", operation)

    async def mark_sync_failed(self, source_id: str, message: str) -> RepositoryResult[EvidenceSource]:
        source_result = await self.get_source(source_id)
        if not source_result.ok or source_result.value is None:
            return source_result
        source = source_result.value.model_copy(update={"sync_status": SourceSyncStatus.failed, "last_error": message})
        return await self.upsert_source(source)

    async def list_evidence(self, *, product_id: str = "", query: str = "", limit: int = 100) -> RepositoryResult[list[EvidenceRecord]]:
        async def operation(connection: Any) -> list[EvidenceRecord]:
            await self._ensure_schema(connection)
            clauses, params = [], []
            if product_id:
                clauses.append("product_id = ?"); params.append(product_id)
            if query:
                clauses.append("(content LIKE ? OR summary LIKE ? OR quote LIKE ?)"); params.extend([f"%{query}%"] * 3)
            statement = "SELECT * FROM decision_evidence" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY occurred_at DESC, updated_at DESC LIMIT ?"
            params.append(max(1, int(limit)))
            rows = await (await connection.execute(statement, tuple(params))).fetchall()
            return [self._evidence_from_row(row) for row in rows]
        return await self._run("product_decision.list_evidence", operation)

    async def _source_exists(self, connection: Any, source_id: str) -> EvidenceSource:
        row = await (await connection.execute("SELECT * FROM decision_evidence_source WHERE id = ?", (source_id,))).fetchone()
        if row is None:
            self._raise_not_found("evidence_source", source_id)
        return self._source_from_row(row)

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript("""
        CREATE TABLE IF NOT EXISTS decision_evidence_source (
            id TEXT PRIMARY KEY, product_id TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '', sync_status TEXT NOT NULL,
            sync_cursor TEXT NOT NULL DEFAULT '', last_synced_at TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS decision_evidence (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, external_id TEXT NOT NULL, product_id TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', quote TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL DEFAULT '', source_version TEXT NOT NULL DEFAULT '', source_refs_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(source_id, external_id),
            FOREIGN KEY(source_id) REFERENCES decision_evidence_source(id)
        );
        CREATE INDEX IF NOT EXISTS idx_decision_evidence_product ON decision_evidence(product_id, updated_at DESC);
        """)

    def _source_from_row(self, row: Any) -> EvidenceSource:
        return EvidenceSource(id=row["id"], product_id=row["product_id"], source_type=row["source_type"], source_url=row["source_url"], display_name=row["display_name"], sync_status=row["sync_status"], sync_cursor=row["sync_cursor"], last_synced_at=row["last_synced_at"], last_error=row["last_error"], metadata=self._load_json_object(row["metadata_json"]))

    def _evidence_from_row(self, row: Any) -> EvidenceRecord:
        return EvidenceRecord(id=row["id"], source_id=row["source_id"], external_id=row["external_id"], product_id=row["product_id"], content=row["content"], summary=row["summary"], quote=row["quote"], source_url=row["source_url"], author=row["author"], occurred_at=row["occurred_at"], source_version=row["source_version"], source_refs=self._load_json_list(row["source_refs_json"]), metadata=self._load_json_object(row["metadata_json"]), created_at=row["created_at"], updated_at=row["updated_at"])
