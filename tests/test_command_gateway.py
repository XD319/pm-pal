from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.product_decision.models import EvidenceRecord, EvidenceSource
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.server.agent_router import create_agent_router
from prd_pal.server.command_gateway import CommandError, CommandGateway, policy_for


def test_action_policy_assigns_confirmation_only_to_side_effectful_actions():
    assert policy_for("connect_feishu").requires_confirmation is True
    assert policy_for("generate_prd").requires_confirmation is True
    assert policy_for("start_review").requires_confirmation is True
    assert policy_for("generate_insight").requires_confirmation is False
    assert policy_for("prepare_delivery").writes is False
    with pytest.raises(CommandError, match="Unsupported"):
        policy_for("delete_everything")


def test_agent_message_requires_actor_identity(tmp_path):
    app = FastAPI()
    app.include_router(create_agent_router(db_path=tmp_path / "agent.sqlite3", decision_db_path=tmp_path / "decision.sqlite3", project_db_path=tmp_path / "project.sqlite3"))
    client = TestClient(app)
    conversation_id = client.post("/api/agent/conversations", json={}).json()["conversation"]["id"]
    response = client.post(f"/api/agent/conversations/{conversation_id}/messages", json={"content": "create a PRD"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "actor_required"


@pytest.mark.asyncio
async def test_generate_insight_requires_confirmed_evidence_and_never_confirms_it(tmp_path):
    db_path = tmp_path / "decision.sqlite3"
    repository = ProductDecisionRepository(db_path)
    assert (await repository.initialize()).ok
    source = EvidenceSource(id="source-1", product_id="p-1", source_type="feishu_doc", external_id="doc-1")
    assert (await repository.upsert_source(source)).ok
    synced = await repository.sync_evidence("source-1", [EvidenceRecord(id="evidence-1", source_id="source-1", product_id="p-1", external_id="record-1", content="checkout freezes")])
    assert synced.ok
    gateway = CommandGateway(decision_db_path=db_path, project_db_path=tmp_path / "project.sqlite3")
    command = {"command_id": "cmd-1", "idempotency_key": "cmd-1", "action": "generate_insight", "actor": "ou_pm", "product_id": "p-1", "project_id": "", "payload": {}}
    with pytest.raises(CommandError, match="Confirm evidence"):
        await gateway.execute(command)
    evidence = (await repository.list_evidence(product_id="p-1")).value[0]
    assert evidence.confirmed is False
    assert (await repository.mark_evidence_confirmed(evidence.id)).ok
    result = await gateway.execute(command)
    assert result["opportunity_id"]
    opportunity = (await repository.get_opportunity(result["opportunity_id"])).value
    assert str(opportunity.status) == "proposed"
