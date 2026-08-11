"""Policy-governed command execution for the conversation agent.

The gateway is the only adapter allowed to cross from an agent conversation into
project-space storage, connectors, or a review run. :-)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pm_pal.connectors.feishu import FeishuConnector
from pm_pal.project_domain.models import (
    EvidenceRecord,
    EvidenceSource,
    OpportunityStatus,
    PrdStatus,
)
from pm_pal.project_domain.repository import ProjectDomainRepository
from pm_pal.project_domain.services import (
    DeliveryService,
    InsightService,
    OpportunityService,
    PrdLifecycleService,
    ProjectDomainError,
)
from pm_pal.server.agent_content import build_insight_opportunity_copy
from pm_pal.utils.time import utc_now_iso

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
    "generate_insight": ActionPolicy("generate_insight", False, writes=False),
    "generate_opportunity": ActionPolicy("generate_opportunity", True),
    "generate_prd": ActionPolicy("generate_prd", True),
    "start_review": ActionPolicy("start_review", True),
    "prepare_delivery": ActionPolicy("prepare_delivery", False, writes=False),
}


def policy_for(action: str) -> ActionPolicy:
    try:
        return POLICIES[action]
    except KeyError as exc:
        raise CommandError(
            "unknown_action", f"Unsupported agent action: {action}"
        ) from exc


class CommandGateway:
    def __init__(
        self,
        *,
        project_db_path: str | Path,
        start_review: ReviewStarter | None = None,
    ) -> None:
        self.project_db_path = Path(project_db_path)
        self.repository = ProjectDomainRepository(self.project_db_path)
        self.repository.initialize()
        self.start_review = start_review

    async def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command.get("action") or "")
        policy = policy_for(action)
        actor = str(command.get("actor") or "").strip()
        if policy.writes and not actor:
            raise CommandError("actor_required", f"{action} requires an actor identity")
        try:
            if action == "connect_feishu":
                return await self._connect_feishu(command)
            if action in {"generate_insight", "generate_opportunity"}:
                return await asyncio.to_thread(
                    self._generate_insight_opportunity, command
                )
            if action == "generate_prd":
                return await asyncio.to_thread(self._generate_prd, command)
            if action == "start_review":
                return await self._start_review(command)
            if action == "prepare_delivery":
                return await asyncio.to_thread(self._prepare_delivery, command)
        except ProjectDomainError as exc:
            raise CommandError(exc.code, exc.message) from exc
        except LookupError as exc:
            raise CommandError("not_found", str(exc)) from exc
        raise CommandError("unknown_action", f"Unsupported agent action: {action}")

    def _project_connection(self) -> sqlite3.Connection:
        self.project_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.project_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _command_id(command: dict[str, Any]) -> str:
        return str(command.get("command_id") or "")

    def _require_project_id(self, command: dict[str, Any]) -> str:
        project_id = str(command.get("project_id") or "").strip()
        if not project_id:
            raise CommandError("project_required", "project_id is required")
        self.repository.ensure_project(project_id)
        return project_id

    async def _connect_feishu(self, command: dict[str, Any]) -> dict[str, Any]:
        source_url = str(
            dict(command.get("payload") or {}).get("source_url") or ""
        ).strip()
        if not source_url:
            raise CommandError(
                "source_url_required", "connect_feishu requires a Feishu URL"
            )
        document = await asyncio.to_thread(FeishuConnector().get_content, source_url)
        command_id = self._command_id(command)
        timestamp = utc_now_iso()
        checksum = hashlib.sha256(document.content_markdown.encode("utf-8")).hexdigest()
        with self._project_connection() as conn:
            project_id = (
                str(command.get("project_id") or "").strip()
                or f"project_{uuid.uuid4().hex[:12]}"
            )
            existing_project = conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if existing_project is None:
                conn.execute(
                    "INSERT INTO projects (id,name,description,model_preset_id,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        project_id,
                        document.title or "飞书产品文档",
                        "由 Agent 命令创建",
                        None,
                        timestamp,
                        timestamp,
                    ),
                )
            source = conn.execute(
                "SELECT * FROM project_sources WHERE project_id=? AND source_url=? "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id, source_url),
            ).fetchone()
            if source is None:
                source_id = f"source_{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT INTO project_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        source_id,
                        project_id,
                        document.title or "飞书文档",
                        "feishu",
                        document.content_markdown,
                        source_url,
                        1,
                        1,
                        timestamp,
                        None,
                        checksum,
                        json.dumps(
                            {
                                "origin": "command_gateway",
                                "command_id": command_id,
                                "actor": command["actor"],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                conn.execute(
                    "INSERT INTO project_events VALUES (?,?,?,?,?,?)",
                    (
                        f"event_{uuid.uuid4().hex[:12]}",
                        project_id,
                        "source_added",
                        document.title or "飞书文档",
                        source_id,
                        timestamp,
                    ),
                )
            else:
                source_id = str(source["id"])
                conn.execute(
                    "UPDATE project_sources SET content=?, checksum=?, metadata_json=? WHERE id=?",
                    (
                        document.content_markdown,
                        checksum,
                        json.dumps(
                            {
                                "origin": "command_gateway",
                                "command_id": command_id,
                                "actor": command["actor"],
                            },
                            ensure_ascii=False,
                        ),
                        source_id,
                    ),
                )
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE id=?", (timestamp, project_id)
            )
            conn.commit()

        evidence_source_id = f"agent-source-{command_id}"
        external_id = str(
            document.metadata.extra.get("resolved_document_token") or source_url
        )
        source = EvidenceSource(
            id=evidence_source_id,
            project_id=project_id,
            source_type="feishu_doc",
            external_id=external_id,
            source_url=source_url,
            display_name=document.title,
            metadata={"command_id": command_id, "source_checksum": checksum},
        )
        self.repository.upsert_source(source)
        evidence = EvidenceRecord(
            id=f"evidence-{command_id}",
            project_id=project_id,
            source_id=evidence_source_id,
            external_id=external_id,
            content=document.content_markdown,
            summary=document.title,
            source_url=source_url,
            source_version=checksum,
            source_refs=[f"project_source:{source_id}", f"command:{command_id}"],
            metadata={"command_id": command_id},
        )
        synced = self.repository.sync_evidence(
            evidence_source_id, [evidence], cursor=checksum
        )
        return {
            "project_id": project_id,
            "source_id": source_id,
            "source_checksum": checksum,
            "evidence_source_id": evidence_source_id,
            "evidence_ids": [item.id for item in synced],
            "source_url": source_url,
            "trace": {"command_id": command_id},
        }

    def _generate_insight_opportunity(self, command: dict[str, Any]) -> dict[str, Any]:
        project_id = self._require_project_id(command)
        command_id = self._command_id(command)
        actor = str(command.get("actor") or "local")
        insights = self.repository.list_insights(project_id)
        insight = next(
            (
                item
                for item in insights
                if item.metadata.get("command_id") == command_id
            ),
            None,
        )
        confirmed = self.repository.list_evidence(
            project_id, confirmed_only=True, limit=1000
        )
        if not confirmed:
            raise CommandError(
                "confirmed_evidence_required",
                "Confirm evidence before generating an insight",
            )
        if insight is None:
            copy = build_insight_opportunity_copy(confirmed)
            insight, _ = InsightService(self.repository).create_insight(
                project_id=project_id,
                title=copy["insight_title"],
                summary=copy["insight_summary"],
                theme="agent",
                evidence_refs=[item.id for item in confirmed],
                actor=actor,
                metadata={
                    "command_id": command_id,
                    "agent": True,
                    "pending_human_confirmation": True,
                },
            )
        opportunities = self.repository.list_opportunities(project_id)
        opportunity = next(
            (
                item
                for item in opportunities
                if item.metadata.get("command_id") == command_id
            ),
            None,
        )
        if opportunity is None:
            copy = build_insight_opportunity_copy(confirmed)
            opportunity, _ = OpportunityService(self.repository).create_candidate(
                project_id=project_id,
                title=copy["opportunity_title"],
                problem=copy["problem"],
                users=copy["users"],
                value=copy["value"],
                insight_ids=[insight.id],
                actor=actor,
                metadata={
                    "command_id": command_id,
                    "agent": True,
                    "pending_human_confirmation": True,
                },
            )
            opportunity, _ = OpportunityService(self.repository).submit_for_approval(
                opportunity.id, actor=actor, reason="agent_generated"
            )
        return {
            "project_id": project_id,
            "insight_id": insight.id,
            "opportunity_id": opportunity.id,
            "evidence_ids": list(insight.evidence_refs),
            "next_action": "编辑或提交机会审批",
            "trace": {"command_id": command_id},
        }

    def _generate_prd(self, command: dict[str, Any]) -> dict[str, Any]:
        project_id = self._require_project_id(command)
        opportunities = self.repository.list_opportunities(project_id)
        approved = next(
            (
                item
                for item in opportunities
                if item.status == OpportunityStatus.approved
            ),
            None,
        )
        if approved is None:
            raise CommandError(
                "opportunity_not_approved",
                "No owner-approved opportunity is available",
            )
        command_id = self._command_id(command)
        versions = self.repository.list_prd_versions(project_id)
        existing = next(
            (
                item
                for item in versions
                if item.metadata.get("command_id") == command_id
            ),
            None,
        )
        if existing is None:
            existing, _ = PrdLifecycleService(
                self.repository
            ).create_from_approved_opportunity(
                approved.id,
                actor=str(command["actor"]),
                metadata={
                    "command_id": command_id,
                    "idempotency_key": command.get("idempotency_key"),
                },
            )
        return {
            "prd_version_id": existing.id,
            "opportunity_id": approved.id,
            "project_id": project_id,
            "project_source_id": existing.project_source_id,
            "next_action": "确认后可发起 PRD 评审",
            "trace": {"command_id": command_id},
        }

    async def _start_review(self, command: dict[str, Any]) -> dict[str, Any]:
        if self.start_review is None:
            raise CommandError(
                "review_executor_missing", "Review executor is not configured"
            )
        project_id = self._require_project_id(command)
        payload = dict(command.get("payload") or {})
        source_id = str(payload.get("source_id") or "").strip()
        expected_checksum = str(payload.get("source_checksum") or "").strip()
        if not source_id:
            # Fall back to latest confirmed PRD materialized as project source. :-)
            ready = [
                item
                for item in self.repository.list_prd_versions(project_id)
                if item.project_source_id
                and item.status
                in {
                    PrdStatus.approved,
                    PrdStatus.waived,
                    PrdStatus.ready_for_delivery,
                    PrdStatus.draft,
                    PrdStatus.quality_checked,
                }
            ]
            if ready:
                source_id = ready[0].project_source_id
        if not source_id:
            with self._project_connection() as conn:
                latest = conn.execute(
                    "SELECT id FROM project_sources WHERE project_id=? AND is_prd=1 "
                    "ORDER BY created_at DESC LIMIT 1",
                    (project_id,),
                ).fetchone()
            if latest:
                source_id = str(latest["id"])
        if not source_id:
            raise CommandError(
                "review_source_required", "A project PRD source is required"
            )
        with self._project_connection() as conn:
            source = conn.execute(
                "SELECT * FROM project_sources WHERE id=? AND project_id=? AND is_prd=1",
                (source_id, project_id),
            ).fetchone()
        if source is None:
            raise CommandError(
                "review_source_not_found", "The selected PRD source no longer exists"
            )
        if expected_checksum and str(source["checksum"] or "") != expected_checksum:
            raise CommandError(
                "precondition_failed", "The PRD source changed after confirmation"
            )
        result = await self.start_review(
            prd_text=source["content"] or None,
            source=source["source_url"] or None,
            mode="auto",
            llm_options=None,
            audit_context={
                "source": "agent",
                "actor": command["actor"],
                "command_id": command["command_id"],
                "idempotency_key": command["idempotency_key"],
            },
        )
        with self._project_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO project_runs VALUES (?,?,?,?)",
                (project_id, result["run_id"], source_id, utc_now_iso()),
            )
            conn.execute(
                "INSERT INTO project_events VALUES (?,?,?,?,?,?)",
                (
                    f"event_{uuid.uuid4().hex[:12]}",
                    project_id,
                    "review_started",
                    f"command:{command['command_id']}",
                    source_id,
                    utc_now_iso(),
                ),
            )
            conn.commit()
        return {
            "project_id": project_id,
            "run_id": result["run_id"],
            "source_id": source_id,
            "next_action": "评审已启动，查看进度",
            "trace": {"command_id": command["command_id"]},
        }

    def _prepare_delivery(self, command: dict[str, Any]) -> dict[str, Any]:
        project_id = self._require_project_id(command)
        versions = self.repository.list_prd_versions(project_id)
        ready = next(
            (item for item in versions if item.status == PrdStatus.ready_for_delivery),
            None,
        )
        if ready is None:
            approved = next(
                (
                    item
                    for item in versions
                    if item.status in {PrdStatus.approved, PrdStatus.waived}
                ),
                None,
            )
            if approved is None:
                raise CommandError(
                    "prd_not_ready",
                    "No approved/ready PRD is available for delivery",
                )
            ready, _ = PrdLifecycleService(self.repository).mark_ready(
                approved.id, actor=str(command.get("actor") or "")
            )
        delivery, _ = DeliveryService(self.repository).export(
            prd_version_id=ready.id,
            target_kind="local_bundle",
            actor=str(command.get("actor") or ""),
            idempotency_key=f"agent:{command.get('command_id')}:{ready.id}",
        )
        return {
            "project_id": project_id,
            "prd_version_id": ready.id,
            "delivery_id": delivery.id,
            "status": str(delivery.status),
            "next_action": "查看交付记录",
            "trace": {"command_id": self._command_id(command)},
        }
