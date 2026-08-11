"""Parse Feishu webhook events and enqueue connector sync tasks."""
from __future__ import annotations

import json
from typing import Any, Callable

from pm_pal.connectors.sync import (
    ConnectorSyncStore,
    build_sync_idempotency_key,
    enqueue_sync_task,
    is_event_processed,
    mark_event_processed,
)

from .config_store import FeishuConfigStore, FeishuDocMapping
from .crypto import FeishuDecryptError, decrypt_feishu_event_payload
from .models import FeishuChallengeEvent, FeishuEventEnvelope

DOCUMENT_UPDATE_EVENT_TYPES = {
    "drive.file.edit_v1",
    "drive.file.title_updated_v1",
    "docx.document.updated_v1",
}


def resolve_event_payload(
    raw_payload: dict[str, Any],
    *,
    encrypt_key: str = "",
) -> dict[str, Any]:
    encrypted = str(raw_payload.get("encrypt") or "").strip()
    if not encrypted:
        return raw_payload
    normalized_key = str(encrypt_key or "").strip()
    if not normalized_key:
        raise FeishuDecryptError("encrypted event received but encrypt_key is not configured")
    return decrypt_feishu_event_payload(encrypt_key=normalized_key, encrypted=encrypted)


def extract_event_id(payload: dict[str, Any]) -> str:
    header = payload.get("header")
    if isinstance(header, dict):
        event_id = str(header.get("event_id") or "").strip()
        if event_id:
            return event_id
    return str(payload.get("uuid") or payload.get("event_id") or "").strip()


def extract_event_type(payload: dict[str, Any]) -> str:
    header = payload.get("header")
    if isinstance(header, dict):
        event_type = str(header.get("event_type") or "").strip()
        if event_type:
            return event_type
    return str(payload.get("type") or "").strip()


def extract_doc_token(payload: dict[str, Any]) -> str:
    event = payload.get("event")
    if not isinstance(event, dict):
        return ""
    for key in ("file_token", "obj_token", "document_id", "token"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    nested = event.get("file")
    if isinstance(nested, dict):
        return str(nested.get("file_token") or nested.get("token") or "").strip()
    return ""


def extract_document_kind(payload: dict[str, Any], *, fallback: str = "docx") -> str:
    event = payload.get("event")
    if isinstance(event, dict):
        for key in ("file_type", "obj_type", "document_kind"):
            value = str(event.get(key) or "").strip().lower()
            if value == "doc":
                return "docs"
            if value:
                return value
    return fallback


def build_feishu_source_url(doc_token: str, document_kind: str) -> str:
    normalized_kind = str(document_kind or "docx").strip().lower() or "docx"
    normalized_token = str(doc_token or "").strip()
    return f"feishu://{normalized_kind}/{normalized_token}"


def is_document_update_event(payload: dict[str, Any]) -> bool:
    event_type = extract_event_type(payload)
    if event_type in DOCUMENT_UPDATE_EVENT_TYPES:
        return bool(extract_doc_token(payload))
    return False


def handle_feishu_event_payload(
    payload: dict[str, Any],
    *,
    sync_store: ConnectorSyncStore,
    config_store: FeishuConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> dict[str, Any]:
    envelope = FeishuEventEnvelope.model_validate(payload)
    if envelope.is_challenge():
        challenge = FeishuChallengeEvent.model_validate(payload)
        return {"kind": "challenge", "challenge": challenge.challenge}

    event_id = extract_event_id(payload)
    if event_id and is_event_processed(sync_store, provider="feishu", event_id=event_id):
        return {"kind": "duplicate", "event_id": event_id}

    if not is_document_update_event(payload):
        return {"kind": "ignored", "event_type": extract_event_type(payload)}

    doc_token = extract_doc_token(payload)
    match = config_store.find_project_for_doc_token(doc_token)
    if match is None:
        return {
            "kind": "ignored",
            "reason": "unmapped_doc_token",
            "doc_token": doc_token,
            "event_type": extract_event_type(payload),
        }

    project_id, mapping = match
    document_kind = extract_document_kind(payload, fallback=mapping.document_kind)
    source_url = mapping.source_url.strip() or build_feishu_source_url(
        doc_token, document_kind
    )
    sync_payload = {
        "trigger": "webhook",
        "event_id": event_id,
        "doc_token": doc_token,
        "document_kind": document_kind,
        "title": mapping.title,
        "source_url": source_url,
    }
    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="feishu",
        payload=sync_payload,
        idempotency_key=build_sync_idempotency_key(
            project_id,
            "feishu",
            resource=doc_token,
            suffix=event_id or "webhook",
        ),
        new_id=new_id,
        now=now,
    )
    if event_id:
        mark_event_processed(
            sync_store,
            provider="feishu",
            event_id=event_id,
            project_id=project_id,
            now=now,
        )
    return {
        "kind": "sync_enqueued",
        "project_id": project_id,
        "doc_token": doc_token,
        "task_id": task["id"],
        "deduplicated": bool(task.get("deduplicated")),
    }


def decode_request_body(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8") or "{}")
