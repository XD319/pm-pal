"""Command gateway policies and project-domain side effects. :-)"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pm_pal.project_domain.models import EvidenceRecord, EvidenceSource
from pm_pal.project_domain.repository import ProjectDomainRepository
from pm_pal.server.agent_router import create_agent_router
from pm_pal.server.command_gateway import CommandError, CommandGateway, policy_for
from pm_pal.server.project_space import Store


def _seed_project(db_path: Path, project_id: str = "project-1") -> None:
    Store(db_path).initialize()
    ProjectDomainRepository(db_path).initialize()
    stamp = "2026-08-03T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects "
            "(id,name,description,model_preset_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (project_id, "Demo", "", None, stamp, stamp),
        )
        conn.commit()


def test_action_policy_assigns_confirmation_only_to_side_effectful_actions():
    assert policy_for("connect_feishu").requires_confirmation is True
    assert policy_for("generate_prd").requires_confirmation is True
    assert policy_for("start_review").requires_confirmation is True
    assert policy_for("generate_insight").requires_confirmation is False
    assert policy_for("prepare_delivery").writes is False
    with pytest.raises(CommandError, match="Unsupported"):
        policy_for("delete_everything")


def test_agent_message_requires_actor_identity(tmp_path):
    db = tmp_path / "project_space.sqlite3"
    _seed_project(db)
    app = FastAPI()
    app.include_router(create_agent_router(db_path=db, project_db_path=db))
    client = TestClient(app)
    conversation_id = client.post(
        "/api/agent/conversations", json={"project_id": "project-1"}
    ).json()["conversation"]["id"]
    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "create a PRD"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "actor_required"


@pytest.mark.asyncio
async def test_generate_insight_requires_confirmed_evidence_and_never_confirms_it(tmp_path):
    db_path = tmp_path / "project_space.sqlite3"
    _seed_project(db_path, "p-1")
    repository = ProjectDomainRepository(db_path)
    repository.initialize()
    source = EvidenceSource(
        id="source-1",
        project_id="p-1",
        source_type="feishu_doc",
        external_id="doc-1",
    )
    repository.upsert_source(source)
    synced = repository.sync_evidence(
        "source-1",
        [
            EvidenceRecord(
                id="evidence-1",
                project_id="p-1",
                source_id="source-1",
                external_id="record-1",
                content="checkout freezes",
            )
        ],
    )
    assert len(synced) == 1
    gateway = CommandGateway(project_db_path=db_path)
    command = {
        "command_id": "cmd-1",
        "idempotency_key": "cmd-1",
        "action": "generate_insight",
        "actor": "ou_pm",
        "project_id": "p-1",
        "payload": {},
    }
    with pytest.raises(CommandError, match="Confirm evidence"):
        await gateway.execute(command)
    evidence = repository.list_evidence("p-1")[0]
    assert evidence.confirmed is False
    repository.confirm_evidence(evidence.id, confirmed=True)
    result = await gateway.execute(command)
    assert result["opportunity_id"]
    opportunity = repository.get_opportunity(result["opportunity_id"])
    assert opportunity is not None
    assert str(opportunity.status) == "pending_approval"
    assert "checkout freezes" in opportunity.problem
    assert "checkout freezes" in opportunity.title or "checkout" in opportunity.title
    insight = repository.get_insight(result["insight_id"])
    assert insight is not None
    assert "checkout freezes" in insight.summary
