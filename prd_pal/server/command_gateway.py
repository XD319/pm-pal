"""Policy-governed command execution for the conversation agent.

The gateway is intentionally the only adapter allowed to cross from an agent
conversation into project storage, the decision domain, connectors, or a
review run.  It keeps the exploration UX lightweight while making every side
effect attributable and retry-safe within the local SQLite deployment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from prd_pal.connectors.feishu import FeishuConnector
from prd_pal.product_decision.models import EvidenceRecord, EvidenceSource, EvidenceSourceType
from prd_pal.product_decision.prd_lifecycle import PrdLifecycleService
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.product_decision.services import InsightService, OpportunityService
from prd_pal.utils.time import utc_now_iso

ReviewStarter = Callable[..., Awaitable[dict[str, Any]]]


class CommandError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    action: str
    requires_confirmation: bool
    writes: bool = True


POLICIES: dict[str, ActionPolicy] = {
    "connect_feishu": ActionPolicy("connect_feishu", True),
    "generate_insight": ActionPolicy("generate_insight", False),
    "generate_prd": ActionPolicy("generate_prd", True),
    "start_review": ActionPolicy("start_review", True),
    "prepare_delivery": ActionPolicy("prepare_delivery", False, writes=False),
}


def policy_for(action: str) -> ActionPolicy:
    try:
        return POLICIES[action]
    except KeyError as exc:
        raise CommandError("unknown_action", f"Unsupported agent action: {action}") from exc


class CommandGateway:
    def __init__(
        self,
        *,
        decision_db_path: str | Path,
        project_db_path: str | Path,
        start_review: ReviewStarter | None = None,
    ) -> None:
        self.decision_db_path = Path(decision_db_path)
        self.project_db_path = Path(project_db_path)
        self.start_review = start_review

    async def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command.get("action") or "")
        policy = policy_for(action)
        actor = str(command.get("actor") or "").strip()
        if policy.writes and not actor:
            raise CommandError("actor_required", f"{action} requires an actor identity")
        if action == "connect_feishu":
            return await self._connect_feishu(command)
        if action == "generate_insight":
            return await self._generate_insight(command)
        if action == "generate_prd":
            return await self._generate_prd(command)
        if action == "start_review":
            return await self._start_review(command)
        if action == "prepare_delivery":
            return await self._prepare_delivery(command)
        raise CommandError("unknown_action", f"Unsupported agent action: {action}")

    def _project_connection(self) -> sqlite3.Connection:
        self.project_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.project_db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',model_preset_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_sources (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,title TEXT NOT NULL,source_type TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',source_url TEXT NOT NULL DEFAULT '',is_prd INTEGER NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,parent_source_id TEXT,checksum TEXT NOT NULL DEFAULT '',metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS project_runs (project_id TEXT NOT NULL,run_id TEXT PRIMARY KEY,source_id TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_events (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL,source_id TEXT,created_at TEXT NOT NULL);
            """
        )
        return conn

    @staticmethod
    def _command_id(command: dict[str, Any]) -> str:
        return str(command.get("command_id") or "")

    async def _connect_feishu(self, command: dict[str, Any]) -> dict[str, Any]:
        source_url = str(dict(command.get("payload") or {}).get("source_url") or "").strip()
        if not source_url:
            raise CommandError("source_url_required", "connect_feishu requires a Feishu URL")
        document = await asyncio.to_thread(FeishuConnector().get_content, source_url)
        command_id = self._command_id(command)
        product_id = str(command.get("product_id") or "").strip() or f"product-{uuid.uuid4().hex[:8]}"
        timestamp = utc_now_iso()
        checksum = hashlib.sha256(document.content_markdown.encode("utf-8")).hexdigest()
        with self._project_connection() as conn:
            project_id = str(command.get("project_id") or "").strip() or f"project_{uuid.uuid4().hex[:12]}"
            existing_project = conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
            if existing_project is None:
                conn.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)", (project_id, document.title or "飞书产品文档", "由 Agent 命令创建", None, timestamp, timestamp))
            source = conn.execute("SELECT * FROM project_sources WHERE project_id=? AND source_url=? ORDER BY created_at DESC LIMIT 1", (project_id, source_url)).fetchone()
            if source is None:
                source_id = f"source_{uuid.uuid4().hex[:12]}"
                conn.execute("INSERT INTO project_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (source_id, project_id, document.title or "飞书文档", "feishu", document.content_markdown, source_url, 1, 1, timestamp, None, checksum, json.dumps({"origin": "command_gateway", "command_id": command_id, "actor": command["actor"]}, ensure_ascii=False)))
                conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?,?)", (f"event_{uuid.uuid4().hex[:12]}", project_id, "source_added", document.title or "飞书文档", source_id, timestamp))
            else:
                source_id = str(source["id"])
                conn.execute("UPDATE project_sources SET content=?, checksum=?, metadata_json=? WHERE id=?", (document.content_markdown, checksum, json.dumps({"origin": "command_gateway", "command_id": command_id, "actor": command["actor"]}, ensure_ascii=False), source_id))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id))

        repository = ProductDecisionRepository(self.decision_db_path)
        await repository.initialize()
        evidence_source_id = f"agent-source-{command_id}"
        external_id = str(document.metadata.extra.get("resolved_document_token") or source_url)
        source = EvidenceSource(id=evidence_source_id, product_id=product_id, source_type=EvidenceSourceType.feishu_doc, external_id=external_id, source_url=source_url, display_name=document.title, metadata={"command_id": command_id, "source_checksum": checksum})
        await repository.upsert_source(source)
        evidence = EvidenceRecord(id=f"evidence-{command_id}", source_id=evidence_source_id, external_id=external_id, product_id=product_id, content=document.content_markdown, summary=document.title, source_url=source_url, source_version=checksum, source_refs=[f"project_source:{source_id}", f"command:{command_id}"], metadata={"command_id": command_id})
        synced = await repository.sync_evidence(evidence_source_id, [evidence], cursor=checksum)
        if not synced.ok:
            raise CommandError("evidence_sync_failed", synced.error.message if synced.error else "Could not persist evidence", retryable=True)
        return {"product_id": product_id, "project_id": project_id, "source_id": source_id, "source_checksum": checksum, "evidence_source_id": evidence_source_id, "evidence_ids": [item.id for item in (synced.value or [])], "source_url": source_url, "trace": {"command_id": command_id}}

    async def _generate_insight(self, command: dict[str, Any]) -> dict[str, Any]:
        repository = ProductDecisionRepository(self.decision_db_path)
        await repository.initialize()
        product_id = str(command.get("product_id") or "").strip()
        if not product_id:
            raise CommandError("product_required", "generate_insight requires product_id")
        command_id = self._command_id(command)
        insights = (await repository.list_insights(product_id=product_id)).value or []
        insight = next((item for item in insights if item.metadata.get("command_id") == command_id), None)
        evidence = (await repository.list_evidence(product_id=product_id, limit=1000)).value or []
        confirmed = [item for item in evidence if item.confirmed]
        if not confirmed:
            raise CommandError("confirmed_evidence_required", "Confirm evidence before generating an insight")
        if insight is None:
            refs = [item.id for item in confirmed]
            insight, _ = await InsightService(repository).create_insight(product_id=product_id, title="Agent 归纳的产品洞察", summary=f"基于 {len(refs)} 条已确认来源生成。", theme="agent", evidence_refs=refs, actor=str(command["actor"]), metadata={"command_id": command_id, "agent": True, "pending_human_confirmation": True})
        opportunities = (await repository.list_opportunities(product_id=product_id)).value or []
        opportunity = next((item for item in opportunities if item.metadata.get("command_id") == command_id), None)
        if opportunity is None:
            opportunity, _ = await OpportunityService(repository).create_candidate(product_id=product_id, title="待验证的产品机会", problem=insight.summary, users="已连接来源涉及的用户", value="等待产品负责人验证并审批。", insight_ids=[insight.id], actor=str(command["actor"]), metadata={"command_id": command_id, "agent": True, "pending_human_confirmation": True})
        return {"product_id": product_id, "insight_id": insight.id, "opportunity_id": opportunity.id, "evidence_ids": list(insight.evidence_refs), "next_action": "编辑或提交机会审批", "trace": {"command_id": command_id}}

    async def _generate_prd(self, command: dict[str, Any]) -> dict[str, Any]:
        repository = ProductDecisionRepository(self.decision_db_path)
        await repository.initialize()
        product_id = str(command.get("product_id") or "").strip()
        opportunities = (await repository.list_opportunities(product_id=product_id)).value or []
        approved = next((item for item in opportunities if str(item.status) == "approved"), None)
        if approved is None:
            raise CommandError("opportunity_not_approved", "No owner-approved opportunity is available")
        command_id = self._command_id(command)
        versions = (await repository.list_prd_versions(product_id=product_id)).value or []
        existing = next((item for item in versions if item.metadata.get("command_id") == command_id), None)
        if existing is None:
            existing, _ = await PrdLifecycleService(repository).create_from_approved_opportunity(approved.id, actor_open_id=str(command["actor"]), metadata={"command_id": command_id, "idempotency_key": command["idempotency_key"]})
        return {"prd_version_id": existing.id, "opportunity_id": approved.id, "project_id": command.get("project_id") or "", "next_action": "确认后可发起 PRD 评审", "trace": {"command_id": command_id}}

    async def _start_review(self, command: dict[str, Any]) -> dict[str, Any]:
        if self.start_review is None:
            raise CommandError("review_executor_missing", "Review executor is not configured")
        project_id = str(command.get("project_id") or "").strip()
        payload = dict(command.get("payload") or {})
        source_id = str(payload.get("source_id") or "").strip()
        expected_checksum = str(payload.get("source_checksum") or "").strip()
        if not project_id or not source_id:
            raise CommandError("review_source_required", "A project PRD source is required")
        with self._project_connection() as conn:
            source = conn.execute("SELECT * FROM project_sources WHERE id=? AND project_id=? AND is_prd=1", (source_id, project_id)).fetchone()
        if source is None:
            raise CommandError("review_source_not_found", "The selected PRD source no longer exists")
        if expected_checksum and str(source["checksum"] or "") != expected_checksum:
            raise CommandError("precondition_failed", "The PRD source changed after confirmation")
        result = await self.start_review(prd_text=source["content"] or None, source=source["source_url"] or None, mode="auto", llm_options=None, audit_context={"source": "agent", "actor": command["actor"], "command_id": command["command_id"], "idempotency_key": command["idempotency_key"]})
        with self._project_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO project_runs VALUES (?,?,?,?)", (project_id, result["run_id"], source_id, utc_now_iso()))
            conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?,?)", (f"event_{uuid.uuid4().hex[:12]}", project_id, "review_started", f"command:{command['command_id']}", source_id, utc_now_iso()))
        return {"project_id": project_id, "source_id": source_id, "run_id": result["run_id"], "next_action": "评审运行中", "trace": {"command_id": command["command_id"]}}

    async def _prepare_delivery(self, command: dict[str, Any]) -> dict[str, Any]:
        repository = ProductDecisionRepository(self.decision_db_path)
        await repository.initialize()
        product_id = str(command.get("product_id") or "").strip()
        versions = (await repository.list_prd_versions(product_id=product_id)).value or []
        ready = next((item for item in versions if str(item.status) == "ready_for_delivery"), None)
        if ready is None:
            raise CommandError("prd_not_ready", "PRD is not ready for delivery")
        return {"prd_version_id": ready.id, "next_action": "可在工作台确认飞书交付目标并导出", "workbench_path": f"/workbench?product_id={product_id}", "trace": {"command_id": command["command_id"]}}
