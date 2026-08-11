"""Parse Notion webhook events and enqueue connector sync tasks."""
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

from .config_store import NotionConfigStore, normalize_notion_page_id

PAGE_UPDATE_EVENT_TYPES = {
    "page.content_updated",
    "page.created",
    "page.properties_updated",
    "page.deleted",
    "page.locked",
    "page.unlocked",
    "page.moved",
    "page.undeleted",
}

DATABASE_UPDATE_EVENT_TYPES = {
    "database.content_updated",
    "database.schema_updated",
    "database.created",
    "database.deleted",
    "database.moved",
    "database.undeleted",
    "data_source.schema_updated",
}


def decode_request_body(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8") or "{}")


def extract_verification_token(payload: dict[str, Any]) -> str:
    return str(payload.get("verification_token") or "").strip()


def is_verification_handshake(payload: dict[str, Any]) -> bool:
    return bool(extract_verification_token(payload))


def extract_event_id(payload: dict[str, Any]) -> str:
    return str(payload.get("id") or payload.get("event_id") or "").strip()


def extract_event_type(payload: dict[str, Any]) -> str:
    return str(payload.get("type") or "").strip()


def extract_entity_id(payload: dict[str, Any]) -> str:
    entity = payload.get("entity")
    if isinstance(entity, dict):
        entity_id = str(entity.get("id") or "").strip()
        if entity_id:
            return entity_id
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("id", "page_id", "database_id"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return ""


def build_notion_source_url(page_id: str, *, source_url: str = "") -> str:
    normalized = normalize_notion_page_id(page_id)
    if source_url.strip():
        return source_url.strip()
    return f"notion://page/{normalized}"


def is_content_update_event(payload: dict[str, Any]) -> bool:
    event_type = extract_event_type(payload)
    if event_type in PAGE_UPDATE_EVENT_TYPES | DATABASE_UPDATE_EVENT_TYPES:
        return bool(extract_entity_id(payload))
    return False


def handle_notion_event_payload(
    payload: dict[str, Any],
    *,
    sync_store: ConnectorSyncStore,
    config_store: NotionConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
) -> dict[str, Any]:
    verification_token = extract_verification_token(payload)
    if verification_token:
        return {"kind": "verification", "verification_token": verification_token}

    event_id = extract_event_id(payload)
    if event_id and is_event_processed(sync_store, provider="notion", event_id=event_id):
        return {"kind": "duplicate", "event_id": event_id}

    if not is_content_update_event(payload):
        return {"kind": "ignored", "event_type": extract_event_type(payload)}

    entity_id = extract_entity_id(payload)
    page_id = normalize_notion_page_id(entity_id)
    match = config_store.find_project_for_page_id(page_id)
    if match is None:
        return {
            "kind": "ignored",
            "reason": "unmapped_page_id",
            "page_id": page_id,
            "event_type": extract_event_type(payload),
        }

    project_id, mapping = match
    source_url = mapping.source_url.strip() or build_notion_source_url(page_id)
    sync_payload = {
        "trigger": "webhook",
        "event_id": event_id,
        "page_id": page_id,
        "title": mapping.title,
        "source_url": source_url,
        "event_type": extract_event_type(payload),
        "timestamp": str(payload.get("timestamp") or "").strip(),
    }
    task = enqueue_sync_task(
        sync_store,
        project_id=project_id,
        provider="notion",
        payload=sync_payload,
        idempotency_key=build_sync_idempotency_key(
            project_id,
            "notion",
            resource=page_id,
            suffix=event_id or "webhook",
        ),
        new_id=new_id,
        now=now,
    )
    if event_id:
        mark_event_processed(
            sync_store,
            provider="notion",
            event_id=event_id,
            project_id=project_id,
            now=now,
        )
    return {
        "kind": "sync_enqueued",
        "project_id": project_id,
        "page_id": page_id,
        "task_id": task["id"],
        "deduplicated": bool(task.get("deduplicated")),
    }
