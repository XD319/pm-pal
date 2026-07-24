"""SQLite-backed repository for PM Agent domain objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from prd_pal.utils.time import utc_now_iso
from prd_pal.workspace.repository_support import RepositoryResult, SQLiteRepositoryBase

from .schemas import (
    FeedbackItem,
    InsightCluster,
    OpportunityBrief,
    PRDDraft,
    PipelineRunRecord,
    PipelineStage,
    PipelineStatus,
)

ArtifactType = Literal["insight", "opportunity", "prd"]


class PmRepository(SQLiteRepositoryBase):
    """Persist feedback, pipeline runs, and typed PM artifacts in data/pm.sqlite3."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)

    async def initialize(self) -> RepositoryResult[bool]:
        async def operation(connection: Any) -> bool:
            await self._ensure_schema(connection)
            await connection.commit()
            return True

        return await self._run("pm_repository.initialize", operation)

    async def upsert_feedback(
        self, item: FeedbackItem
    ) -> RepositoryResult[FeedbackItem]:
        async def operation(connection: Any) -> FeedbackItem:
            await self._ensure_schema(connection)
            created_at = str(item.created_at or "").strip() or utc_now_iso()
            saved = item.model_copy(update={"created_at": created_at})
            await connection.execute(
                """
                INSERT INTO pm_feedback (
                    id, text, source, product_hint, created_at,
                    source_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    text = excluded.text,
                    source = excluded.source,
                    product_hint = excluded.product_hint,
                    created_at = excluded.created_at,
                    source_refs_json = excluded.source_refs_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    saved.text,
                    saved.source,
                    saved.product_hint,
                    saved.created_at,
                    self._dump_json(saved.source_refs),
                    self._dump_json(saved.metadata),
                ),
            )
            await connection.commit()
            return saved

        return await self._run("pm_repository.upsert_feedback", operation)

    async def get_feedback(self, feedback_id: str) -> RepositoryResult[FeedbackItem]:
        async def operation(connection: Any) -> FeedbackItem:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT id, text, source, product_hint, created_at,
                       source_refs_json, metadata_json
                FROM pm_feedback
                WHERE id = ?
                """,
                (feedback_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("feedback", feedback_id)
            return self._row_to_feedback(row)

        return await self._run("pm_repository.get_feedback", operation)

    async def list_feedback(
        self, *, product_hint: str | None = None, limit: int = 100
    ) -> RepositoryResult[list[FeedbackItem]]:
        async def operation(connection: Any) -> list[FeedbackItem]:
            await self._ensure_schema(connection)
            limit_value = max(1, int(limit))
            if product_hint:
                cursor = await connection.execute(
                    """
                    SELECT id, text, source, product_hint, created_at,
                           source_refs_json, metadata_json
                    FROM pm_feedback
                    WHERE product_hint = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (product_hint, limit_value),
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT id, text, source, product_hint, created_at,
                           source_refs_json, metadata_json
                    FROM pm_feedback
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (limit_value,),
                )
            rows = await cursor.fetchall()
            return [self._row_to_feedback(row) for row in rows]

        return await self._run("pm_repository.list_feedback", operation)

    async def upsert_pipeline_run(
        self, record: PipelineRunRecord
    ) -> RepositoryResult[PipelineRunRecord]:
        async def operation(connection: Any) -> PipelineRunRecord:
            await self._ensure_schema(connection)
            now = utc_now_iso()
            created_at = str(record.created_at or "").strip() or now
            updated_at = now
            saved = record.model_copy(
                update={"created_at": created_at, "updated_at": updated_at}
            )
            await connection.execute(
                """
                INSERT INTO pm_pipeline_run (
                    id, status, stage, product_hint,
                    feedback_ids_json, insight_ids_json,
                    opportunity_id, prd_id, review_run_id, error_message,
                    created_at, updated_at, source_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    stage = excluded.stage,
                    product_hint = excluded.product_hint,
                    feedback_ids_json = excluded.feedback_ids_json,
                    insight_ids_json = excluded.insight_ids_json,
                    opportunity_id = excluded.opportunity_id,
                    prd_id = excluded.prd_id,
                    review_run_id = excluded.review_run_id,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at,
                    source_refs_json = excluded.source_refs_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    str(saved.status),
                    str(saved.stage),
                    saved.product_hint,
                    self._dump_json(saved.feedback_ids),
                    self._dump_json(saved.insight_ids),
                    saved.opportunity_id,
                    saved.prd_id,
                    saved.review_run_id,
                    saved.error_message,
                    saved.created_at,
                    saved.updated_at,
                    self._dump_json(saved.source_refs),
                    self._dump_json(saved.metadata),
                ),
            )
            await connection.commit()
            return saved

        return await self._run("pm_repository.upsert_pipeline_run", operation)

    async def get_pipeline_run(
        self, pipeline_id: str
    ) -> RepositoryResult[PipelineRunRecord]:
        async def operation(connection: Any) -> PipelineRunRecord:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT id, status, stage, product_hint,
                       feedback_ids_json, insight_ids_json,
                       opportunity_id, prd_id, review_run_id, error_message,
                       created_at, updated_at, source_refs_json, metadata_json
                FROM pm_pipeline_run
                WHERE id = ?
                """,
                (pipeline_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("pipeline_run", pipeline_id)
            return self._row_to_pipeline_run(row)

        return await self._run("pm_repository.get_pipeline_run", operation)

    async def upsert_artifact(
        self,
        *,
        artifact_type: ArtifactType,
        artifact_id: str,
        payload: InsightCluster | OpportunityBrief | PRDDraft,
        pipeline_id: str = "",
    ) -> RepositoryResult[dict[str, Any]]:
        async def operation(connection: Any) -> dict[str, Any]:
            await self._ensure_schema(connection)
            now = utc_now_iso()
            payload_json = self._dump_json(payload.model_dump(mode="python"))
            await connection.execute(
                """
                INSERT INTO pm_artifact (
                    id, artifact_type, pipeline_id, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    artifact_type = excluded.artifact_type,
                    pipeline_id = excluded.pipeline_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (artifact_id, artifact_type, pipeline_id, payload_json, now, now),
            )
            await connection.commit()
            return {
                "id": artifact_id,
                "artifact_type": artifact_type,
                "pipeline_id": pipeline_id,
                "payload": payload.model_dump(mode="python"),
                "updated_at": now,
            }

        return await self._run("pm_repository.upsert_artifact", operation)

    async def get_artifact(
        self, artifact_id: str
    ) -> RepositoryResult[dict[str, Any]]:
        async def operation(connection: Any) -> dict[str, Any]:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT id, artifact_type, pipeline_id, payload_json, created_at, updated_at
                FROM pm_artifact
                WHERE id = ?
                """,
                (artifact_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("artifact", artifact_id)
            return {
                "id": str(row["id"]),
                "artifact_type": str(row["artifact_type"]),
                "pipeline_id": str(row["pipeline_id"] or ""),
                "payload": self._load_json_object(row["payload_json"]),
                "created_at": str(row["created_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }

        return await self._run("pm_repository.get_artifact", operation)

    async def get_insight(self, insight_id: str) -> RepositoryResult[InsightCluster]:
        from prd_pal.workspace.repository_support import RepositoryErrorCode

        result = await self.get_artifact(insight_id)
        if not result.ok or result.value is None:
            return RepositoryResult(ok=False, error=result.error)
        if result.value.get("artifact_type") != "insight":
            return RepositoryResult.failure(
                RepositoryErrorCode.validation_error,
                f"artifact {insight_id} is not an insight",
            )
        return RepositoryResult.success(
            InsightCluster.model_validate(result.value["payload"])
        )

    async def get_opportunity(
        self, opportunity_id: str
    ) -> RepositoryResult[OpportunityBrief]:
        result = await self.get_artifact(opportunity_id)
        if not result.ok or result.value is None:
            return RepositoryResult(ok=False, error=result.error)
        if result.value.get("artifact_type") != "opportunity":
            from prd_pal.workspace.repository_support import RepositoryErrorCode

            return RepositoryResult.failure(
                RepositoryErrorCode.validation_error,
                f"artifact {opportunity_id} is not an opportunity",
            )
        return RepositoryResult.success(
            OpportunityBrief.model_validate(result.value["payload"])
        )

    async def get_prd(self, prd_id: str) -> RepositoryResult[PRDDraft]:
        result = await self.get_artifact(prd_id)
        if not result.ok or result.value is None:
            return RepositoryResult(ok=False, error=result.error)
        if result.value.get("artifact_type") != "prd":
            from prd_pal.workspace.repository_support import RepositoryErrorCode

            return RepositoryResult.failure(
                RepositoryErrorCode.validation_error,
                f"artifact {prd_id} is not a prd",
            )
        return RepositoryResult.success(
            PRDDraft.model_validate(result.value["payload"])
        )

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pm_feedback (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                product_hint TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pm_pipeline_run (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                product_hint TEXT NOT NULL DEFAULT '',
                feedback_ids_json TEXT NOT NULL DEFAULT '[]',
                insight_ids_json TEXT NOT NULL DEFAULT '[]',
                opportunity_id TEXT NOT NULL DEFAULT '',
                prd_id TEXT NOT NULL DEFAULT '',
                review_run_id TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pm_artifact (
                id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                pipeline_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_pm_feedback_product
                ON pm_feedback (product_hint, created_at);

            CREATE INDEX IF NOT EXISTS idx_pm_pipeline_status
                ON pm_pipeline_run (status, updated_at);

            CREATE INDEX IF NOT EXISTS idx_pm_artifact_type
                ON pm_artifact (artifact_type, updated_at);

            CREATE INDEX IF NOT EXISTS idx_pm_artifact_pipeline
                ON pm_artifact (pipeline_id, artifact_type);
            """
        )

    def _row_to_feedback(self, row: Any) -> FeedbackItem:
        return FeedbackItem(
            id=str(row["id"]),
            text=str(row["text"]),
            source=str(row["source"] or ""),
            product_hint=str(row["product_hint"] or ""),
            created_at=str(row["created_at"] or ""),
            source_refs=[
                str(item) for item in self._load_json_list(row["source_refs_json"])
            ],
            metadata=self._load_json_object(row["metadata_json"]),
        )

    def _row_to_pipeline_run(self, row: Any) -> PipelineRunRecord:
        return PipelineRunRecord(
            id=str(row["id"]),
            status=PipelineStatus(str(row["status"])),
            stage=PipelineStage(str(row["stage"])),
            product_hint=str(row["product_hint"] or ""),
            feedback_ids=[
                str(item) for item in self._load_json_list(row["feedback_ids_json"])
            ],
            insight_ids=[
                str(item) for item in self._load_json_list(row["insight_ids_json"])
            ],
            opportunity_id=str(row["opportunity_id"] or ""),
            prd_id=str(row["prd_id"] or ""),
            review_run_id=str(row["review_run_id"] or ""),
            error_message=str(row["error_message"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            source_refs=[
                str(item) for item in self._load_json_list(row["source_refs_json"])
            ],
            metadata=self._load_json_object(row["metadata_json"]),
        )
