from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.product_decision.router import create_product_decision_router


def test_evidence_sync_is_idempotent_and_preserves_source_link(tmp_path) -> None:
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
    with TestClient(app) as client:
        source = client.post("/api/decision/sources", json={"product_id": "p-1", "source_type": "feishu_bitable", "source_url": "https://example.feishu.cn/base/1", "display_name": "Research inbox"}).json()["source"]
        first = client.post(f"/api/decision/sources/{source['id']}/sync", json={"cursor": "1", "records": [{"external_id": "row-1", "content": "Login is confusing", "source_url": "https://example.feishu.cn/base/1?row=row-1", "source_version": "v1"}]}).json()
        second = client.post(f"/api/decision/sources/{source['id']}/sync", json={"cursor": "2", "records": [{"external_id": "row-1", "content": "Login is confusing on mobile", "source_version": "v2"}]}).json()
        evidence = client.get("/api/decision/evidence?product_id=p-1&query=mobile").json()["evidence"]
        sources = client.get("/api/decision/sources?product_id=p-1").json()["sources"]

    assert first["synced_count"] == 1
    assert second["synced_count"] == 1
    assert len(evidence) == 1
    assert evidence[0]["content"] == "Login is confusing on mobile"
    assert sources[0]["sync_cursor"] == "2"
    assert sources[0]["sync_status"] == "succeeded"
