from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.product_decision.router import create_product_decision_router


def test_workbench_summary_and_confirmed_evidence_drafts(tmp_path):
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
    with TestClient(app) as client:
        source = client.post(
            "/api/decision/sources",
            json={"product_id": "p-1", "source_type": "feishu_doc", "external_id": "doc-1"},
        ).json()["source"]
        evidence = client.post(
            f"/api/decision/sources/{source['id']}/sync",
            json={"records": [{"external_id": "r-1", "content": "Mobile checkout freezes"}]},
        ).json()["evidence"][0]

        rejected = client.post("/api/decision/drafts/generate", json={"product_id": "p-1"})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "confirmed_evidence_required"

        assert client.post(f"/api/decision/evidence/{evidence['id']}/confirm", json={}).status_code == 200
        generated = client.post("/api/decision/drafts/generate", json={"product_id": "p-1"})
        assert generated.status_code == 200
        payload = generated.json()
        assert payload["opportunity"]["status"] == "proposed"
        assert payload["opportunity"]["evidence_refs"] == [evidence["id"]]
        assert payload["opportunity"]["metadata"]["pending_human_confirmation"] is True

        summary = client.get("/api/decision/workbench/summary", params={"product_id": "p-1"})
        assert summary.status_code == 200
        assert summary.json()["counts"]["opportunities"] == 1
