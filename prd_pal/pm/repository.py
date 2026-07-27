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
from .models import (
    Decision,
    ProductContext,
    RoadmapHorizon,
    RoadmapItem,
    TraceLink,
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
                    id, text, source, product_id, product_hint, created_at,
                    source_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    text = excluded.text,
                    source = excluded.source,
                    product_id = excluded.product_id,
                    product_hint = excluded.product_hint,
                    created_at = excluded.created_at,
                    source_refs_json = excluded.source_refs_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    saved.text,
                    saved.source,
                    saved.product_id,
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
                SELECT id, text, source, product_id, product_hint, created_at,
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
        self, *, product_hint: str | None = None, product_id: str | None = None, limit: int = 100
    ) -> RepositoryResult[list[FeedbackItem]]:
        async def operation(connection: Any) -> list[FeedbackItem]:
            await self._ensure_schema(connection)
            limit_value = max(1, int(limit))
            if product_id:
                cursor = await connection.execute(
                    """
                    SELECT id, text, source, product_id, product_hint, created_at,
                           source_refs_json, metadata_json
                    FROM pm_feedback WHERE product_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """, (product_id, limit_value)
                )
            elif product_hint:
                cursor = await connection.execute(
                    """
                    SELECT id, text, source, product_id, product_hint, created_at,
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
                    SELECT id, text, source, product_id, product_hint, created_at,
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
                    id, status, stage, product_id, product_hint,
                    feedback_ids_json, insight_ids_json,
                    opportunity_id, prd_id, review_run_id, error_message,
                    created_at, updated_at, source_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    stage = excluded.stage,
                    product_id = excluded.product_id,
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
                    saved.product_id,
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
                SELECT id, status, stage, product_id, product_hint,
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

    async def list_artifacts(self, artifact_type: ArtifactType) -> RepositoryResult[list[dict[str, Any]]]:
        async def operation(connection: Any) -> list[dict[str, Any]]:
            await self._ensure_schema(connection)
            cursor = await connection.execute("SELECT id, artifact_type, pipeline_id, payload_json, created_at, updated_at FROM pm_artifact WHERE artifact_type = ? ORDER BY updated_at DESC", (artifact_type,))
            rows = await cursor.fetchall()
            return [{"id": str(row["id"]), "artifact_type": str(row["artifact_type"]), "pipeline_id": str(row["pipeline_id"] or ""), "payload": self._load_json_object(row["payload_json"]), "created_at": str(row["created_at"] or ""), "updated_at": str(row["updated_at"] or "")} for row in rows]
        return await self._run("pm_repository.list_artifacts", operation)

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

    async def upsert_product_context(
        self, product: ProductContext
    ) -> RepositoryResult[ProductContext]:
        async def operation(connection: Any) -> ProductContext:
            await self._ensure_schema(connection)
            now = utc_now_iso()
            saved = product.model_copy(
                update={
                    "created_at": str(product.created_at or "").strip() or now,
                    "updated_at": now,
                }
            )
            await connection.execute(
                """
                INSERT INTO pm_product_context (
                    id, name, module, target_users, business_goals_json,
                    constraints_json, summary, created_at, updated_at,
                    source_refs_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    module = excluded.module,
                    target_users = excluded.target_users,
                    business_goals_json = excluded.business_goals_json,
                    constraints_json = excluded.constraints_json,
                    summary = excluded.summary,
                    updated_at = excluded.updated_at,
                    source_refs_json = excluded.source_refs_json,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    saved.name,
                    saved.module,
                    saved.target_users,
                    self._dump_json(saved.business_goals),
                    self._dump_json(saved.constraints),
                    saved.summary,
                    saved.created_at,
                    saved.updated_at,
                    self._dump_json(saved.source_refs),
                    self._dump_json(saved.metadata),
                ),
            )
            await connection.commit()
            return saved

        return await self._run("pm_repository.upsert_product_context", operation)

    async def get_product_context(
        self, product_id: str
    ) -> RepositoryResult[ProductContext]:
        async def operation(connection: Any) -> ProductContext:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT id, name, module, target_users, business_goals_json,
                       constraints_json, summary, created_at, updated_at,
                       source_refs_json, metadata_json
                FROM pm_product_context
                WHERE id = ?
                """,
                (product_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("product_context", product_id)
            return ProductContext(
                id=str(row["id"]),
                name=str(row["name"]),
                module=str(row["module"] or ""),
                target_users=str(row["target_users"] or ""),
                business_goals=[
                    str(item) for item in self._load_json_list(row["business_goals_json"])
                ],
                constraints=[
                    str(item) for item in self._load_json_list(row["constraints_json"])
                ],
                summary=str(row["summary"] or ""),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
                source_refs=[
                    str(item) for item in self._load_json_list(row["source_refs_json"])
                ],
                metadata=self._load_json_object(row["metadata_json"]),
            )

        return await self._run("pm_repository.get_product_context", operation)

    async def list_product_contexts(self) -> RepositoryResult[list[ProductContext]]:
        async def operation(connection: Any) -> list[ProductContext]:
            await self._ensure_schema(connection)
            cursor = await connection.execute("SELECT id, name, module, target_users, business_goals_json, constraints_json, summary, created_at, updated_at, source_refs_json, metadata_json FROM pm_product_context ORDER BY updated_at DESC, id DESC")
            rows = await cursor.fetchall()
            return [self._row_to_product_context(row) for row in rows]
        return await self._run("pm_repository.list_product_contexts", operation)

    async def get_workspace_summary(self, product_id: str) -> RepositoryResult[dict[str, int]]:
        async def operation(connection: Any) -> dict[str, int]:
            await self._ensure_schema(connection)
            async def count(table: str, where: str, args: tuple[str, ...]) -> int:
                cursor = await connection.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", args)
                row = await cursor.fetchone()
                return int(row["total"])
            return {
                "feedback": await count("pm_feedback", "product_id = ?", (product_id,)),
                "roadmap": await count("pm_roadmap_item", "product_id = ?", (product_id,)),
            }
        return await self._run("pm_repository.get_workspace_summary", operation)
    async def upsert_decision(self, decision: Decision) -> RepositoryResult[Decision]:
        async def operation(connection: Any) -> Decision:
            await self._ensure_schema(connection)
            now = utc_now_iso()
            saved = decision.model_copy(
                update={
                    "created_at": str(decision.created_at or "").strip() or now,
                    "updated_at": now,
                }
            )
            await connection.execute(
                """
                INSERT INTO pm_decision (
                    id, product_id, title, status, summary, rationale,
                    evidence_refs_json, source_refs_json, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    product_id = excluded.product_id,
                    title = excluded.title,
                    status = excluded.status,
                    summary = excluded.summary,
                    rationale = excluded.rationale,
                    evidence_refs_json = excluded.evidence_refs_json,
                    source_refs_json = excluded.source_refs_json,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    saved.product_id,
                    saved.title,
                    str(saved.status),
                    saved.summary,
                    saved.rationale,
                    self._dump_json(saved.evidence_refs),
                    self._dump_json(saved.source_refs),
                    saved.created_at,
                    saved.updated_at,
                    self._dump_json(saved.metadata),
                ),
            )
            await connection.commit()
            return saved

        return await self._run("pm_repository.upsert_decision", operation)

    async def get_decision(self, decision_id: str) -> RepositoryResult[Decision]:
        async def operation(connection: Any) -> Decision:
            await self._ensure_schema(connection)
            cursor = await connection.execute("SELECT id, product_id, title, status, summary, rationale, evidence_refs_json, source_refs_json, created_at, updated_at, metadata_json FROM pm_decision WHERE id = ?", (decision_id,))
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("decision", decision_id)
            return self._row_to_decision(row)
        return await self._run("pm_repository.get_decision", operation)

    async def list_decisions(self, *, product_id: str | None = None) -> RepositoryResult[list[Decision]]:
        async def operation(connection: Any) -> list[Decision]:
            await self._ensure_schema(connection)
            query = "SELECT id, product_id, title, status, summary, rationale, evidence_refs_json, source_refs_json, created_at, updated_at, metadata_json FROM pm_decision"
            args: tuple[str, ...] = ()
            if product_id:
                query += " WHERE product_id = ?"
                args = (product_id,)
            cursor = await connection.execute(query + " ORDER BY updated_at DESC", args)
            return [self._row_to_decision(row) for row in await cursor.fetchall()]
        return await self._run("pm_repository.list_decisions", operation)

    async def upsert_roadmap_item(
        self, item: RoadmapItem
    ) -> RepositoryResult[RoadmapItem]:
        async def operation(connection: Any) -> RoadmapItem:
            await self._ensure_schema(connection)
            now = utc_now_iso()
            saved = item.model_copy(
                update={
                    "created_at": str(item.created_at or "").strip() or now,
                    "updated_at": now,
                }
            )
            await connection.execute(
                """
                INSERT INTO pm_roadmap_item (
                    id, product_id, title, horizon, opportunity_id, prd_id, score,
                    summary, source_refs_json, evidence_refs_json,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    product_id = excluded.product_id,
                    title = excluded.title,
                    horizon = excluded.horizon,
                    opportunity_id = excluded.opportunity_id,
                    prd_id = excluded.prd_id,
                    score = excluded.score,
                    summary = excluded.summary,
                    source_refs_json = excluded.source_refs_json,
                    evidence_refs_json = excluded.evidence_refs_json,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    saved.product_id,
                    saved.title,
                    str(saved.horizon),
                    saved.opportunity_id,
                    saved.prd_id,
                    float(saved.score),
                    saved.summary,
                    self._dump_json(saved.source_refs),
                    self._dump_json(saved.evidence_refs),
                    saved.created_at,
                    saved.updated_at,
                    self._dump_json(saved.metadata),
                ),
            )
            await connection.commit()
            return saved

        return await self._run("pm_repository.upsert_roadmap_item", operation)

    async def list_roadmap_items(
        self, *, product_id: str | None = None
    ) -> RepositoryResult[list[RoadmapItem]]:
        async def operation(connection: Any) -> list[RoadmapItem]:
            await self._ensure_schema(connection)
            if product_id:
                cursor = await connection.execute(
                    """
                    SELECT id, product_id, title, horizon, opportunity_id, prd_id, score,
                           summary, source_refs_json, evidence_refs_json,
                           created_at, updated_at, metadata_json
                    FROM pm_roadmap_item
                    WHERE product_id = ?
                    ORDER BY score DESC, updated_at DESC
                    """,
                    (product_id,),
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT id, product_id, title, horizon, opportunity_id, prd_id, score,
                           summary, source_refs_json, evidence_refs_json,
                           created_at, updated_at, metadata_json
                    FROM pm_roadmap_item
                    ORDER BY score DESC, updated_at DESC
                    """
                )
            rows = await cursor.fetchall()
            items: list[RoadmapItem] = []
            for row in rows:
                items.append(
                    RoadmapItem(
                        id=str(row["id"]),
                        product_id=str(row["product_id"] or ""),
                        title=str(row["title"]),
                        horizon=RoadmapHorizon(str(row["horizon"])),
                        opportunity_id=str(row["opportunity_id"] or ""),
                        prd_id=str(row["prd_id"] or ""),
                        score=float(row["score"] or 0),
                        summary=str(row["summary"] or ""),
                        source_refs=[
                            str(item)
                            for item in self._load_json_list(row["source_refs_json"])
                        ],
                        evidence_refs=[
                            str(item)
                            for item in self._load_json_list(row["evidence_refs_json"])
                        ],
                        created_at=str(row["created_at"] or ""),
                        updated_at=str(row["updated_at"] or ""),
                        metadata=self._load_json_object(row["metadata_json"]),
                    )
                )
            return items

        return await self._run("pm_repository.list_roadmap_items", operation)

    async def upsert_launch_review(self, review: Any) -> RepositoryResult[Any]:
        async def operation(connection: Any) -> Any:
            await self._ensure_schema(connection)
            await connection.execute("INSERT INTO pm_launch_review (id, prd_id, product_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json", (review.id, review.prd_id, review.product_id, self._dump_json(review.model_dump(mode="python")), utc_now_iso()))
            await connection.commit()
            return review
        return await self._run("pm_repository.upsert_launch_review", operation)

    async def list_launch_reviews(self, *, product_id: str | None = None) -> RepositoryResult[list[dict[str, Any]]]:
        async def operation(connection: Any) -> list[dict[str, Any]]:
            await self._ensure_schema(connection)
            query = "SELECT payload_json FROM pm_launch_review"
            args: tuple[str, ...] = ()
            if product_id:
                query += " WHERE product_id = ?"; args = (product_id,)
            cursor = await connection.execute(query + " ORDER BY created_at DESC", args)
            return [self._load_json_object(row["payload_json"]) for row in await cursor.fetchall()]
        return await self._run("pm_repository.list_launch_reviews", operation)

    async def upsert_trace_link(self, link: TraceLink) -> RepositoryResult[TraceLink]:
        async def operation(connection: Any) -> TraceLink:
            await self._ensure_schema(connection)
            saved = link.model_copy(
                update={"created_at": str(link.created_at or "").strip() or utc_now_iso()}
            )
            await connection.execute(
                """
                INSERT INTO pm_trace_link (
                    id, source_type, source_id, target_type, target_id,
                    relation, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_id = excluded.source_id,
                    target_type = excluded.target_type,
                    target_id = excluded.target_id,
                    relation = excluded.relation,
                    metadata_json = excluded.metadata_json
                """,
                (
                    saved.id,
                    saved.source_type,
                    saved.source_id,
                    saved.target_type,
                    saved.target_id,
                    saved.relation,
                    saved.created_at,
                    self._dump_json(saved.metadata),
                ),
            )
            await connection.commit()
            return saved

        return await self._run("pm_repository.upsert_trace_link", operation)

    async def list_trace_links(
        self, *, root_id: str
    ) -> RepositoryResult[list[TraceLink]]:
        async def operation(connection: Any) -> list[TraceLink]:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """
                SELECT id, source_type, source_id, target_type, target_id,
                       relation, created_at, metadata_json
                FROM pm_trace_link
                WHERE source_id = ? OR target_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (root_id, root_id),
            )
            rows = await cursor.fetchall()
            return [
                TraceLink(
                    id=str(row["id"]),
                    source_type=str(row["source_type"]),
                    source_id=str(row["source_id"]),
                    target_type=str(row["target_type"]),
                    target_id=str(row["target_id"]),
                    relation=str(row["relation"] or "derived_from"),
                    created_at=str(row["created_at"] or ""),
                    metadata=self._load_json_object(row["metadata_json"]),
                )
                for row in rows
            ]

        return await self._run("pm_repository.list_trace_links", operation)

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pm_feedback (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                product_id TEXT NOT NULL DEFAULT '',
                product_hint TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pm_pipeline_run (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                product_id TEXT NOT NULL DEFAULT '',
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
                ON pm_feedback (product_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_pm_pipeline_status
                ON pm_pipeline_run (status, updated_at);

            CREATE INDEX IF NOT EXISTS idx_pm_artifact_type
                ON pm_artifact (artifact_type, updated_at);

            CREATE INDEX IF NOT EXISTS idx_pm_artifact_pipeline
                ON pm_artifact (pipeline_id, artifact_type);

            CREATE TABLE IF NOT EXISTS pm_product_context (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                module TEXT NOT NULL DEFAULT '',
                target_users TEXT NOT NULL DEFAULT '',
                business_goals_json TEXT NOT NULL DEFAULT '[]',
                constraints_json TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pm_decision (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pm_roadmap_item (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                horizon TEXT NOT NULL,
                opportunity_id TEXT NOT NULL DEFAULT '',
                prd_id TEXT NOT NULL DEFAULT '',
                score REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS pm_launch_review (
                id TEXT PRIMARY KEY,
                prd_id TEXT NOT NULL DEFAULT '',
                product_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pm_trace_link (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'derived_from',
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_pm_trace_source
                ON pm_trace_link (source_id, source_type);

            CREATE INDEX IF NOT EXISTS idx_pm_trace_target
                ON pm_trace_link (target_id, target_type);

            CREATE INDEX IF NOT EXISTS idx_pm_roadmap_product
                ON pm_roadmap_item (product_id, horizon, score);
            """
        )
        for statement in (
            "ALTER TABLE pm_feedback ADD COLUMN product_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE pm_pipeline_run ADD COLUMN product_id TEXT NOT NULL DEFAULT ''",
        ):
            try:
                await connection.execute(statement)
            except Exception:
                pass

    def _row_to_decision(self, row: Any) -> Decision:
        from .models import DecisionStatus
        return Decision(id=str(row["id"]), product_id=str(row["product_id"] or ""), title=str(row["title"]), status=DecisionStatus(str(row["status"])), summary=str(row["summary"] or ""), rationale=str(row["rationale"] or ""), evidence_refs=[str(item) for item in self._load_json_list(row["evidence_refs_json"])], source_refs=[str(item) for item in self._load_json_list(row["source_refs_json"])], created_at=str(row["created_at"] or ""), updated_at=str(row["updated_at"] or ""), metadata=self._load_json_object(row["metadata_json"]))

    def _row_to_product_context(self, row: Any) -> ProductContext:
        return ProductContext(
            id=str(row["id"]), name=str(row["name"]), module=str(row["module"] or ""),
            target_users=str(row["target_users"] or ""),
            business_goals=[str(item) for item in self._load_json_list(row["business_goals_json"])],
            constraints=[str(item) for item in self._load_json_list(row["constraints_json"])],
            summary=str(row["summary"] or ""), created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            source_refs=[str(item) for item in self._load_json_list(row["source_refs_json"])],
            metadata=self._load_json_object(row["metadata_json"]),
        )
    def _row_to_feedback(self, row: Any) -> FeedbackItem:
        return FeedbackItem(
            id=str(row["id"]),
            text=str(row["text"]),
            source=str(row["source"] or ""),
            product_id=str(row["product_id"] or ""),
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
            product_id=str(row["product_id"] or ""),
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
