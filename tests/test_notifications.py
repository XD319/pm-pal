from __future__ import annotations

import json

import pytest

from pm_pal.monitoring import read_audit_events
from pm_pal.notifications import (
    BaseNotifier,
    FeishuCardRenderer,
    FeishuNotifier,
    FeishuNotifierConfig,
    FeishuOpenAPISender,
    FeishuWebhookSender,
    NotificationDeliveryResult,
    NotificationType,
    build_notification_event,
    build_notification_record,
    dispatch_notification,
    read_notification_records,
    resolve_feishu_notifiers,
)
from pm_pal.notifications.feishu import FeishuHTTPResponse


@pytest.mark.parametrize(
    "notification_type",
    [
        NotificationType.approval_requested,
        NotificationType.blocked_by_risk,
        NotificationType.executor_handoff_created,
        NotificationType.execution_completed,
        NotificationType.execution_failed,
    ],
)
def test_dispatch_notification_supports_all_notification_types(
    tmp_path, notification_type: NotificationType
) -> None:
    result = dispatch_notification(
        tmp_path,
        notification_type=notification_type,
        title=f"Notification: {notification_type.value}",
        summary="dry-run notification",
        run_id="20260308T060708Z",
        bundle_id="bundle-20260308T060708Z",
        task_id="bundle-20260308T060708Z:implementation_pack",
        metadata={"severity": "info"},
        audit_context={
            "actor": "workflow-bot",
            "source": "mcp",
            "tool_name": "test_tool",
            "client_metadata": {"request_id": "req-1"},
        },
    )

    records = read_notification_records(tmp_path)
    audit_events = read_audit_events(tmp_path)

    assert result.status == "dispatched"
    assert len(result.dispatches) == 2
    assert len(records) == 2
    assert len(audit_events) == 2
    assert {record["channel"] for record in records} == {"feishu", "wecom"}
    assert {record["event_type"] for record in records} == {notification_type.value}
    assert {record["notification_id"] for record in records} == {result.notification_id}
    assert {record["dispatch_status"] for record in records} == {"dispatched"}
    assert all(record["created_at"] for record in records)
    assert all(record["dispatched_at"] for record in records)
    assert records[0]["metadata"]["actor"] == "workflow-bot"
    assert records[0]["metadata"]["tool_name"] == "test_tool"
    assert records[0]["metadata"]["client_metadata"]["request_id"] == "req-1"
    assert all(record["payload"]["dry_run"] is True for record in records)
    assert [event["operation"] for event in audit_events] == [
        "notification_dispatch",
        "notification_dispatch",
    ]
    assert {event["status"] for event in audit_events} == {"dispatched"}


def test_build_notification_record_captures_dispatch_fields() -> None:
    record = build_notification_record(
        notification_type=NotificationType.execution_failed,
        title="Execution failed: bundle-1:implementation_pack",
        summary="Validation failed in dry-run mode.",
        run_id="20260308T060709Z",
        bundle_id="bundle-20260308T060709Z",
        task_id="bundle-20260308T060709Z:implementation_pack",
        metadata={"actor": "codex-worker-1"},
        audit_context={
            "source": "mcp",
            "tool_name": "update_execution_task",
        },
        channel="feishu",
        payload={"dry_run": True, "channel": "feishu"},
        dispatch_status="dispatched",
        dispatched_at="2026-03-08T06:07:10+00:00",
    )

    assert record.event_type == NotificationType.execution_failed
    assert record.channel == "feishu"
    assert record.dispatch_status == "dispatched"
    assert record.payload["dry_run"] is True
    assert record.metadata["actor"] == "codex-worker-1"
    assert record.metadata["source"] == "mcp"
    assert record.metadata["tool_name"] == "update_execution_task"
    assert record.dispatched_at == "2026-03-08T06:07:10+00:00"


class _BrokenNotifier(BaseNotifier):
    channel = "broken"
    description = "Always fail when building a payload."

    def build_payload(self, event):
        raise RuntimeError(f"cannot render {event.event_type}")


class _OpenAPIFailureNotifier(BaseNotifier):
    channel = "feishu_openapi"

    def build_payload(self, event):
        return {"channel": self.channel, "dry_run": False}

    def send_payload(self, event, payload):
        raise RuntimeError("openapi unavailable")


class _WebhookSuccessNotifier(BaseNotifier):
    channel = "feishu_webhook"

    def build_payload(self, event):
        return {"channel": self.channel, "dry_run": False}

    def send_payload(self, event, payload):
        return NotificationDeliveryResult(
            payload=payload,
            delivery_metadata={"mode": "webhook", "status_code": 200},
            dry_run=False,
        )


class _RecordingFeishuHTTPClient:
    def __init__(self, responses: list[FeishuHTTPResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        timeout_seconds: float = 10.0,
    ) -> FeishuHTTPResponse:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers or {}),
                "json_body": dict(json_body or {}),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.pop(0)


def test_dispatch_notification_persists_failed_dispatches_without_silencing_errors(
    tmp_path,
) -> None:
    result = dispatch_notification(
        tmp_path,
        notification_type=NotificationType.blocked_by_risk,
        title="Bundle blocked by risk",
        summary="Critical dependency risk remains unresolved.",
        run_id="20260308T060710Z",
        bundle_id="bundle-20260308T060710Z",
        notifiers=[_BrokenNotifier()],
        audit_context={"tool_name": "approve_handoff", "source": "mcp"},
    )

    records = read_notification_records(tmp_path)
    audit_events = read_audit_events(tmp_path)

    assert result.status == "failed"
    assert len(records) == 1
    assert records[0]["event_type"] == "blocked_by_risk"
    assert records[0]["channel"] == "broken"
    assert records[0]["dispatch_status"] == "failed"
    assert records[0]["error_message"] == "cannot render blocked_by_risk"
    assert records[0]["payload"] == {}
    assert len(audit_events) == 1
    assert audit_events[0]["operation"] == "notification_dispatch"
    assert audit_events[0]["status"] == "failed"
    assert (
        audit_events[0]["details"]["error_message"] == "cannot render blocked_by_risk"
    )


def test_dispatch_notification_records_each_feishu_channel_when_one_fails(
    tmp_path,
) -> None:
    result = dispatch_notification(
        tmp_path,
        notification_type=NotificationType.review_completed,
        title="Review completed",
        summary="OpenAPI failed but webhook was attempted separately.",
        run_id="20260308T060715Z",
        notifiers=[_OpenAPIFailureNotifier(), _WebhookSuccessNotifier()],
    )

    records = read_notification_records(tmp_path)
    audit_events = read_audit_events(tmp_path)

    assert result.status == "partial_success"
    assert [record["channel"] for record in records] == [
        "feishu_openapi",
        "feishu_webhook",
    ]
    assert [record["dispatch_status"] for record in records] == ["failed", "dispatched"]
    assert records[0]["error_message"] == "openapi unavailable"
    assert records[1]["delivery_metadata"] == {"mode": "webhook", "status_code": 200}
    assert [event["status"] for event in audit_events] == ["failed", "dispatched"]


@pytest.mark.parametrize(
    ("notification_type", "expected_status", "expected_template"),
    [
        (NotificationType.review_submitted, "Submitted", "wathet"),
        (NotificationType.review_running, "Running", "blue"),
        (NotificationType.review_completed, "Completed", "green"),
        (NotificationType.review_failed, "Failed", "red"),
        (NotificationType.clarification_required, "Clarification Required", "orange"),
    ],
)
def test_feishu_renderer_builds_review_status_cards(
    notification_type: NotificationType,
    expected_status: str,
    expected_template: str,
) -> None:
    renderer = FeishuCardRenderer(
        config=FeishuNotifierConfig(
            detail_base_url="https://review.example.test",
            dry_run=True,
        )
    )
    event = build_notification_event(
        notification_type=notification_type,
        title=f"Review update: {notification_type.value}",
        summary="Structured review update.",
        run_id="20260308T060711Z",
    )

    payload = renderer.render(event)

    assert payload["channel"] == "feishu"
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == expected_template
    assert expected_status in payload["card"]["header"]["title"]["content"]
    actions = payload["card"]["elements"][-1]["actions"]
    assert actions[0]["text"]["content"] == "查看最新结果"
    assert actions[0]["url"] == (
        "https://review.example.test/run/20260308T060711Z?embed=feishu&trigger_source=feishu"
    )
    assert actions[-1]["text"]["content"] == "重新提交"
    assert actions[-1]["url"] == "https://review.example.test/feishu"
    fields = payload["card"]["elements"][0]["fields"]
    assert "`20260308T060711Z`" in fields[0]["text"]["content"]
    assert expected_status in fields[1]["text"]["content"]


def test_feishu_renderer_includes_clarification_shortcut_for_clarification_required() -> (
    None
):
    renderer = FeishuCardRenderer(
        config=FeishuNotifierConfig(
            detail_base_url="https://review.example.test",
            dry_run=True,
        )
    )
    event = build_notification_event(
        notification_type=NotificationType.clarification_required,
        title="Review needs clarification",
        summary="Please provide additional input.",
        run_id="20260308T060713Z",
    )

    payload = renderer.render(event)
    actions = payload["card"]["elements"][-1]["actions"]
    labels = [item["text"]["content"] for item in actions]
    urls = [item["url"] for item in actions]

    assert labels == ["查看最新结果", "继续澄清", "重新提交"]
    assert urls[1].endswith(
        "/run/20260308T060713Z?embed=feishu&trigger_source=feishu#clarification"
    )


def test_feishu_renderer_includes_next_delivery_shortcut_for_completed() -> None:
    renderer = FeishuCardRenderer(
        config=FeishuNotifierConfig(
            detail_base_url="https://review.example.test",
            dry_run=True,
        )
    )
    event = build_notification_event(
        notification_type=NotificationType.review_completed,
        title="Review completed",
        summary="Ready for handoff.",
        run_id="20260308T060714Z",
    )

    payload = renderer.render(event)
    actions = payload["card"]["elements"][-1]["actions"]
    labels = [item["text"]["content"] for item in actions]
    urls = [item["url"] for item in actions]

    assert labels == ["查看最新结果", "生成下一步交付", "重新提交"]
    assert urls[1].endswith(
        "/run/20260308T060714Z?embed=feishu&trigger_source=feishu#next-delivery"
    )


def test_dispatch_notification_records_feishu_dry_run_delivery_metadata(
    tmp_path,
) -> None:
    notifier = FeishuNotifier(
        renderer=FeishuCardRenderer(
            config=FeishuNotifierConfig(
                detail_base_url="https://review.example.test",
                dry_run=True,
            )
        )
    )

    result = dispatch_notification(
        tmp_path,
        notification_type=NotificationType.review_completed,
        title="Review completed: 20260308T060712Z",
        summary="Review completed and is ready for inspection.",
        run_id="20260308T060712Z",
        metadata={"review_run_status": "completed"},
        notifiers=[notifier],
    )

    records = read_notification_records(tmp_path)

    assert result.status == "dispatched"
    assert len(records) == 1
    assert records[0]["channel"] == "feishu"
    assert records[0]["dry_run"] is True
    assert records[0]["payload"]["dry_run"] is True
    assert records[0]["delivery_metadata"]["mode"] == "dry_run"
    assert records[0]["payload"]["card"]["header"]["template"] == "green"


def test_resolve_feishu_notifiers_supports_both_channels_as_separate_records(
    tmp_path,
) -> None:
    config = FeishuNotifierConfig(
        detail_base_url="https://review.example.test",
        dry_run=True,
        channels="both",
    )

    result = dispatch_notification(
        tmp_path,
        notification_type=NotificationType.review_completed,
        title="Review completed: 20260308T060716Z",
        summary="Review completed and is ready for inspection.",
        run_id="20260308T060716Z",
        metadata={
            "review_run_status": "completed",
            "client_metadata": {"open_id": "ou_1"},
        },
        notifiers=resolve_feishu_notifiers(config),
    )
    records = read_notification_records(tmp_path)

    assert result.status == "dispatched"
    assert [record["channel"] for record in records] == [
        "feishu_openapi",
        "feishu_webhook",
    ]
    assert [record["delivery_metadata"]["mode"] for record in records] == [
        "dry_run",
        "dry_run",
    ]
    assert records[0]["delivery_metadata"]["receive_id"] == "ou_1"


def test_feishu_openapi_sender_posts_interactive_card_to_recipient() -> None:
    config = FeishuNotifierConfig(
        detail_base_url="https://review.example.test",
        dry_run=False,
        channels="openapi",
        receive_id_type="open_id",
        open_base_url="https://open.feishu.test",
        app_id="cli_a",
        app_secret="secret",
    )
    event = build_notification_event(
        notification_type=NotificationType.review_completed,
        title="Review completed",
        summary="Ready for inspection.",
        run_id="20260308T060717Z",
        metadata={"client_metadata": {"open_id": "ou_receiver"}},
    )
    payload = FeishuCardRenderer(config=config).render(event)
    http_client = _RecordingFeishuHTTPClient(
        [
            FeishuHTTPResponse(
                status_code=200,
                json_body={"code": 0, "tenant_access_token": "tenant-token"},
            ),
            FeishuHTTPResponse(
                status_code=200, json_body={"code": 0, "data": {"message_id": "om_1"}}
            ),
        ]
    )

    result = FeishuOpenAPISender(config=config, http_client=http_client).send(
        event, payload
    )

    assert result.dry_run is False
    assert result.delivery_metadata["mode"] == "openapi"
    assert result.delivery_metadata["message_id"] == "om_1"
    auth_request, message_request = http_client.requests
    assert (
        auth_request["url"]
        == "https://open.feishu.test/open-apis/auth/v3/tenant_access_token/internal"
    )
    assert auth_request["json_body"] == {"app_id": "cli_a", "app_secret": "secret"}
    assert (
        message_request["url"]
        == "https://open.feishu.test/open-apis/im/v1/messages?receive_id_type=open_id"
    )
    assert message_request["headers"] == {"Authorization": "Bearer tenant-token"}
    message_body = message_request["json_body"]
    assert message_body["receive_id"] == "ou_receiver"
    assert message_body["msg_type"] == "interactive"
    assert json.loads(message_body["content"]) == payload["card"]
    assert message_body["uuid"] == event.notification_id


def test_feishu_webhook_sender_requires_webhook_url_when_not_dry_run() -> None:
    config = FeishuNotifierConfig(dry_run=False, webhook_url="")
    sender = FeishuWebhookSender(config=config)
    event = build_notification_event(
        notification_type=NotificationType.review_completed,
        title="Review completed",
        run_id="20260308T060718Z",
    )

    with pytest.raises(RuntimeError, match="MARRDP_FEISHU_NOTIFICATION_WEBHOOK_URL"):
        sender.send(event, {"card": {}, "channel": "feishu"})
