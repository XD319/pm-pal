"""Agent conversation API against project_space SQLite. :-)"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pm_pal.project_domain.repository import ProjectDomainRepository
from pm_pal.server.agent_router import create_agent_router
from pm_pal.server.project_space import Store


def _seed_project(db_path: Path, project_id: str = "project-a") -> None:
    store = Store(db_path)
    store.initialize()
    ProjectDomainRepository(db_path).initialize()
    stamp = "2026-08-03T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects "
            "(id,name,description,model_preset_id,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (project_id, "Demo", "", None, stamp, stamp),
        )
        conn.commit()


def test_agent_conversation_requires_confirmation_and_keeps_feishu_reference(tmp_path):
    db = tmp_path / "project_space.sqlite3"
    _seed_project(db, "mobile")
    app = FastAPI()
    app.include_router(create_agent_router(db_path=db, project_db_path=db))
    client = TestClient(app)

    created = client.post(
        "/api/agent/conversations", json={"project_id": "mobile", "actor": "ou_pm"}
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "content": "please process https://example.feishu.cn/docx/abc PRD",
            "actor": "ou_pm",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    task = payload["task"]
    assert task["kind"] == "connect_feishu"
    assert task["status"] == "awaiting_confirmation"
    assert task["source_url"].endswith("/abc")

    confirmed = client.post(
        f"/api/agent/tasks/{task['id']}/confirm",
        json={"confirmed": True, "actor": "ou_pm"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["task"]["status"] == "failed"

    restored = client.get(f"/api/agent/conversations/{conversation_id}")
    assert len(restored.json()["messages"]) == 2
    assert restored.json()["tasks"][0]["status"] == "failed"


def test_agent_conversation_history_is_newest_first_with_associations(tmp_path):
    db = tmp_path / "project_space.sqlite3"
    _seed_project(db, "project-a")
    _seed_project(db, "project-b")
    app = FastAPI()
    app.include_router(create_agent_router(db_path=db, project_db_path=db))
    client = TestClient(app)

    assert client.get("/api/agent/conversations").json() == {"conversations": []}
    first = client.post(
        "/api/agent/conversations", json={"title": "Earlier", "project_id": "project-a"}
    ).json()["conversation"]
    second = client.post(
        "/api/agent/conversations", json={"title": "Latest", "project_id": "project-b"}
    ).json()["conversation"]
    client.post(
        f"/api/agent/conversations/{first['id']}/messages",
        json={"content": "create a PRD", "actor": "ou_pm"},
    )

    items = client.get("/api/agent/conversations").json()["conversations"]
    assert [item["id"] for item in items] == [first["id"], second["id"]]
    assert items[0]["title"] == "Earlier"
    assert items[0]["project_id"] == "project-a"
    assert items[0]["latest_task_status"] == "awaiting_confirmation"
    assert items[1]["project_id"] == "project-b"


def test_agent_conversation_uses_creator_actor_when_message_omits_it(tmp_path):
    db = tmp_path / "project_space.sqlite3"
    _seed_project(db, "mobile")
    app = FastAPI()
    app.include_router(create_agent_router(db_path=db, project_db_path=db))
    client = TestClient(app)

    created = client.post(
        "/api/agent/conversations", json={"project_id": "mobile", "actor": "ou_pm"}
    )
    assert created.status_code == 200
    conversation = created.json()["conversation"]
    assert conversation["actor"] == "ou_pm"

    response = client.post(
        f"/api/agent/conversations/{conversation['id']}/messages",
        json={"content": "please process https://example.feishu.cn/docx/abc PRD"},
    )

    assert response.status_code == 200
    assert response.json()["task"]["details"]["command"]["actor"] == "ou_pm"


def test_agent_message_ambiguous_intent_without_preferred_action(tmp_path):
    db = tmp_path / "project_space.sqlite3"
    _seed_project(db, "mobile")
    app = FastAPI()
    app.include_router(create_agent_router(db_path=db, project_db_path=db))
    client = TestClient(app)
    conversation_id = client.post(
        "/api/agent/conversations", json={"project_id": "mobile", "actor": "ou_pm"}
    ).json()["conversation"]["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "content": "请做一次需求完整性评审，并生成可跟进的机会清单",
            "actor": "ou_pm",
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "intent_ambiguous"
    actions = {item["action"] for item in detail["candidates"]}
    assert "start_review" in actions
    assert "generate_opportunity" in actions


def test_agent_message_preferred_action_overrides_keywords(tmp_path):
    db = tmp_path / "project_space.sqlite3"
    _seed_project(db, "mobile")
    app = FastAPI()
    app.include_router(create_agent_router(db_path=db, project_db_path=db))
    client = TestClient(app)
    conversation_id = client.post(
        "/api/agent/conversations", json={"project_id": "mobile", "actor": "ou_pm"}
    ).json()["conversation"]["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={
            "content": "请做一次需求完整性评审，并生成可跟进的机会清单",
            "actor": "ou_pm",
            "action": "start_review",
        },
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["kind"] == "start_review"
    assert task["status"] == "awaiting_confirmation"
