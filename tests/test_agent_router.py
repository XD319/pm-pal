from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.server.agent_router import create_agent_router


def test_agent_conversation_requires_confirmation_and_keeps_feishu_reference(tmp_path):
    app = FastAPI()
    app.include_router(create_agent_router(db_path=tmp_path / "agent.sqlite3", decision_db_path=tmp_path / "decision.sqlite3", project_db_path=tmp_path / "projects.sqlite3"))
    client = TestClient(app)

    created = client.post("/api/agent/conversations", json={"product_id": "mobile"})
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["id"]

    response = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json={"content": "please process https://example.feishu.cn/docx/abc PRD", "actor": "ou_pm"},
    )
    assert response.status_code == 200
    payload = response.json()
    task = payload["task"]
    assert task["kind"] == "connect_feishu"
    assert task["status"] == "awaiting_confirmation"
    assert task["source_url"].endswith("/abc")

    confirmed = client.post(f"/api/agent/tasks/{task['id']}/confirm", json={"confirmed": True, "actor": "ou_pm"})
    assert confirmed.status_code == 200
    assert confirmed.json()["task"]["status"] == "failed"

    restored = client.get(f"/api/agent/conversations/{conversation_id}")
    assert len(restored.json()["messages"]) == 2
    assert restored.json()["tasks"][0]["status"] == "failed"


def test_agent_conversation_history_is_newest_first_with_associations(tmp_path):
    app = FastAPI()
    app.include_router(create_agent_router(db_path=tmp_path / "agent.sqlite3", decision_db_path=tmp_path / "decision.sqlite3", project_db_path=tmp_path / "projects.sqlite3"))
    client = TestClient(app)

    assert client.get("/api/agent/conversations").json() == {"conversations": []}
    first = client.post("/api/agent/conversations", json={"title": "Earlier", "project_id": "project-a"}).json()["conversation"]
    second = client.post("/api/agent/conversations", json={"title": "Latest", "product_id": "product-b"}).json()["conversation"]
    client.post(f"/api/agent/conversations/{first['id']}/messages", json={"content": "create a PRD", "actor": "ou_pm"})

    items = client.get("/api/agent/conversations").json()["conversations"]
    assert [item["id"] for item in items] == [first["id"], second["id"]]
    assert items[0]["title"] == "Earlier"
    assert items[0]["project_id"] == "project-a"
    assert items[0]["latest_task_status"] == "awaiting_confirmation"
    assert items[1]["product_id"] == "product-b"
