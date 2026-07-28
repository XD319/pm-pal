from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.product_decision.router import create_product_decision_router


def _seed_evidence(client: TestClient, *, product_id: str, content: str, external_id: str) -> str:
    source = client.post(
        "/api/decision/sources",
        json={
            "product_id": product_id,
            "source_type": "feishu_doc",
            "external_id": external_id,
            "source_url": f"https://example.feishu.cn/docx/{external_id}",
            "display_name": f"{product_id}-doc",
        },
    ).json()["source"]
    synced = client.post(
        f"/api/decision/sources/{source['id']}/sync",
        json={
            "cursor": "1",
            "records": [
                {
                    "external_id": external_id,
                    "content": content,
                    "source_url": f"https://example.feishu.cn/docx/{external_id}",
                    "source_version": "v1",
                }
            ],
        },
    ).json()
    return synced["evidence"][0]["id"]


def test_insight_requires_evidence_and_isolates_products(tmp_path) -> None:
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
    with TestClient(app) as client:
        evidence_a = _seed_evidence(
            client, product_id="p-a", content="Checkout fails on mobile", external_id="doc-a"
        )
        evidence_b = _seed_evidence(
            client, product_id="p-b", content="Search ranking is noisy", external_id="doc-b"
        )

        rejected = client.post(
            "/api/decision/insights",
            json={
                "product_id": "p-a",
                "title": "No evidence insight",
                "evidence_refs": [],
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "evidence_required"

        cross = client.post(
            "/api/decision/insights",
            json={
                "product_id": "p-a",
                "title": "Wrong product evidence",
                "evidence_refs": [evidence_b],
            },
        )
        assert cross.status_code == 422
        assert cross.json()["detail"]["code"] == "evidence_required"

        created = client.post(
            "/api/decision/insights",
            json={
                "product_id": "p-a",
                "title": "Mobile checkout friction",
                "summary": "Users fail checkout on phones",
                "evidence_refs": [evidence_a],
                "actor": "ou_owner",
            },
        ).json()
        assert created["artifact_id"]
        assert created["audit_id"]
        assert created["version"] == 1
        assert created["next_human_action"]
        assert created["insight"]["source_urls"] == [
            "https://example.feishu.cn/docx/doc-a"
        ]

        listed_a = client.get("/api/decision/insights?product_id=p-a").json()["insights"]
        listed_b = client.get("/api/decision/insights?product_id=p-b").json()["insights"]
        assert len(listed_a) == 1
        assert listed_a[0]["evidence_refs"] == [evidence_a]
        assert listed_b == []


def test_candidate_cannot_bypass_approval_to_create_prd(tmp_path) -> None:
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
    with TestClient(app) as client:
        evidence_id = _seed_evidence(
            client,
            product_id="p-1",
            content="Need offline draft mode",
            external_id="doc-1",
        )
        insight = client.post(
            "/api/decision/insights",
            json={
                "product_id": "p-1",
                "title": "Offline drafting",
                "evidence_refs": [evidence_id],
            },
        ).json()["insight"]
        opportunity = client.post(
            "/api/decision/opportunities",
            json={
                "product_id": "p-1",
                "title": "Offline PRD drafting",
                "insight_ids": [insight["id"]],
            },
        ).json()
        assert opportunity["opportunity"]["status"] == "proposed"
        assert opportunity["next_human_action"] == "edit_add_evidence_reject_or_submit_approval"

        blocked = client.post(f"/api/decision/opportunities/{opportunity['artifact_id']}/prd")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "opportunity_not_approved"

        submitted = client.post(
            f"/api/decision/opportunities/{opportunity['artifact_id']}/submit",
            json={"actor": "ou_pm", "reason": "ready for owner"},
        ).json()
        assert submitted["opportunity"]["status"] == "pending_approval"
        assert submitted["audit_id"]

        still_blocked = client.post(
            f"/api/decision/opportunities/{opportunity['artifact_id']}/prd"
        )
        assert still_blocked.status_code == 409

        evaluated = client.post(
            f"/api/decision/opportunities/{opportunity['artifact_id']}/evaluate",
            json={"method": "rice", "reach": 100, "impact": 2, "confidence": 0.8, "effort": 2},
        ).json()
        assert evaluated["opportunity"]["score"] > 0
        assert evaluated["opportunity"]["source_urls"][0].startswith("https://example.feishu.cn/")
