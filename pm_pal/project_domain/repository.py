"""SQLite persistence for project-domain PM entities inside project_space.sqlite3. :-)"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from pm_pal.utils.time import utc_now_iso

from .models import (
    DeliveryRecord,
    DeliveryStatus,
    EvidenceRecord,
    EvidenceSource,
    InsightRecord,
    OpportunityRecord,
    OpportunityStatus,
    PrdStatus,
    PrdVersionRecord,
    ProjectAuditEvent,
    SourceSyncStatus,
    TraceLink,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pm_evidence_source (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    external_id TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    sync_status TEXT NOT NULL DEFAULT 'idle',
    sync_cursor TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pm_evidence_source_project
    ON pm_evidence_source(project_id);

CREATE TABLE IF NOT EXISTS pm_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
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
    UNIQUE(source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_pm_evidence_project
    ON pm_evidence(project_id, confirmed, updated_at DESC);

CREATE TABLE IF NOT EXISTS pm_insight (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_pm_insight_project ON pm_insight(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS pm_opportunity (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    problem TEXT NOT NULL DEFAULT '',
    users TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',
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
CREATE INDEX IF NOT EXISTS idx_pm_opportunity_project
    ON pm_opportunity(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS pm_prd_version (
    id TEXT PRIMARY KEY,
    prd_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    quality_assessment_id TEXT NOT NULL DEFAULT '',
    quality_decision TEXT NOT NULL DEFAULT '',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    source_urls_json TEXT NOT NULL DEFAULT '[]',
    project_source_id TEXT NOT NULL DEFAULT '',
    audit_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(prd_id, version)
);
CREATE INDEX IF NOT EXISTS idx_pm_prd_project ON pm_prd_version(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS pm_delivery (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    prd_version_id TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    external_url TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    field_payload_json TEXT NOT NULL DEFAULT '{}',
    audit_id TEXT NOT NULL DEFAULT '',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pm_delivery_project ON pm_delivery(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS pm_audit_event (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    artifact_type TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    artifact_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pm_audit_project ON pm_audit_event(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pm_trace_link (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    from_type TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_type TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'derived_from',
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pm_trace_project ON pm_trace_link(project_id);

CREATE TABLE IF NOT EXISTS agent_conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id)
);
CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    requires_confirmation INTEGER NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT,
    command_id TEXT,
    FOREIGN KEY(conversation_id) REFERENCES agent_conversations(id)
);
CREATE TABLE IF NOT EXISTS agent_commands (
    command_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    executed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_agent_commands_project
    ON agent_commands(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_conversations_project
    ON agent_conversations(project_id, updated_at DESC);
"""


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _dump_list(value: Any) -> str:
    return json.dumps(list(value or []), ensure_ascii=False)


def _load_obj(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _load_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if str(item or "").strip()]


def _summarize(content: str) -> str:
    text = " ".join(str(content or "").split())
    return text[:160] + ("…" if len(text) > 160 else "")


def _quote(content: str) -> str:
    text = " ".join(str(content or "").split())
    return text[:240]


class ProjectDomainRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # Drop legacy product_id column aliases if tables were created earlier. :-)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_project(self, project_id: str) -> None:
        project_id = str(project_id or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"project not found: {project_id}")

    # ---- evidence sources -------------------------------------------------

    def upsert_source(self, source: EvidenceSource) -> EvidenceSource:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pm_evidence_source
                (id, project_id, source_type, external_id, source_url, display_name,
                 sync_status, sync_cursor, last_synced_at, last_error, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                project_id=excluded.project_id, source_type=excluded.source_type,
                external_id=excluded.external_id, source_url=excluded.source_url,
                display_name=excluded.display_name, sync_status=excluded.sync_status,
                sync_cursor=excluded.sync_cursor, last_synced_at=excluded.last_synced_at,
                last_error=excluded.last_error, metadata_json=excluded.metadata_json""",
                (
                    source.id,
                    source.project_id,
                    str(source.source_type),
                    source.external_id,
                    source.source_url,
                    source.display_name,
                    str(source.sync_status),
                    source.sync_cursor,
                    source.last_synced_at,
                    source.last_error,
                    _dump(source.metadata),
                ),
            )
            conn.commit()
        return source

    def list_sources(self, project_id: str) -> list[EvidenceSource]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_evidence_source WHERE project_id=? ORDER BY display_name, id",
                (project_id,),
            ).fetchall()
        return [self._source_from_row(row) for row in rows]

    def get_source(self, source_id: str) -> EvidenceSource | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pm_evidence_source WHERE id=?", (source_id,)
            ).fetchone()
        return self._source_from_row(row) if row else None

    def sync_evidence(
        self,
        source_id: str,
        records: list[EvidenceRecord],
        *,
        cursor: str = "",
    ) -> list[EvidenceRecord]:
        with self._connect() as conn:
            source_row = conn.execute(
                "SELECT * FROM pm_evidence_source WHERE id=?", (source_id,)
            ).fetchone()
            if source_row is None:
                raise LookupError(f"evidence source not found: {source_id}")
            project_id = str(source_row["project_id"])
            conn.execute(
                "UPDATE pm_evidence_source SET sync_status=?, last_error='' WHERE id=?",
                (SourceSyncStatus.syncing, source_id),
            )
            saved: list[EvidenceRecord] = []
            for record in records:
                existing = conn.execute(
                    "SELECT * FROM pm_evidence WHERE source_id=? AND external_id=?",
                    (source_id, record.external_id),
                ).fetchone()
                now = utc_now_iso()
                if existing is not None and bool(existing["confirmed"]):
                    metadata = {
                        **_load_obj(existing["metadata_json"]),
                        **dict(record.metadata or {}),
                    }
                    conn.execute(
                        "UPDATE pm_evidence SET metadata_json=?, updated_at=? "
                        "WHERE source_id=? AND external_id=?",
                        (_dump(metadata), now, source_id, record.external_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM pm_evidence WHERE source_id=? AND external_id=?",
                        (source_id, record.external_id),
                    ).fetchone()
                    saved.append(self._evidence_from_row(row))
                    continue
                evidence_id = (
                    str(existing["id"])
                    if existing is not None
                    else (record.id or f"evidence-{uuid.uuid4().hex[:12]}")
                )
                created_at = (
                    str(existing["created_at"])
                    if existing is not None
                    else (record.created_at or now)
                )
                conn.execute(
                    """INSERT INTO pm_evidence
                    (id, project_id, source_id, external_id, content, summary, quote,
                     source_url, author, occurred_at, source_version, confirmed,
                     source_refs_json, metadata_json, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET
                    content=excluded.content, summary=excluded.summary, quote=excluded.quote,
                    source_url=excluded.source_url, author=excluded.author,
                    occurred_at=excluded.occurred_at, source_version=excluded.source_version,
                    source_refs_json=excluded.source_refs_json, metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                    WHERE confirmed = 0""",
                    (
                        evidence_id,
                        project_id,
                        source_id,
                        record.external_id,
                        record.content,
                        record.summary or _summarize(record.content),
                        record.quote or _quote(record.content),
                        record.source_url,
                        record.author,
                        record.occurred_at,
                        record.source_version,
                        0,
                        _dump_list(record.source_refs),
                        _dump(record.metadata),
                        created_at,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM pm_evidence WHERE source_id=? AND external_id=?",
                    (source_id, record.external_id),
                ).fetchone()
                saved.append(self._evidence_from_row(row))
            conn.execute(
                """UPDATE pm_evidence_source
                   SET sync_status=?, sync_cursor=?, last_synced_at=?, last_error=''
                   WHERE id=?""",
                (SourceSyncStatus.succeeded, cursor, utc_now_iso(), source_id),
            )
            conn.commit()
        return saved

    def list_evidence(
        self,
        project_id: str,
        *,
        query: str = "",
        confirmed_only: bool = False,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        sql = "SELECT * FROM pm_evidence WHERE project_id=?"
        params: list[Any] = [project_id]
        if confirmed_only:
            sql += " AND confirmed=1"
        if query.strip():
            sql += " AND (content LIKE ? OR summary LIKE ? OR author LIKE ?)"
            needle = f"%{query.strip()}%"
            params.extend([needle, needle, needle])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 1000)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pm_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
        return self._evidence_from_row(row) if row else None

    def confirm_evidence(
        self, evidence_id: str, *, confirmed: bool = True
    ) -> EvidenceRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pm_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"evidence not found: {evidence_id}")
            now = utc_now_iso()
            conn.execute(
                "UPDATE pm_evidence SET confirmed=?, updated_at=? WHERE id=?",
                (1 if confirmed else 0, now, evidence_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM pm_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
        return self._evidence_from_row(row)

    # ---- insights / opportunities / prd / delivery ------------------------

    def upsert_insight(self, insight: InsightRecord) -> InsightRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pm_insight
                (id, project_id, title, summary, theme, evidence_refs_json, source_refs_json,
                 source_urls_json, version, audit_id, created_at, updated_at, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, summary=excluded.summary, theme=excluded.theme,
                evidence_refs_json=excluded.evidence_refs_json,
                source_refs_json=excluded.source_refs_json,
                source_urls_json=excluded.source_urls_json, version=excluded.version,
                audit_id=excluded.audit_id, updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json""",
                (
                    insight.id,
                    insight.project_id,
                    insight.title,
                    insight.summary,
                    insight.theme,
                    _dump_list(insight.evidence_refs),
                    _dump_list(insight.source_refs),
                    _dump_list(insight.source_urls),
                    insight.version,
                    insight.audit_id,
                    insight.created_at,
                    insight.updated_at,
                    _dump(insight.metadata),
                ),
            )
            conn.commit()
        return insight

    def list_insights(self, project_id: str) -> list[InsightRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_insight WHERE project_id=? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [self._insight_from_row(row) for row in rows]

    def get_insight(self, insight_id: str) -> InsightRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pm_insight WHERE id=?", (insight_id,)
            ).fetchone()
        return self._insight_from_row(row) if row else None

    def upsert_opportunity(self, opportunity: OpportunityRecord) -> OpportunityRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pm_opportunity
                (id, project_id, title, problem, users, value, status, insight_ids_json,
                 evidence_refs_json, source_refs_json, source_urls_json, score, score_method,
                 score_details_json, version, audit_id, created_at, updated_at, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, problem=excluded.problem, users=excluded.users,
                value=excluded.value, status=excluded.status,
                insight_ids_json=excluded.insight_ids_json,
                evidence_refs_json=excluded.evidence_refs_json,
                source_refs_json=excluded.source_refs_json,
                source_urls_json=excluded.source_urls_json, score=excluded.score,
                score_method=excluded.score_method, score_details_json=excluded.score_details_json,
                version=excluded.version, audit_id=excluded.audit_id,
                updated_at=excluded.updated_at, metadata_json=excluded.metadata_json""",
                (
                    opportunity.id,
                    opportunity.project_id,
                    opportunity.title,
                    opportunity.problem,
                    opportunity.users,
                    opportunity.value,
                    str(opportunity.status),
                    _dump_list(opportunity.insight_ids),
                    _dump_list(opportunity.evidence_refs),
                    _dump_list(opportunity.source_refs),
                    _dump_list(opportunity.source_urls),
                    opportunity.score,
                    opportunity.score_method,
                    _dump(opportunity.score_details),
                    opportunity.version,
                    opportunity.audit_id,
                    opportunity.created_at,
                    opportunity.updated_at,
                    _dump(opportunity.metadata),
                ),
            )
            conn.commit()
        return opportunity

    def list_opportunities(self, project_id: str) -> list[OpportunityRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_opportunity WHERE project_id=? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [self._opportunity_from_row(row) for row in rows]

    def get_opportunity(self, opportunity_id: str) -> OpportunityRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pm_opportunity WHERE id=?", (opportunity_id,)
            ).fetchone()
        return self._opportunity_from_row(row) if row else None

    def insert_prd_version(self, version: PrdVersionRecord) -> PrdVersionRecord:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pm_prd_version
                (id, prd_id, project_id, opportunity_id, version, title, markdown, status,
                 quality_assessment_id, quality_decision, evidence_refs_json, source_refs_json,
                 source_urls_json, project_source_id, audit_id, created_at, updated_at, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    version.id,
                    version.prd_id,
                    version.project_id,
                    version.opportunity_id,
                    version.version,
                    version.title,
                    version.markdown,
                    str(version.status),
                    version.quality_assessment_id,
                    version.quality_decision,
                    _dump_list(version.evidence_refs),
                    _dump_list(version.source_refs),
                    _dump_list(version.source_urls),
                    version.project_source_id,
                    version.audit_id,
                    version.created_at,
                    version.updated_at,
                    _dump(version.metadata),
                ),
            )
            conn.commit()
        return version

    def update_prd_version(self, version: PrdVersionRecord) -> PrdVersionRecord:
        with self._connect() as conn:
            conn.execute(
                """UPDATE pm_prd_version SET
                title=?, markdown=?, status=?, quality_assessment_id=?, quality_decision=?,
                evidence_refs_json=?, source_refs_json=?, source_urls_json=?,
                project_source_id=?, audit_id=?, updated_at=?, metadata_json=?
                WHERE id=?""",
                (
                    version.title,
                    version.markdown,
                    str(version.status),
                    version.quality_assessment_id,
                    version.quality_decision,
                    _dump_list(version.evidence_refs),
                    _dump_list(version.source_refs),
                    _dump_list(version.source_urls),
                    version.project_source_id,
                    version.audit_id,
                    version.updated_at,
                    _dump(version.metadata),
                    version.id,
                ),
            )
            conn.commit()
        return version

    def list_prd_versions(self, project_id: str) -> list[PrdVersionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_prd_version WHERE project_id=? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [self._prd_from_row(row) for row in rows]

    def get_prd_version(self, prd_version_id: str) -> PrdVersionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pm_prd_version WHERE id=?", (prd_version_id,)
            ).fetchone()
        return self._prd_from_row(row) if row else None

    def upsert_delivery(self, delivery: DeliveryRecord) -> DeliveryRecord:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM pm_delivery WHERE idempotency_key=?",
                (delivery.idempotency_key,),
            ).fetchone()
            if existing is not None:
                return self._delivery_from_row(existing)
            conn.execute(
                """INSERT INTO pm_delivery
                (id, project_id, prd_version_id, target_kind, idempotency_key, status,
                 external_url, external_id, failure_reason, field_payload_json, audit_id,
                 evidence_refs_json, created_at, updated_at, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    delivery.id,
                    delivery.project_id,
                    delivery.prd_version_id,
                    delivery.target_kind,
                    delivery.idempotency_key,
                    str(delivery.status),
                    delivery.external_url,
                    delivery.external_id,
                    delivery.failure_reason,
                    _dump(delivery.field_payload),
                    delivery.audit_id,
                    _dump_list(delivery.evidence_refs),
                    delivery.created_at,
                    delivery.updated_at,
                    _dump(delivery.metadata),
                ),
            )
            conn.commit()
        return delivery

    def update_delivery(self, delivery: DeliveryRecord) -> DeliveryRecord:
        with self._connect() as conn:
            conn.execute(
                """UPDATE pm_delivery SET status=?, external_url=?, external_id=?,
                failure_reason=?, field_payload_json=?, audit_id=?, updated_at=?, metadata_json=?
                WHERE id=?""",
                (
                    str(delivery.status),
                    delivery.external_url,
                    delivery.external_id,
                    delivery.failure_reason,
                    _dump(delivery.field_payload),
                    delivery.audit_id,
                    delivery.updated_at,
                    _dump(delivery.metadata),
                    delivery.id,
                ),
            )
            conn.commit()
        return delivery

    def list_deliveries(self, project_id: str) -> list[DeliveryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_delivery WHERE project_id=? ORDER BY updated_at DESC",
                (project_id,),
            ).fetchall()
        return [self._delivery_from_row(row) for row in rows]

    def append_audit(self, event: ProjectAuditEvent) -> ProjectAuditEvent:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pm_audit_event
                (id, project_id, artifact_type, artifact_id, action, actor, reason,
                 artifact_version, created_at, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.id,
                    event.project_id,
                    event.artifact_type,
                    event.artifact_id,
                    event.action,
                    event.actor,
                    event.reason,
                    event.artifact_version,
                    event.created_at,
                    _dump(event.metadata),
                ),
            )
            conn.commit()
        return event

    def add_trace_link(self, link: TraceLink) -> TraceLink:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO pm_trace_link
                (id, project_id, from_type, from_id, to_type, to_id, relation, created_at, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    link.id,
                    link.project_id,
                    link.from_type,
                    link.from_id,
                    link.to_type,
                    link.to_id,
                    link.relation,
                    link.created_at,
                    _dump(link.metadata),
                ),
            )
            conn.commit()
        return link

    def list_trace_links(self, project_id: str) -> list[TraceLink]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_trace_link WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            TraceLink(
                id=row["id"],
                project_id=row["project_id"],
                from_type=row["from_type"],
                from_id=row["from_id"],
                to_type=row["to_type"],
                to_id=row["to_id"],
                relation=row["relation"],
                created_at=row["created_at"],
                metadata=_load_obj(row["metadata_json"]),
            )
            for row in rows
        ]

    def workbench_summary(self, project_id: str) -> dict[str, Any]:
        evidence = self.list_evidence(project_id, limit=1000)
        insights = self.list_insights(project_id)
        opportunities = self.list_opportunities(project_id)
        prds = self.list_prd_versions(project_id)
        deliveries = self.list_deliveries(project_id)
        pending = [
            item
            for item in opportunities
            if item.status == OpportunityStatus.pending_approval
        ]
        ready = [item for item in prds if item.status == PrdStatus.ready_for_delivery]
        return {
            "project_id": project_id,
            "counts": {
                "evidence": len(evidence),
                "confirmed_evidence": sum(1 for item in evidence if item.confirmed),
                "insights": len(insights),
                "opportunities": len(opportunities),
                "prd_versions": len(prds),
                "deliveries": len(deliveries),
            },
            "pending_approvals": len(pending),
            "ready_for_delivery": len(ready),
        }

    # ---- mappers ----------------------------------------------------------

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> EvidenceSource:
        return EvidenceSource(
            id=row["id"],
            project_id=row["project_id"],
            source_type=row["source_type"],
            external_id=row["external_id"],
            source_url=row["source_url"],
            display_name=row["display_name"],
            sync_status=SourceSyncStatus(row["sync_status"] or "idle"),
            sync_cursor=row["sync_cursor"] or "",
            last_synced_at=row["last_synced_at"] or "",
            last_error=row["last_error"] or "",
            metadata=_load_obj(row["metadata_json"]),
        )

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            id=row["id"],
            project_id=row["project_id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            content=row["content"],
            summary=row["summary"] or "",
            quote=row["quote"] or "",
            source_url=row["source_url"] or "",
            author=row["author"] or "",
            occurred_at=row["occurred_at"] or "",
            source_version=row["source_version"] or "",
            confirmed=bool(row["confirmed"]),
            source_refs=_load_list(row["source_refs_json"]),
            metadata=_load_obj(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _insight_from_row(row: sqlite3.Row) -> InsightRecord:
        return InsightRecord(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            summary=row["summary"] or "",
            theme=row["theme"] or "",
            evidence_refs=_load_list(row["evidence_refs_json"]),
            source_refs=_load_list(row["source_refs_json"]),
            source_urls=_load_list(row["source_urls_json"]),
            version=int(row["version"] or 1),
            audit_id=row["audit_id"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load_obj(row["metadata_json"]),
        )

    @staticmethod
    def _opportunity_from_row(row: sqlite3.Row) -> OpportunityRecord:
        return OpportunityRecord(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            problem=row["problem"] or "",
            users=row["users"] or "",
            value=row["value"] or "",
            status=OpportunityStatus(row["status"] or "proposed"),
            insight_ids=_load_list(row["insight_ids_json"]),
            evidence_refs=_load_list(row["evidence_refs_json"]),
            source_refs=_load_list(row["source_refs_json"]),
            source_urls=_load_list(row["source_urls_json"]),
            score=float(row["score"] or 0),
            score_method=row["score_method"] or "",
            score_details={
                str(k): float(v)
                for k, v in _load_obj(row["score_details_json"]).items()
                if isinstance(v, (int, float))
            },
            version=int(row["version"] or 1),
            audit_id=row["audit_id"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load_obj(row["metadata_json"]),
        )

    @staticmethod
    def _prd_from_row(row: sqlite3.Row) -> PrdVersionRecord:
        return PrdVersionRecord(
            id=row["id"],
            prd_id=row["prd_id"],
            project_id=row["project_id"],
            opportunity_id=row["opportunity_id"] or "",
            version=int(row["version"] or 1),
            title=row["title"],
            markdown=row["markdown"],
            status=PrdStatus(row["status"] or "draft"),
            quality_assessment_id=row["quality_assessment_id"] or "",
            quality_decision=row["quality_decision"] or "",
            evidence_refs=_load_list(row["evidence_refs_json"]),
            source_refs=_load_list(row["source_refs_json"]),
            source_urls=_load_list(row["source_urls_json"]),
            project_source_id=row["project_source_id"] or "",
            audit_id=row["audit_id"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load_obj(row["metadata_json"]),
        )

    @staticmethod
    def _delivery_from_row(row: sqlite3.Row) -> DeliveryRecord:
        return DeliveryRecord(
            id=row["id"],
            project_id=row["project_id"],
            prd_version_id=row["prd_version_id"],
            target_kind=row["target_kind"],
            idempotency_key=row["idempotency_key"],
            status=DeliveryStatus(row["status"] or "pending"),
            external_url=row["external_url"] or "",
            external_id=row["external_id"] or "",
            failure_reason=row["failure_reason"] or "",
            field_payload=_load_obj(row["field_payload_json"]),
            audit_id=row["audit_id"] or "",
            evidence_refs=_load_list(row["evidence_refs_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load_obj(row["metadata_json"]),
        )
