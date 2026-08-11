"""Feishu notification renderer and sender."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pm_pal.notifications.base import BaseNotifier
from pm_pal.notifications.models import (
    NotificationDeliveryResult,
    NotificationEvent,
    NotificationType,
)

_FALSE_VALUES = {"0", "false", "no", "off"}
_DETAIL_BASE_URL_ENVS = (
    "MARRDP_FEISHU_NOTIFICATION_DETAIL_BASE_URL",
    "MARRDP_PUBLIC_BASE_URL",
    "MARRDP_APP_BASE_URL",
)
_VALID_NOTIFICATION_CHANNELS = {"webhook", "openapi", "both"}
_VALID_RECEIVE_ID_TYPES = {"open_id", "user_id", "union_id", "email", "chat_id"}


@dataclass(frozen=True, slots=True)
class FeishuNotifierConfig:
    webhook_url: str = ""
    detail_base_url: str = ""
    dry_run: bool = True
    timeout_seconds: float = 10.0
    channels: str = "webhook"
    receive_id_type: str = "open_id"
    default_receive_id: str = ""
    open_base_url: str = "https://open.feishu.cn"
    app_id: str = ""
    app_secret: str = ""


@dataclass(frozen=True, slots=True)
class FeishuHTTPResponse:
    status_code: int
    json_body: dict[str, Any]


class _FeishuHTTPClient(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> FeishuHTTPResponse: ...


class _DefaultFeishuHTTPClient:
    USER_AGENT = "marrdp-feishu-notifier/1.0"

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> FeishuHTTPResponse:
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": self.USER_AGENT,
        }
        if headers:
            request_headers.update(headers)

        request = Request(
            url,
            data=json.dumps(json_body or {}).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            response = urlopen(request, timeout=timeout_seconds)
        except HTTPError as exc:
            return FeishuHTTPResponse(
                status_code=int(exc.code),
                json_body=_decode_json_body(exc.read()),
            )
        except URLError as exc:
            raise RuntimeError(
                f"Feishu notification delivery failed: {exc.reason or exc}"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(
                "Feishu notification delivery failed: request timed out"
            ) from exc

        with response:
            status_code_getter = getattr(response, "getcode", None)
            status_code = (
                int(status_code_getter()) if callable(status_code_getter) else 200
            )
            return FeishuHTTPResponse(
                status_code=status_code,
                json_body=_decode_json_body(response.read()),
            )


def _decode_json_body(raw_body: bytes) -> dict[str, Any]:
    normalized = bytes(raw_body or b"").strip()
    if not normalized:
        return {}
    try:
        decoded = json.loads(normalized.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"message": normalized.decode("utf-8", errors="replace")}
    return decoded if isinstance(decoded, dict) else {"data": decoded}


def _env_disabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def _env_float(name: str, *, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw.strip()))
    except ValueError:
        return default


def _env_choice(name: str, *, default: str, allowed: set[str]) -> str:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {allowed_values}")
    return raw


def load_feishu_notifier_config() -> FeishuNotifierConfig:
    webhook_url = str(
        os.getenv("MARRDP_FEISHU_NOTIFICATION_WEBHOOK_URL", "") or ""
    ).strip()
    detail_base_url = ""
    for env_name in _DETAIL_BASE_URL_ENVS:
        candidate = str(os.getenv(env_name, "") or "").strip()
        if candidate:
            detail_base_url = candidate.rstrip("/")
            break
    return FeishuNotifierConfig(
        webhook_url=webhook_url,
        detail_base_url=detail_base_url,
        dry_run=_env_disabled("MARRDP_FEISHU_NOTIFICATION_DRY_RUN", default=True),
        timeout_seconds=_env_float(
            "MARRDP_FEISHU_NOTIFICATION_TIMEOUT_SEC", default=10.0, minimum=1.0
        ),
        channels=_env_choice(
            "MARRDP_FEISHU_NOTIFICATION_CHANNELS",
            default="webhook",
            allowed=_VALID_NOTIFICATION_CHANNELS,
        ),
        receive_id_type=_env_choice(
            "MARRDP_FEISHU_NOTIFICATION_RECEIVE_ID_TYPE",
            default="open_id",
            allowed=_VALID_RECEIVE_ID_TYPES,
        ),
        default_receive_id=str(
            os.getenv("MARRDP_FEISHU_NOTIFICATION_DEFAULT_RECEIVE_ID", "") or ""
        ).strip(),
        open_base_url=str(
            os.getenv("MARRDP_FEISHU_OPEN_BASE_URL", "https://open.feishu.cn")
            or "https://open.feishu.cn"
        ).rstrip("/"),
        app_id=str(os.getenv("MARRDP_FEISHU_APP_ID", "") or "").strip(),
        app_secret=str(os.getenv("MARRDP_FEISHU_APP_SECRET", "") or "").strip(),
    )


def _normalize_review_status(event: NotificationEvent) -> str:
    metadata = event.metadata or {}
    status_from_metadata = str(metadata.get("review_run_status") or "").strip().lower()
    if status_from_metadata:
        return status_from_metadata

    mapping = {
        NotificationType.review_submitted: "submitted",
        NotificationType.review_running: "running",
        NotificationType.review_completed: "completed",
        NotificationType.review_failed: "failed",
        NotificationType.clarification_required: "clarification_required",
        NotificationType.execution_completed: "completed",
        NotificationType.execution_failed: "failed",
    }
    return mapping.get(event.event_type, "running")


def _status_label(status: str) -> str:
    mapping = {
        "submitted": "Submitted",
        "running": "Running",
        "completed": "Completed",
        "failed": "Failed",
        "clarification_required": "Clarification Required",
    }
    return mapping.get(status, status.replace("_", " ").title() or "Running")


def _status_template(status: str) -> str:
    mapping = {
        "submitted": "wathet",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "clarification_required": "orange",
    }
    return mapping.get(status, "blue")


class FeishuCardRenderer:
    def __init__(self, *, config: FeishuNotifierConfig | None = None) -> None:
        self._config = config or load_feishu_notifier_config()

    def render(self, event: NotificationEvent) -> dict[str, object]:
        status = _normalize_review_status(event)
        summary = (
            str(event.summary or event.title or "").strip()
            or f"Review {status.replace('_', ' ')}."
        )
        detail_url = self._resolve_detail_url(event)
        feishu_entry_url = self._resolve_feishu_entry_url(event)
        metadata = event.metadata or {}
        actor = str(metadata.get("actor") or "").strip()
        question_count = int(metadata.get("clarification_question_count", 0) or 0)

        note_segments = [
            f"Run ID: {event.run_id or '-'}",
            f"Status: {_status_label(status)}",
        ]
        if actor:
            note_segments.append(f"Actor: {actor}")
        if question_count:
            note_segments.append(f"Questions: {question_count}")

        elements: list[dict[str, Any]] = [
            {
                "tag": "div",
                "fields": [
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**Run ID**\n`{event.run_id or '-'}`",
                        },
                    },
                    {
                        "is_short": True,
                        "text": {
                            "tag": "lark_md",
                            "content": f"**Status**\n{_status_label(status)}",
                        },
                    },
                ],
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": " | ".join(note_segments)}
                ],
            },
        ]
        action_buttons = self._build_action_buttons(
            status=status,
            detail_url=detail_url,
            feishu_entry_url=feishu_entry_url,
        )
        if action_buttons:
            elements.append(
                {
                    "tag": "action",
                    "actions": action_buttons,
                }
            )

        return {
            "channel": "feishu",
            "dry_run": True,
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True, "enable_forward": True},
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"{_status_label(status)} | {event.run_id or event.title}",
                    },
                    "template": _status_template(status),
                },
                "elements": elements,
            },
        }

    def _resolve_detail_url(self, event: NotificationEvent) -> str:
        metadata = event.metadata or {}
        explicit_detail_url = str(metadata.get("detail_url") or "").strip()
        if explicit_detail_url:
            return explicit_detail_url
        run_id = str(event.run_id or "").strip()
        if not run_id:
            return ""
        result_context = self._resolve_result_context(metadata)
        base_url = self._config.detail_base_url
        if not base_url:
            return self._append_query_params(f"/run/{run_id}", result_context)
        return self._append_query_params(f"{base_url}/run/{run_id}", result_context)

    def _resolve_feishu_entry_url(self, event: NotificationEvent) -> str:
        metadata = event.metadata or {}
        explicit_entry_url = str(metadata.get("entry_url") or "").strip()
        if explicit_entry_url:
            return explicit_entry_url
        base_url = self._config.detail_base_url
        if not base_url:
            return "/feishu"
        return f"{base_url}/feishu"

    @staticmethod
    def _resolve_result_context(metadata: dict[str, Any]) -> dict[str, str]:
        resolved: dict[str, str] = {"embed": "feishu", "trigger_source": "feishu"}
        client_metadata = metadata.get("client_metadata")
        if isinstance(client_metadata, dict):
            for key in ("open_id", "tenant_key", "lang", "locale"):
                value = str(client_metadata.get(key) or "").strip()
                if value:
                    resolved[key] = value
        for key in ("open_id", "tenant_key", "lang", "locale"):
            value = str(metadata.get(key) or "").strip()
            if value and key not in resolved:
                resolved[key] = value
        return resolved

    @staticmethod
    def _append_query_params(url: str, params: dict[str, str]) -> str:
        parsed = urlsplit(str(url or "").strip())
        existing_pairs = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if key
        ]
        merged: dict[str, str] = {}
        for key, value in existing_pairs:
            if value:
                merged[key] = value
        for key, value in params.items():
            normalized_key = str(key or "").strip()
            normalized_value = str(value or "").strip()
            if normalized_key and normalized_value:
                merged[normalized_key] = normalized_value
        query = urlencode(merged)
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
        )

    def _build_action_buttons(
        self,
        *,
        status: str,
        detail_url: str,
        feishu_entry_url: str,
    ) -> list[dict[str, Any]]:
        if not detail_url and not feishu_entry_url:
            return []

        actions: list[dict[str, Any]] = []

        if detail_url:
            actions.append(self._build_button("查看最新结果", detail_url, primary=True))

        if status == "clarification_required" and detail_url:
            actions.append(
                self._build_button(
                    "继续澄清",
                    self._with_hash(detail_url, "clarification"),
                    primary=False,
                )
            )

        if status == "completed" and detail_url:
            actions.append(
                self._build_button(
                    "生成下一步交付",
                    self._with_hash(detail_url, "next-delivery"),
                    primary=False,
                )
            )

        if feishu_entry_url:
            actions.append(
                self._build_button("重新提交", feishu_entry_url, primary=False)
            )

        return actions[:3]

    @staticmethod
    def _build_button(label: str, url: str, *, primary: bool) -> dict[str, Any]:
        return {
            "tag": "button",
            "type": "primary" if primary else "default",
            "text": {"tag": "plain_text", "content": label},
            "url": url,
        }

    @staticmethod
    def _with_hash(url: str, section_id: str) -> str:
        parsed = urlsplit(str(url or "").strip())
        if not parsed.scheme and not parsed.netloc:
            base = parsed.path or ""
            query = f"?{parsed.query}" if parsed.query else ""
            return f"{base}{query}#{section_id}"
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.query, section_id)
        )


class FeishuWebhookSender:
    def __init__(
        self,
        *,
        config: FeishuNotifierConfig | None = None,
        http_client: _FeishuHTTPClient | None = None,
    ) -> None:
        self._config = config or load_feishu_notifier_config()
        self._http_client = http_client or _DefaultFeishuHTTPClient()

    def send(
        self, event: NotificationEvent, payload: dict[str, object]
    ) -> NotificationDeliveryResult:
        normalized_payload = dict(payload) if isinstance(payload, dict) else {}
        if not self._config.dry_run and not self._config.webhook_url:
            raise RuntimeError(
                "Feishu webhook notification requires MARRDP_FEISHU_NOTIFICATION_WEBHOOK_URL."
            )
        effective_dry_run = self._config.dry_run
        normalized_payload["dry_run"] = effective_dry_run

        delivery_metadata = {
            "mode": "dry_run" if effective_dry_run else "webhook",
        }
        if self._config.webhook_url:
            parsed = urlparse(self._config.webhook_url)
            delivery_metadata["target_host"] = parsed.netloc

        if effective_dry_run:
            return NotificationDeliveryResult(
                payload=normalized_payload,
                delivery_metadata=delivery_metadata,
                dry_run=True,
            )

        response = self._http_client.post_json(
            self._config.webhook_url,
            json_body=normalized_payload,
            timeout_seconds=self._config.timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Feishu notification delivery failed with HTTP {response.status_code}: {response.json_body!r}"
            )
        api_code = response.json_body.get("code")
        if api_code not in (None, 0):
            raise RuntimeError(
                f"Feishu notification delivery failed with code={api_code}: {response.json_body!r}"
            )

        delivery_metadata["status_code"] = response.status_code
        return NotificationDeliveryResult(
            payload=normalized_payload,
            delivery_metadata=delivery_metadata,
            dry_run=False,
        )


class FeishuOpenAPISender:
    def __init__(
        self,
        *,
        config: FeishuNotifierConfig | None = None,
        http_client: _FeishuHTTPClient | None = None,
    ) -> None:
        self._config = config or load_feishu_notifier_config()
        self._http_client = http_client or _DefaultFeishuHTTPClient()

    def send(
        self, event: NotificationEvent, payload: dict[str, object]
    ) -> NotificationDeliveryResult:
        normalized_payload = dict(payload) if isinstance(payload, dict) else {}
        normalized_payload["dry_run"] = self._config.dry_run
        receive_id = self._resolve_receive_id(event)
        delivery_metadata = {
            "mode": "dry_run" if self._config.dry_run else "openapi",
            "receive_id_type": self._config.receive_id_type,
        }
        if receive_id:
            delivery_metadata["receive_id"] = receive_id

        if self._config.dry_run:
            return NotificationDeliveryResult(
                payload=normalized_payload,
                delivery_metadata=delivery_metadata,
                dry_run=True,
            )

        if not self._config.app_id or not self._config.app_secret:
            raise RuntimeError(
                "Feishu OpenAPI notification requires MARRDP_FEISHU_APP_ID and MARRDP_FEISHU_APP_SECRET."
            )
        if not receive_id:
            raise RuntimeError(
                "Feishu OpenAPI notification requires a recipient from run context or "
                "MARRDP_FEISHU_NOTIFICATION_DEFAULT_RECEIVE_ID."
            )

        tenant_access_token = self._tenant_access_token()
        card_payload = normalized_payload.get("card")
        if not isinstance(card_payload, dict):
            raise RuntimeError(
                "Feishu OpenAPI notification requires an interactive card payload."
            )

        response = self._http_client.post_json(
            f"{self._config.open_base_url}/open-apis/im/v1/messages?{urlencode({'receive_id_type': self._config.receive_id_type})}",
            headers={"Authorization": f"Bearer {tenant_access_token}"},
            json_body={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card_payload, ensure_ascii=False),
                "uuid": event.notification_id,
            },
            timeout_seconds=self._config.timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Feishu OpenAPI notification failed with HTTP {response.status_code}: {response.json_body!r}"
            )
        api_code = response.json_body.get("code")
        if api_code not in (None, 0):
            raise RuntimeError(
                f"Feishu OpenAPI notification failed with code={api_code}: {response.json_body!r}"
            )

        delivery_metadata["status_code"] = response.status_code
        message_id = _extract_message_id(response.json_body)
        if message_id:
            delivery_metadata["message_id"] = message_id
        return NotificationDeliveryResult(
            payload=normalized_payload,
            delivery_metadata=delivery_metadata,
            dry_run=False,
        )

    def _tenant_access_token(self) -> str:
        response = self._http_client.post_json(
            f"{self._config.open_base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json_body={
                "app_id": self._config.app_id,
                "app_secret": self._config.app_secret,
            },
            timeout_seconds=self._config.timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Feishu OpenAPI auth failed with HTTP {response.status_code}: {response.json_body!r}"
            )
        api_code = response.json_body.get("code")
        if api_code not in (None, 0):
            raise RuntimeError(
                f"Feishu OpenAPI auth failed with code={api_code}: {response.json_body!r}"
            )
        data = response.json_body.get("data")
        if not isinstance(data, dict):
            data = {}
        token = str(
            response.json_body.get("tenant_access_token")
            or data.get("tenant_access_token")
            or ""
        ).strip()
        if not token:
            raise RuntimeError(
                "Feishu OpenAPI auth failed: tenant_access_token missing from response."
            )
        return token

    def _resolve_receive_id(self, event: NotificationEvent) -> str:
        metadata = event.metadata or {}
        client_metadata = metadata.get("client_metadata")
        if not isinstance(client_metadata, dict):
            client_metadata = {}
        keys_by_type = {
            "open_id": ("open_id",),
            "user_id": ("user_id",),
            "union_id": ("union_id",),
            "email": ("email",),
            "chat_id": ("chat_id",),
        }
        for key in keys_by_type.get(self._config.receive_id_type, ()):
            value = str(client_metadata.get(key) or metadata.get(key) or "").strip()
            if value:
                return value
        return self._config.default_receive_id


def _extract_message_id(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        message_id = data.get("message_id")
        message = data.get("message")
        if not message_id and isinstance(message, dict):
            message_id = message.get("message_id")
        if isinstance(message_id, str):
            return message_id.strip()
    return ""


class FeishuWebhookNotifier(BaseNotifier):
    channel = "feishu_webhook"
    description = (
        "Render and dispatch a Feishu interactive-card notification through webhook."
    )

    def __init__(
        self,
        *,
        renderer: FeishuCardRenderer | None = None,
        sender: FeishuWebhookSender | None = None,
    ) -> None:
        self._renderer = renderer or FeishuCardRenderer()
        self._sender = sender or FeishuWebhookSender()

    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        payload = self._renderer.render(event)
        payload["channel"] = self.channel
        payload["delivery_method"] = "webhook"
        return payload

    def send_payload(
        self,
        event: NotificationEvent,
        payload: dict[str, object],
    ) -> NotificationDeliveryResult:
        return self._sender.send(event, payload)


class FeishuOpenAPINotifier(BaseNotifier):
    channel = "feishu_openapi"
    description = (
        "Render and dispatch a Feishu interactive-card notification through OpenAPI."
    )

    def __init__(
        self,
        *,
        renderer: FeishuCardRenderer | None = None,
        sender: FeishuOpenAPISender | None = None,
    ) -> None:
        self._renderer = renderer or FeishuCardRenderer()
        self._sender = sender or FeishuOpenAPISender()

    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        payload = self._renderer.render(event)
        payload["channel"] = self.channel
        payload["delivery_method"] = "openapi"
        return payload

    def send_payload(
        self,
        event: NotificationEvent,
        payload: dict[str, object],
    ) -> NotificationDeliveryResult:
        return self._sender.send(event, payload)


class FeishuNotifier(FeishuWebhookNotifier):
    channel = "feishu"
    description = (
        "Render and optionally dispatch a Feishu interactive-card webhook notification."
    )

    def build_payload(self, event: NotificationEvent) -> dict[str, object]:
        payload = super().build_payload(event)
        payload["channel"] = self.channel
        return payload


def resolve_feishu_notifiers(
    config: FeishuNotifierConfig | None = None,
) -> tuple[BaseNotifier, ...]:
    resolved = config or load_feishu_notifier_config()
    renderer = FeishuCardRenderer(config=resolved)
    if resolved.channels == "openapi":
        return (
            FeishuOpenAPINotifier(
                renderer=renderer, sender=FeishuOpenAPISender(config=resolved)
            ),
        )
    if resolved.channels == "both":
        return (
            FeishuOpenAPINotifier(
                renderer=renderer, sender=FeishuOpenAPISender(config=resolved)
            ),
            FeishuWebhookNotifier(
                renderer=renderer, sender=FeishuWebhookSender(config=resolved)
            ),
        )
    return (
        FeishuNotifier(renderer=renderer, sender=FeishuWebhookSender(config=resolved)),
    )
