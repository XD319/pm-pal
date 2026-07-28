from __future__ import annotations

import asyncio

import pytest

from prd_pal.connectors.feishu import FeishuHTTPResponse
from prd_pal.product_decision.delivery import (
    DeliveryService,
    FeishuBitableDeliveryTarget,
    FeishuProjectDeliveryTarget,
    build_delivery_fields,
    delivery_idempotency_key,
)
from prd_pal.product_decision.models import PrdVersion, PrdVersionStatus
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.utils.time import utc_now_iso


class _FakeHTTP:
    def __init__(self, responses: list[FeishuHTTPResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, path, *, headers=None, json_body=None):
        self.calls.append((method, path, json_body))
        if path.endswith("/tenant_access_token/internal"):
            return FeishuHTTPResponse(
                status_code=200, json_body={"code": 0, "tenant_access_token": "t"}
            )
        if not self.responses:
            raise AssertionError(f"unexpected {method} {path}")
        return self.responses.pop(0)


async def _seed_ready_prd(repo: ProductDecisionRepository) -> PrdVersion:
    await repo.initialize()
    now = utc_now_iso()
    version = PrdVersion(
        id="prd-1:v1",
        prd_id="prd-1",
        product_id="p-1",
        opportunity_id="opp-1",
        version=1,
        title="Offline drafting",
        markdown="# Offline drafting\n\nAcceptance: works offline",
        status=PrdVersionStatus.ready_for_delivery,
        quality_decision="pass",
        evidence_refs=["evidence-1"],
        source_urls=["https://example.feishu.cn/docx/doc-1"],
        audit_id="audit-seed",
        created_at=now,
        updated_at=now,
        metadata={"acceptance_criteria": ["works offline"], "risks": ["sync lag"], "owner": "ou_owner"},
    )
    await repo.insert_prd_version(version)
    return version


@pytest.mark.asyncio
async def test_export_gate_field_mapping_idempotency_and_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "app")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "secret")
    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    version = await _seed_ready_prd(repo)
    draft = version.model_copy(update={"id": "prd-draft:v1", "prd_id": "prd-draft", "status": PrdVersionStatus.draft})
    await repo.insert_prd_version(draft)

    service = DeliveryService(repo)
    with pytest.raises(Exception) as blocked:
        await service.export_prd(
            draft.id,
            target=FeishuBitableDeliveryTarget(app_token="app", table_id="tbl"),
        )
    assert blocked.value.code == "prd_not_ready_for_delivery"

    http = _FakeHTTP(
        [
            FeishuHTTPResponse(
                status_code=200,
                json_body={"code": 0, "data": {"record": {"record_id": "rec-1"}}},
            )
        ]
    )
    target = FeishuBitableDeliveryTarget(
        app_token="appTok",
        table_id="tblTok",
        field_mapping={
            "requirement": "需求",
            "user_stories": "用户故事",
            "acceptance_criteria": "验收标准",
            "risks": "风险",
            "owner": "负责人",
            "quality_decision": "质量结论",
            "prd_link": "PRD",
            "evidence_links": "证据",
            "audit_id": "审计ID",
        },
        http_client=http,
    )
    first, receipt = await service.export_prd(version.id, target=target, actor_open_id="ou_owner")
    assert str(first.status) == "succeeded"
    assert first.external_url.endswith("record=rec-1")
    assert first.field_payload["requirement"] == "Offline drafting"
    assert "质量结论" in http.calls[-1][2]["fields"]
    assert receipt.audit_id

    second, _ = await service.export_prd(version.id, target=target, actor_open_id="ou_owner")
    assert second.id == first.id
    assert second.idempotency_key == delivery_idempotency_key(version.id, "feishu_bitable")
    assert len([call for call in http.calls if call[0] == "POST" and "records" in call[1]]) == 1


@pytest.mark.asyncio
async def test_project_permission_failure_degrades_to_bitable(tmp_path, monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "app")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "secret")
    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    version = await _seed_ready_prd(repo)
    http = _FakeHTTP(
        [
            FeishuHTTPResponse(
                status_code=403,
                json_body={"code": 99991672, "msg": "permission denied"},
            ),
            FeishuHTTPResponse(
                status_code=200,
                json_body={"code": 0, "data": {"record": {"record_id": "rec-fallback"}}},
            ),
        ]
    )
    target = FeishuProjectDeliveryTarget(
        project_key="proj-1",
        field_mapping={"requirement": "title"},
        fallback=FeishuBitableDeliveryTarget(
            app_token="appTok", table_id="tblTok", http_client=http
        ),
        http_client=http,
    )
    export, _ = await DeliveryService(repo).export_prd(version.id, target=target)
    assert str(export.status) == "degraded"
    assert export.degraded_from == "feishu_project"
    assert export.external_id == "rec-fallback"
    assert "permission" in export.failure_reason.lower() or "Permission" in export.failure_reason


def test_build_delivery_fields_includes_trace_links():
    version = PrdVersion(
        id="prd-1:v1",
        prd_id="prd-1",
        product_id="p-1",
        version=1,
        title="T",
        markdown="body",
        quality_decision="pass",
        evidence_refs=["e1"],
        source_urls=["https://example.feishu.cn/docx/e1"],
    )
    fields = build_delivery_fields(version, audit_id="audit-1")
    assert fields["audit_id"] == "audit-1"
    assert fields["prd_link"] == "prd-1:v1"
    assert fields["evidence_links"] == ["https://example.feishu.cn/docx/e1"]
