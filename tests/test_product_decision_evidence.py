from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.connectors.errors import ConnectorNetworkError, ConnectorPermissionError
from prd_pal.connectors.feishu import FeishuHTTPResponse
from prd_pal.platform import LocalArtifactStore, LocalJobQueue
from prd_pal.product_decision.feishu_client import FeishuEvidenceClient
from prd_pal.product_decision.models import (
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    SyncTrigger,
)
from prd_pal.product_decision.repository import ProductDecisionRepository
from prd_pal.product_decision.router import create_product_decision_router
from prd_pal.product_decision.scheduler import next_shanghai_0200
from prd_pal.product_decision.sync_service import (
    SHANGHAI_TZ,
    EvidenceSyncService,
    shanghai_day_key,
    sync_idempotency_key,
)


class _RecordingNotifications:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def notify(self, *, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))


class _FakeBitableHTTP:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = list(pages)
        self.calls: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> FeishuHTTPResponse:
        if path.endswith("/tenant_access_token/internal"):
            return FeishuHTTPResponse(
                status_code=200,
                json_body={"code": 0, "tenant_access_token": "tenant-token"},
            )
        self.calls.append(path)
        if not self.pages:
            raise AssertionError(f"Unexpected bitable call: {path}")
        body = self.pages.pop(0)
        return FeishuHTTPResponse(status_code=200, json_body=body)


class _ScriptedClient:
    def __init__(self, pages: list[Any]) -> None:
        self.pages = list(pages)
        self.calls = 0

    def fetch_page(self, source: EvidenceSource, *, cursor: str = ""):
        self.calls += 1
        if not self.pages:
            raise AssertionError("no more pages")
        return self.pages.pop(0)


def test_evidence_sync_is_idempotent_and_preserves_source_link(tmp_path) -> None:
    app = FastAPI()
    app.include_router(create_product_decision_router(db_path=tmp_path / "decision.sqlite3"))
    with TestClient(app) as client:
        source = client.post(
            "/api/decision/sources",
            json={
                "product_id": "p-1",
                "source_type": "feishu_bitable",
                "external_id": "app1:tbl1",
                "source_url": "https://example.feishu.cn/base/app1?table=tbl1",
                "display_name": "Research inbox",
                "field_mapping": {"content": "反馈", "author": "提交人"},
            },
        ).json()["source"]
        first = client.post(
            f"/api/decision/sources/{source['id']}/sync",
            json={
                "cursor": "1",
                "records": [
                    {
                        "external_id": "row-1",
                        "content": "Login is confusing",
                        "source_url": "https://example.feishu.cn/base/app1?table=tbl1&record=row-1",
                        "source_version": "v1",
                    }
                ],
            },
        ).json()
        second = client.post(
            f"/api/decision/sources/{source['id']}/sync",
            json={
                "cursor": "2",
                "records": [
                    {
                        "external_id": "row-1",
                        "content": "Login is confusing on mobile",
                        "source_version": "v2",
                    }
                ],
            },
        ).json()
        evidence = client.get("/api/decision/evidence?product_id=p-1&query=mobile").json()[
            "evidence"
        ]
        sources = client.get("/api/decision/sources?product_id=p-1").json()["sources"]

    assert first["synced_count"] == 1
    assert second["synced_count"] == 1
    assert len(evidence) == 1
    assert evidence[0]["content"] == "Login is confusing on mobile"
    assert sources[0]["sync_cursor"] == "2"
    assert sources[0]["sync_status"] == "succeeded"
    assert sources[0]["external_id"] == "app1:tbl1"
    assert sources[0]["field_mapping"]["content"] == "反馈"


@pytest.mark.asyncio
async def test_confirmed_evidence_and_downstream_artifacts_are_not_overwritten(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "app-secret")
    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    await repo.initialize()
    source = EvidenceSource(
        id="source-1",
        product_id="p-1",
        source_type=EvidenceSourceType.feishu_bitable,
        external_id="app:tbl",
        source_url="https://example.feishu.cn/base/app?table=tbl",
        display_name="Inbox",
        field_mapping={"content": "text"},
    )
    await repo.upsert_source(source)
    await repo.sync_evidence(
        "source-1",
        [
            EvidenceRecord(
                id="evidence-1",
                source_id="source-1",
                external_id="row-1",
                product_id="p-1",
                content="Original confirmed feedback",
                source_version="v1",
            )
        ],
        cursor='{"watermark":"v1"}',
    )
    await repo.mark_evidence_confirmed("evidence-1", confirmed=True)

    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    await artifacts.put_json(
        "confirmed-insight/evidence-1",
        {"id": "insight-1", "evidence_refs": ["evidence-1"], "status": "confirmed"},
    )

    await repo.sync_evidence(
        "source-1",
        [
            EvidenceRecord(
                id="evidence-1",
                source_id="source-1",
                external_id="row-1",
                product_id="p-1",
                content="Should not replace confirmed content",
                source_version="v2",
            )
        ],
        cursor='{"watermark":"v2"}',
    )
    listed = await repo.list_evidence(product_id="p-1")
    assert listed.value[0].content == "Original confirmed feedback"
    assert listed.value[0].confirmed is True
    assert listed.value[0].metadata.get("last_seen_version") == "v2"
    assert await artifacts.get_json("confirmed-insight/evidence-1") == {
        "id": "insight-1",
        "evidence_refs": ["evidence-1"],
        "status": "confirmed",
    }
    source_after = await repo.get_source("source-1")
    assert source_after.value.sync_cursor == '{"watermark":"v2"}'


@pytest.mark.asyncio
async def test_bitable_pagination_resumes_from_page_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARRDP_FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("MARRDP_FEISHU_APP_SECRET", "app-secret")
    http = _FakeBitableHTTP(
        [
            {
                "code": 0,
                "data": {
                    "has_more": True,
                    "page_token": "page-2",
                    "items": [
                        {
                            "record_id": "row-1",
                            "last_modified_time": "2026-07-28T01:00:00+08:00",
                            "fields": {"text": "First page"},
                        }
                    ],
                },
            },
            {
                "code": 0,
                "data": {
                    "has_more": False,
                    "page_token": "",
                    "items": [
                        {
                            "record_id": "row-2",
                            "last_modified_time": "2026-07-28T01:30:00+08:00",
                            "fields": {"text": "Second page"},
                        }
                    ],
                },
            },
        ]
    )
    client = FeishuEvidenceClient(http_client=http, page_size=1)
    source = EvidenceSource(
        id="source-bitable",
        product_id="p-1",
        source_type=EvidenceSourceType.feishu_bitable,
        external_id="appTok:tblTok",
        source_url="https://example.feishu.cn/base/appTok?table=tblTok",
        field_mapping={"content": "text"},
    )
    first = client.fetch_page(source, cursor="")
    assert first.done is False
    assert first.records[0].external_id == "row-1"
    assert json.loads(first.next_cursor)["page_token"] == "page-2"

    second = client.fetch_page(source, cursor=first.next_cursor)
    assert second.done is True
    assert second.records[0].external_id == "row-2"
    assert "page_token=page-2" in http.calls[1]

    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    await repo.initialize()
    await repo.upsert_source(source)
    notifications = _RecordingNotifications()
    service = EvidenceSyncService(
        repo,
        job_queue=LocalJobQueue(),
        notifications=notifications,
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        client=client,
    )
    # Rebuild client with fresh pages for full sync through service.
    http2 = _FakeBitableHTTP(
        [
            {
                "code": 0,
                "data": {
                    "has_more": True,
                    "page_token": "page-2",
                    "items": [
                        {
                            "record_id": "row-1",
                            "last_modified_time": "2026-07-28T01:00:00+08:00",
                            "fields": {"text": "First page"},
                        }
                    ],
                },
            },
            {
                "code": 0,
                "data": {
                    "has_more": False,
                    "items": [
                        {
                            "record_id": "row-2",
                            "last_modified_time": "2026-07-28T01:30:00+08:00",
                            "fields": {"text": "Second page"},
                        }
                    ],
                },
            },
        ]
    )
    service.client = FeishuEvidenceClient(http_client=http2, page_size=1)
    outcome = await service.sync_source("source-bitable", trigger=SyncTrigger.manual)
    assert outcome["status"] == "completed"
    assert outcome["result"]["synced_count"] == 2
    evidence = await repo.list_evidence(product_id="p-1")
    assert {item.external_id for item in evidence.value} == {"row-1", "row-2"}


@pytest.mark.asyncio
async def test_permission_and_network_failures_update_status_and_notify(
    tmp_path,
) -> None:
    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    await repo.initialize()
    await repo.upsert_source(
        EvidenceSource(
            id="source-fail",
            product_id="p-1",
            source_type=EvidenceSourceType.feishu_doc,
            external_id="doc-1",
            source_url="https://example.feishu.cn/docx/doc-1",
        )
    )

    class _PermClient:
        def fetch_page(self, source, *, cursor=""):
            raise ConnectorPermissionError("denied", source=source.id)

    notifications = _RecordingNotifications()
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    service = EvidenceSyncService(
        repo,
        job_queue=LocalJobQueue(),
        notifications=notifications,
        artifacts=artifacts,
        client=_PermClient(),
        admin_open_ids=["ou_admin"],
    )
    failed = await service.sync_source("source-fail", trigger=SyncTrigger.manual)
    assert failed["status"] == "failed"
    source = await repo.get_source("source-fail")
    assert source.value.sync_status == "failed"
    assert "denied" in source.value.last_error
    assert notifications.events[0][0] == "evidence_sync_failed"
    assert notifications.events[0][1]["admin_open_ids"] == ["ou_admin"]

    class _NetClient:
        def fetch_page(self, source, *, cursor=""):
            raise ConnectorNetworkError("timeout", source=source.id, retryable=True)

    service.client = _NetClient()
    # New day key path would be needed for a fresh job; use a distinct source.
    await repo.upsert_source(
        EvidenceSource(
            id="source-net",
            product_id="p-1",
            source_type=EvidenceSourceType.feishu_meeting_notes,
            external_id="doc-2",
            source_url="https://example.feishu.cn/docx/doc-2",
        )
    )
    service.job_queue = LocalJobQueue()
    net = await service.sync_source("source-net", trigger=SyncTrigger.scheduled)
    assert net["status"] == "failed"
    assert (await repo.get_source("source-net")).value.sync_status == "failed"


def test_manual_and_scheduled_share_idempotency_key() -> None:
    day = shanghai_day_key(datetime(2026, 7, 28, 10, 0, tzinfo=SHANGHAI_TZ))
    assert sync_idempotency_key("source-1", day_key=day) == "evidence-sync:source-1:2026-07-28"
    assert sync_idempotency_key("source-1", day_key=day) == sync_idempotency_key(
        "source-1", day_key=day
    )


def test_next_shanghai_0200_rolls_forward() -> None:
    before = datetime(2026, 7, 28, 1, 59, tzinfo=SHANGHAI_TZ)
    after = datetime(2026, 7, 28, 2, 0, tzinfo=SHANGHAI_TZ)
    assert next_shanghai_0200(before).hour == 2
    assert next_shanghai_0200(before).day == 28
    assert next_shanghai_0200(after).day == 29


@pytest.mark.asyncio
async def test_repeat_refresh_is_idempotent_same_day(tmp_path) -> None:
    from prd_pal.product_decision.feishu_client import FetchedEvidencePage

    repo = ProductDecisionRepository(tmp_path / "decision.sqlite3")
    await repo.initialize()
    await repo.upsert_source(
        EvidenceSource(
            id="source-repeat",
            product_id="p-1",
            source_type=EvidenceSourceType.feishu_doc,
            external_id="doc-repeat",
            source_url="https://example.feishu.cn/docx/doc-repeat",
        )
    )
    page = FetchedEvidencePage(
        records=[
            EvidenceRecord(
                id="",
                source_id="source-repeat",
                external_id="doc-repeat",
                product_id="p-1",
                content="Ship decision workspace",
                source_version="v1",
                source_url="https://example.feishu.cn/docx/doc-repeat",
            )
        ],
        next_cursor='{"watermark":"v1","done":true}',
        done=True,
        source_version="v1",
    )
    client = _ScriptedClient([page])
    service = EvidenceSyncService(
        repo,
        job_queue=LocalJobQueue(),
        notifications=_RecordingNotifications(),
        artifacts=LocalArtifactStore(tmp_path / "artifacts"),
        client=client,
    )
    now = datetime(2026, 7, 28, 9, 0, tzinfo=SHANGHAI_TZ)
    first = await service.sync_source(
        "source-repeat", trigger=SyncTrigger.scheduled, now=now
    )
    second = await service.sync_source(
        "source-repeat", trigger=SyncTrigger.manual, now=now
    )
    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert second["result"] == first["result"]
    assert client.calls == 1
