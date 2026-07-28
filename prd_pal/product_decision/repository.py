"""SQLite repository for sources and evidence; safe for a single-team pilot."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from prd_pal.utils.time import utc_now_iso
from prd_pal.workspace.repository_support import RepositoryResult, SQLiteRepositoryBase

from .models import (
    DecisionAuditEvent,
    DecisionInsight,
    EvidenceRecord,
    EvidenceSource,
    OpportunityCandidate,
    OpportunityCandidateStatus,
    PrdVersion,
    PrdVersionStatus,
    ProductOwnerConfig,
    SourceSyncStatus,
)
from prd_pal.quality_engine.models import QualityAssessment, QualityGateDecision


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
                (id, product_id, source_type, external_id, source_url, display_name, field_mapping_json,
                 sync_status, sync_cursor, last_synced_at, last_error, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                product_id=excluded.product_id, source_type=excluded.source_type,
                external_id=excluded.external_id, source_url=excluded.source_url,
                display_name=excluded.display_name, field_mapping_json=excluded.field_mapping_json,
                sync_status=excluded.sync_status, sync_cursor=excluded.sync_cursor,
                last_synced_at=excluded.last_synced_at, last_error=excluded.last_error,
                metadata_json=excluded.metadata_json""",
                (
                    source.id,
                    source.product_id,
                    str(source.source_type),
                    source.external_id,
                    source.source_url,
                    source.display_name,
                    self._dump_json(source.field_mapping),
                    str(source.sync_status),
                    source.sync_cursor,
                    source.last_synced_at,
                    source.last_error,
                    self._dump_json(source.metadata),
                ),
            )
            await connection.commit()
            return source

        return await self._run("product_decision.upsert_source", operation)

    async def get_source(self, source_id: str) -> RepositoryResult[EvidenceSource]:
        async def operation(connection: Any) -> EvidenceSource:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                "SELECT * FROM decision_evidence_source WHERE id = ?", (source_id,)
            )
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

    async def sync_evidence(
        self,
        source_id: str,
        records: list[EvidenceRecord],
        *,
        cursor: str = "",
    ) -> RepositoryResult[list[EvidenceRecord]]:
        """Idempotently upsert source records by ``(source_id, external_id)``.

        Confirmed evidence keeps its content fields; source content updates still
        advance the watermark for unconfirmed rows.
        """

        async def operation(connection: Any) -> list[EvidenceRecord]:
            await self._ensure_schema(connection)
            source = await self._source_exists(connection, source_id)
            await connection.execute(
                "UPDATE decision_evidence_source SET sync_status = ?, last_error = '' WHERE id = ?",
                (SourceSyncStatus.syncing, source_id),
            )
            saved: list[EvidenceRecord] = []
            for record in records:
                if record.source_id != source_id:
                    self._raise_validation_error(
                        "Evidence record source_id must match the sync source."
                    )
                existing = await (
                    await connection.execute(
                        "SELECT * FROM decision_evidence WHERE source_id = ? AND external_id = ?",
                        (source_id, record.external_id),
                    )
                ).fetchone()
                now = utc_now_iso()
                if existing is not None and bool(existing["confirmed"]):
                    # Preserve confirmed content; only refresh non-content bookkeeping.
                    persisted = self._evidence_from_row(existing).model_copy(
                        update={
                            "updated_at": now,
                            "metadata": {
                                **self._load_json_object(existing["metadata_json"]),
                                **dict(record.metadata or {}),
                                "last_seen_version": record.source_version
                                or self._load_json_object(existing["metadata_json"]).get(
                                    "last_seen_version", ""
                                ),
                            },
                        }
                    )
                    await connection.execute(
                        """UPDATE decision_evidence SET metadata_json = ?, updated_at = ?
                           WHERE source_id = ? AND external_id = ?""",
                        (
                            self._dump_json(persisted.metadata),
                            persisted.updated_at,
                            source_id,
                            record.external_id,
                        ),
                    )
                else:
                    if (
                        existing is not None
                        and str(existing["source_version"] or "")
                        and str(existing["source_version"] or "") == str(record.source_version or "")
                        and str(existing["content"] or "") == str(record.content or "")
                    ):
                        persisted = self._evidence_from_row(existing)
                    else:
                        persisted = record.model_copy(
                            update={
                                "id": (
                                    str(existing["id"])
                                    if existing is not None
                                    else (record.id or f"evidence-{uuid.uuid4().hex[:12]}")
                                ),
                                "product_id": record.product_id or source.product_id,
                                "summary": record.summary or _summarize(record.content),
                                "quote": record.quote or _quote(record.content),
                                "created_at": (
                                    str(existing["created_at"])
                                    if existing is not None
                                    else (record.created_at or now)
                                ),
                                "updated_at": now,
                                "confirmed": False,
                            }
                        )
                        await connection.execute(
                            """INSERT INTO decision_evidence
                            (id, source_id, external_id, product_id, content, summary, quote, source_url,
                             author, occurred_at, source_version, confirmed, source_refs_json, metadata_json,
                             created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(source_id, external_id) DO UPDATE SET
                            content=excluded.content, summary=excluded.summary, quote=excluded.quote,
                            source_url=excluded.source_url, author=excluded.author,
                            occurred_at=excluded.occurred_at, source_version=excluded.source_version,
                            source_refs_json=excluded.source_refs_json, metadata_json=excluded.metadata_json,
                            updated_at=excluded.updated_at
                            WHERE confirmed = 0""",
                            (
                                persisted.id,
                                persisted.source_id,
                                persisted.external_id,
                                persisted.product_id,
                                persisted.content,
                                persisted.summary,
                                persisted.quote,
                                persisted.source_url,
                                persisted.author,
                                persisted.occurred_at,
                                persisted.source_version,
                                1 if persisted.confirmed else 0,
                                self._dump_json(persisted.source_refs),
                                self._dump_json(persisted.metadata),
                                persisted.created_at,
                                persisted.updated_at,
                            ),
                        )
                row = await (
                    await connection.execute(
                        "SELECT * FROM decision_evidence WHERE source_id = ? AND external_id = ?",
                        (source_id, persisted.external_id),
                    )
                ).fetchone()
                saved.append(self._evidence_from_row(row))
            await connection.execute(
                """UPDATE decision_evidence_source
                   SET sync_status = ?, sync_cursor = ?, last_synced_at = ?, last_error = ''
                   WHERE id = ?""",
                (SourceSyncStatus.succeeded, cursor, utc_now_iso(), source_id),
            )
            await connection.commit()
            return saved

        return await self._run("product_decision.sync_evidence", operation)

    async def mark_sync_failed(
        self, source_id: str, message: str
    ) -> RepositoryResult[EvidenceSource]:
        source_result = await self.get_source(source_id)
        if not source_result.ok or source_result.value is None:
            return source_result
        source = source_result.value.model_copy(
            update={"sync_status": SourceSyncStatus.failed, "last_error": message}
        )
        return await self.upsert_source(source)

    async def mark_evidence_confirmed(
        self, evidence_id: str, *, confirmed: bool = True
    ) -> RepositoryResult[EvidenceRecord]:
        async def operation(connection: Any) -> EvidenceRecord:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                "SELECT * FROM decision_evidence WHERE id = ?", (evidence_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                self._raise_not_found("evidence", evidence_id)
            await connection.execute(
                "UPDATE decision_evidence SET confirmed = ?, updated_at = ? WHERE id = ?",
                (1 if confirmed else 0, utc_now_iso(), evidence_id),
            )
            await connection.commit()
            refreshed = await (
                await connection.execute(
                    "SELECT * FROM decision_evidence WHERE id = ?", (evidence_id,)
                )
            ).fetchone()
            return self._evidence_from_row(refreshed)

        return await self._run("product_decision.mark_evidence_confirmed", operation)

    async def list_evidence(
        self, *, product_id: str = "", query: str = "", limit: int = 100
    ) -> RepositoryResult[list[EvidenceRecord]]:
        async def operation(connection: Any) -> list[EvidenceRecord]:
            await self._ensure_schema(connection)
            clauses: list[str] = []
            params: list[Any] = []
            if product_id:
                clauses.append("product_id = ?")
                params.append(product_id)
            if query:
                clauses.append("(content LIKE ? OR summary LIKE ? OR quote LIKE ?)")
                params.extend([f"%{query}%"] * 3)
            statement = (
                "SELECT * FROM decision_evidence"
                + (" WHERE " + " AND ".join(clauses) if clauses else "")
                + " ORDER BY occurred_at DESC, updated_at DESC LIMIT ?"
            )
            params.append(max(1, int(limit)))
            rows = await (await connection.execute(statement, tuple(params))).fetchall()
            return [self._evidence_from_row(row) for row in rows]

        return await self._run("product_decision.list_evidence", operation)

    async def upsert_insight(
        self, insight: DecisionInsight
    ) -> RepositoryResult[DecisionInsight]:
        async def operation(connection: Any) -> DecisionInsight:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_insight
                (id, product_id, title, summary, theme, evidence_refs_json, source_refs_json,
                 source_urls_json, version, audit_id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                product_id=excluded.product_id, title=excluded.title, summary=excluded.summary,
                theme=excluded.theme, evidence_refs_json=excluded.evidence_refs_json,
                source_refs_json=excluded.source_refs_json, source_urls_json=excluded.source_urls_json,
                version=excluded.version, audit_id=excluded.audit_id, updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json""",
                (
                    insight.id,
                    insight.product_id,
                    insight.title,
                    insight.summary,
                    insight.theme,
                    self._dump_json(insight.evidence_refs),
                    self._dump_json(insight.source_refs),
                    self._dump_json(insight.source_urls),
                    insight.version,
                    insight.audit_id,
                    insight.created_at,
                    insight.updated_at,
                    self._dump_json(insight.metadata),
                ),
            )
            await connection.commit()
            return insight

        return await self._run("product_decision.upsert_insight", operation)

    async def list_insights(
        self, *, product_id: str = ""
    ) -> RepositoryResult[list[DecisionInsight]]:
        async def operation(connection: Any) -> list[DecisionInsight]:
            await self._ensure_schema(connection)
            query = "SELECT * FROM decision_insight"
            params: tuple[str, ...] = ()
            if product_id:
                query += " WHERE product_id = ?"
                params = (product_id,)
            query += " ORDER BY updated_at DESC, id"
            rows = await (await connection.execute(query, params)).fetchall()
            return [self._insight_from_row(row) for row in rows]

        return await self._run("product_decision.list_insights", operation)

    async def get_insight(self, insight_id: str) -> RepositoryResult[DecisionInsight]:
        async def operation(connection: Any) -> DecisionInsight:
            await self._ensure_schema(connection)
            row = await (
                await connection.execute(
                    "SELECT * FROM decision_insight WHERE id = ?", (insight_id,)
                )
            ).fetchone()
            if row is None:
                self._raise_not_found("insight", insight_id)
            return self._insight_from_row(row)

        return await self._run("product_decision.get_insight", operation)

    async def upsert_opportunity(
        self, opportunity: OpportunityCandidate
    ) -> RepositoryResult[OpportunityCandidate]:
        async def operation(connection: Any) -> OpportunityCandidate:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_opportunity
                (id, product_id, title, problem, users, value, status, insight_ids_json,
                 evidence_refs_json, source_refs_json, source_urls_json, score, score_method,
                 score_details_json, version, audit_id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                product_id=excluded.product_id, title=excluded.title, problem=excluded.problem,
                users=excluded.users, value=excluded.value, status=excluded.status,
                insight_ids_json=excluded.insight_ids_json, evidence_refs_json=excluded.evidence_refs_json,
                source_refs_json=excluded.source_refs_json, source_urls_json=excluded.source_urls_json,
                score=excluded.score, score_method=excluded.score_method,
                score_details_json=excluded.score_details_json, version=excluded.version,
                audit_id=excluded.audit_id, updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json""",
                (
                    opportunity.id,
                    opportunity.product_id,
                    opportunity.title,
                    opportunity.problem,
                    opportunity.users,
                    opportunity.value,
                    str(opportunity.status),
                    self._dump_json(opportunity.insight_ids),
                    self._dump_json(opportunity.evidence_refs),
                    self._dump_json(opportunity.source_refs),
                    self._dump_json(opportunity.source_urls),
                    float(opportunity.score),
                    opportunity.score_method,
                    self._dump_json(opportunity.score_details),
                    opportunity.version,
                    opportunity.audit_id,
                    opportunity.created_at,
                    opportunity.updated_at,
                    self._dump_json(opportunity.metadata),
                ),
            )
            await connection.commit()
            return opportunity

        return await self._run("product_decision.upsert_opportunity", operation)

    async def get_opportunity(
        self, opportunity_id: str
    ) -> RepositoryResult[OpportunityCandidate]:
        async def operation(connection: Any) -> OpportunityCandidate:
            await self._ensure_schema(connection)
            row = await (
                await connection.execute(
                    "SELECT * FROM decision_opportunity WHERE id = ?", (opportunity_id,)
                )
            ).fetchone()
            if row is None:
                self._raise_not_found("opportunity", opportunity_id)
            return self._opportunity_from_row(row)

        return await self._run("product_decision.get_opportunity", operation)

    async def list_opportunities(
        self, *, product_id: str = ""
    ) -> RepositoryResult[list[OpportunityCandidate]]:
        async def operation(connection: Any) -> list[OpportunityCandidate]:
            await self._ensure_schema(connection)
            query = "SELECT * FROM decision_opportunity"
            params: tuple[str, ...] = ()
            if product_id:
                query += " WHERE product_id = ?"
                params = (product_id,)
            query += " ORDER BY updated_at DESC, id"
            rows = await (await connection.execute(query, params)).fetchall()
            return [self._opportunity_from_row(row) for row in rows]

        return await self._run("product_decision.list_opportunities", operation)

    async def append_audit(
        self, event: DecisionAuditEvent
    ) -> RepositoryResult[DecisionAuditEvent]:
        async def operation(connection: Any) -> DecisionAuditEvent:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_audit_event
                (id, product_id, artifact_type, artifact_id, action, actor, reason,
                 artifact_version, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.product_id,
                    event.artifact_type,
                    event.artifact_id,
                    event.action,
                    event.actor,
                    event.reason,
                    event.artifact_version,
                    event.created_at,
                    self._dump_json(event.metadata),
                ),
            )
            await connection.commit()
            return event

        return await self._run("product_decision.append_audit", operation)

    async def upsert_product_owner(
        self, config: ProductOwnerConfig
    ) -> RepositoryResult[ProductOwnerConfig]:
        async def operation(connection: Any) -> ProductOwnerConfig:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_product_owner
                (product_id, owner_open_id, admin_open_ids_json)
                VALUES (?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                owner_open_id=excluded.owner_open_id,
                admin_open_ids_json=excluded.admin_open_ids_json""",
                (
                    config.product_id,
                    config.owner_open_id,
                    self._dump_json(config.admin_open_ids),
                ),
            )
            await connection.commit()
            return config

        return await self._run("product_decision.upsert_product_owner", operation)

    async def get_product_owner(
        self, product_id: str
    ) -> RepositoryResult[ProductOwnerConfig]:
        async def operation(connection: Any) -> ProductOwnerConfig:
            await self._ensure_schema(connection)
            row = await (
                await connection.execute(
                    "SELECT * FROM decision_product_owner WHERE product_id = ?",
                    (product_id,),
                )
            ).fetchone()
            if row is None:
                self._raise_not_found("product_owner", product_id)
            return ProductOwnerConfig(
                product_id=row["product_id"],
                owner_open_id=row["owner_open_id"],
                admin_open_ids=self._load_json_list(row["admin_open_ids_json"]),
            )

        return await self._run("product_decision.get_product_owner", operation)

    async def insert_prd_version(
        self, version: PrdVersion
    ) -> RepositoryResult[PrdVersion]:
        async def operation(connection: Any) -> PrdVersion:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_prd_version
                (id, prd_id, product_id, opportunity_id, version, title, markdown, status,
                 quality_assessment_id, quality_decision, evidence_refs_json, source_refs_json,
                 source_urls_json, audit_id, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version.id,
                    version.prd_id,
                    version.product_id,
                    version.opportunity_id,
                    version.version,
                    version.title,
                    version.markdown,
                    str(version.status),
                    version.quality_assessment_id,
                    version.quality_decision,
                    self._dump_json(version.evidence_refs),
                    self._dump_json(version.source_refs),
                    self._dump_json(version.source_urls),
                    version.audit_id,
                    version.created_at,
                    version.updated_at,
                    self._dump_json(version.metadata),
                ),
            )
            await connection.commit()
            return version

        return await self._run("product_decision.insert_prd_version", operation)

    async def update_prd_version_gate(
        self, version: PrdVersion
    ) -> RepositoryResult[PrdVersion]:
        """Update gate fields only; never rewrite markdown/title content."""

        async def operation(connection: Any) -> PrdVersion:
            await self._ensure_schema(connection)
            cursor = await connection.execute(
                """UPDATE decision_prd_version
                   SET status = ?, quality_assessment_id = ?, quality_decision = ?,
                       audit_id = ?, updated_at = ?, metadata_json = ?
                   WHERE id = ?""",
                (
                    str(version.status),
                    version.quality_assessment_id,
                    version.quality_decision,
                    version.audit_id,
                    version.updated_at,
                    self._dump_json(version.metadata),
                    version.id,
                ),
            )
            if cursor.rowcount == 0:
                self._raise_not_found("prd_version", version.id)
            await connection.commit()
            row = await (
                await connection.execute(
                    "SELECT * FROM decision_prd_version WHERE id = ?", (version.id,)
                )
            ).fetchone()
            return self._prd_from_row(row)

        return await self._run("product_decision.update_prd_version_gate", operation)

    async def get_prd_version(
        self, prd_version_id: str
    ) -> RepositoryResult[PrdVersion]:
        async def operation(connection: Any) -> PrdVersion:
            await self._ensure_schema(connection)
            row = await (
                await connection.execute(
                    "SELECT * FROM decision_prd_version WHERE id = ?", (prd_version_id,)
                )
            ).fetchone()
            if row is None:
                self._raise_not_found("prd_version", prd_version_id)
            return self._prd_from_row(row)

        return await self._run("product_decision.get_prd_version", operation)

    async def list_prd_versions(
        self, *, prd_id: str = "", product_id: str = ""
    ) -> RepositoryResult[list[PrdVersion]]:
        async def operation(connection: Any) -> list[PrdVersion]:
            await self._ensure_schema(connection)
            clauses: list[str] = []
            params: list[str] = []
            if prd_id:
                clauses.append("prd_id = ?")
                params.append(prd_id)
            if product_id:
                clauses.append("product_id = ?")
                params.append(product_id)
            query = "SELECT * FROM decision_prd_version"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY prd_id, version"
            rows = await (await connection.execute(query, tuple(params))).fetchall()
            return [self._prd_from_row(row) for row in rows]

        return await self._run("product_decision.list_prd_versions", operation)

    async def save_quality_assessment(
        self, assessment: QualityAssessment
    ) -> RepositoryResult[QualityAssessment]:
        async def operation(connection: Any) -> QualityAssessment:
            await self._ensure_schema(connection)
            await connection.execute(
                """INSERT INTO decision_quality_assessment
                (id, prd_version_id, review_run_id, decision, quality_score, findings_json,
                 risks_json, clarification_items_json, evidence_refs_json, policy,
                 created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                decision=excluded.decision, quality_score=excluded.quality_score,
                findings_json=excluded.findings_json, risks_json=excluded.risks_json,
                clarification_items_json=excluded.clarification_items_json,
                metadata_json=excluded.metadata_json""",
                (
                    assessment.id,
                    assessment.prd_version_id,
                    assessment.review_run_id,
                    str(assessment.decision),
                    float(assessment.quality_score),
                    self._dump_json(assessment.findings),
                    self._dump_json(assessment.risks),
                    self._dump_json(assessment.clarification_items),
                    self._dump_json(assessment.evidence_refs),
                    assessment.policy,
                    assessment.created_at,
                    self._dump_json(assessment.metadata),
                ),
            )
            await connection.commit()
            return assessment

        return await self._run("product_decision.save_quality_assessment", operation)

    async def get_quality_assessment(
        self, assessment_id: str
    ) -> RepositoryResult[QualityAssessment]:
        async def operation(connection: Any) -> QualityAssessment:
            await self._ensure_schema(connection)
            row = await (
                await connection.execute(
                    "SELECT * FROM decision_quality_assessment WHERE id = ?",
                    (assessment_id,),
                )
            ).fetchone()
            if row is None:
                self._raise_not_found("quality_assessment", assessment_id)
            return QualityAssessment(
                id=row["id"],
                prd_version_id=row["prd_version_id"],
                review_run_id=row["review_run_id"],
                decision=QualityGateDecision(str(row["decision"])),
                quality_score=float(row["quality_score"] or 0),
                findings=self._load_json_list(row["findings_json"]),
                risks=self._load_json_list(row["risks_json"]),
                clarification_items=self._load_json_list(row["clarification_items_json"]),
                evidence_refs=self._load_json_list(row["evidence_refs_json"]),
                policy=row["policy"],
                created_at=row["created_at"],
                metadata=self._load_json_object(row["metadata_json"]),
            )

        return await self._run("product_decision.get_quality_assessment", operation)

    async def _source_exists(self, connection: Any, source_id: str) -> EvidenceSource:
        row = await (
            await connection.execute(
                "SELECT * FROM decision_evidence_source WHERE id = ?", (source_id,)
            )
        ).fetchone()
        if row is None:
            self._raise_not_found("evidence_source", source_id)
        return self._source_from_row(row)

    async def _ensure_schema(self, connection: Any) -> None:
        await connection.executescript(
            """
        CREATE TABLE IF NOT EXISTS decision_evidence_source (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL,
            external_id TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            field_mapping_json TEXT NOT NULL DEFAULT '{}',
            sync_status TEXT NOT NULL,
            sync_cursor TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS decision_evidence (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            product_id TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            quote TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL DEFAULT '',
            source_version TEXT NOT NULL DEFAULT '',
            confirmed INTEGER NOT NULL DEFAULT 0,
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_id, external_id),
            FOREIGN KEY(source_id) REFERENCES decision_evidence_source(id)
        );
        CREATE INDEX IF NOT EXISTS idx_decision_evidence_product
            ON decision_evidence(product_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS decision_insight (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            theme TEXT NOT NULL DEFAULT '',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            source_urls_json TEXT NOT NULL DEFAULT '[]',
            version INTEGER NOT NULL DEFAULT 1,
            audit_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS decision_opportunity (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            title TEXT NOT NULL,
            problem TEXT NOT NULL DEFAULT '',
            users TEXT NOT NULL DEFAULT '',
            value TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            insight_ids_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            source_urls_json TEXT NOT NULL DEFAULT '[]',
            score REAL NOT NULL DEFAULT 0,
            score_method TEXT NOT NULL DEFAULT '',
            score_details_json TEXT NOT NULL DEFAULT '{}',
            version INTEGER NOT NULL DEFAULT 1,
            audit_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS decision_audit_event (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL DEFAULT '',
            artifact_type TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            artifact_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_decision_insight_product
            ON decision_insight(product_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_decision_opportunity_product
            ON decision_opportunity(product_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS decision_product_owner (
            product_id TEXT PRIMARY KEY,
            owner_open_id TEXT NOT NULL,
            admin_open_ids_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS decision_prd_version (
            id TEXT PRIMARY KEY,
            prd_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL,
            title TEXT NOT NULL,
            markdown TEXT NOT NULL,
            status TEXT NOT NULL,
            quality_assessment_id TEXT NOT NULL DEFAULT '',
            quality_decision TEXT NOT NULL DEFAULT '',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            source_refs_json TEXT NOT NULL DEFAULT '[]',
            source_urls_json TEXT NOT NULL DEFAULT '[]',
            audit_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(prd_id, version)
        );
        CREATE TABLE IF NOT EXISTS decision_quality_assessment (
            id TEXT PRIMARY KEY,
            prd_version_id TEXT NOT NULL,
            review_run_id TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL,
            quality_score REAL NOT NULL DEFAULT 0,
            findings_json TEXT NOT NULL DEFAULT '[]',
            risks_json TEXT NOT NULL DEFAULT '[]',
            clarification_items_json TEXT NOT NULL DEFAULT '[]',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            policy TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_decision_prd_product
            ON decision_prd_version(product_id, updated_at DESC);
        """
        )
        await self._ensure_column(
            connection, "decision_evidence_source", "external_id", "TEXT NOT NULL DEFAULT ''"
        )
        await self._ensure_column(
            connection,
            "decision_evidence_source",
            "field_mapping_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        await self._ensure_column(
            connection, "decision_evidence", "confirmed", "INTEGER NOT NULL DEFAULT 0"
        )

    async def _ensure_column(
        self, connection: Any, table: str, column: str, ddl: str
    ) -> None:
        rows = await (await connection.execute(f"PRAGMA table_info({table})")).fetchall()
        names = {str(row["name"] if hasattr(row, "keys") else row[1]) for row in rows}
        if column not in names:
            await connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _source_from_row(self, row: Any) -> EvidenceSource:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        field_mapping_raw = row["field_mapping_json"] if "field_mapping_json" in keys else "{}"
        external_id = row["external_id"] if "external_id" in keys else ""
        return EvidenceSource(
            id=row["id"],
            product_id=row["product_id"],
            source_type=row["source_type"],
            external_id=str(external_id or ""),
            source_url=row["source_url"],
            display_name=row["display_name"],
            field_mapping=self._load_json_object(field_mapping_raw),
            sync_status=row["sync_status"],
            sync_cursor=row["sync_cursor"],
            last_synced_at=row["last_synced_at"],
            last_error=row["last_error"],
            metadata=self._load_json_object(row["metadata_json"]),
        )

    def _evidence_from_row(self, row: Any) -> EvidenceRecord:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        confirmed = bool(row["confirmed"]) if "confirmed" in keys else False
        return EvidenceRecord(
            id=row["id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            product_id=row["product_id"],
            content=row["content"],
            summary=row["summary"],
            quote=row["quote"],
            source_url=row["source_url"],
            author=row["author"],
            occurred_at=row["occurred_at"],
            source_version=row["source_version"],
            confirmed=confirmed,
            source_refs=self._load_json_list(row["source_refs_json"]),
            metadata=self._load_json_object(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _insight_from_row(self, row: Any) -> DecisionInsight:
        return DecisionInsight(
            id=row["id"],
            product_id=row["product_id"],
            title=row["title"],
            summary=row["summary"],
            theme=row["theme"],
            evidence_refs=self._load_json_list(row["evidence_refs_json"]),
            source_refs=self._load_json_list(row["source_refs_json"]),
            source_urls=self._load_json_list(row["source_urls_json"]),
            version=int(row["version"] or 1),
            audit_id=row["audit_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=self._load_json_object(row["metadata_json"]),
        )

    def _opportunity_from_row(self, row: Any) -> OpportunityCandidate:
        return OpportunityCandidate(
            id=row["id"],
            product_id=row["product_id"],
            title=row["title"],
            problem=row["problem"],
            users=row["users"],
            value=row["value"],
            status=OpportunityCandidateStatus(str(row["status"])),
            insight_ids=self._load_json_list(row["insight_ids_json"]),
            evidence_refs=self._load_json_list(row["evidence_refs_json"]),
            source_refs=self._load_json_list(row["source_refs_json"]),
            source_urls=self._load_json_list(row["source_urls_json"]),
            score=float(row["score"] or 0),
            score_method=str(row["score_method"] or ""),
            score_details={
                str(key): float(value)
                for key, value in self._load_json_object(row["score_details_json"]).items()
                if isinstance(value, (int, float))
            },
            version=int(row["version"] or 1),
            audit_id=row["audit_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=self._load_json_object(row["metadata_json"]),
        )

    def _prd_from_row(self, row: Any) -> PrdVersion:
        return PrdVersion(
            id=row["id"],
            prd_id=row["prd_id"],
            product_id=row["product_id"],
            opportunity_id=row["opportunity_id"],
            version=int(row["version"] or 1),
            title=row["title"],
            markdown=row["markdown"],
            status=PrdVersionStatus(str(row["status"])),
            quality_assessment_id=row["quality_assessment_id"],
            quality_decision=row["quality_decision"],
            evidence_refs=self._load_json_list(row["evidence_refs_json"]),
            source_refs=self._load_json_list(row["source_refs_json"]),
            source_urls=self._load_json_list(row["source_urls_json"]),
            audit_id=row["audit_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=self._load_json_object(row["metadata_json"]),
        )


def _summarize(content: str, *, limit: int = 160) -> str:
    normalized = " ".join(str(content or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _quote(content: str, *, limit: int = 280) -> str:
    normalized = str(content or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"
