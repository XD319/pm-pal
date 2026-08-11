"""Sync handler that ingests Notion pages into project materials."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from pm_pal.connectors.auth import ConnectorAuthConfig, ConnectorAuthType
from pm_pal.connectors.notion import NotionConfig, NotionConnector
from pm_pal.connectors.sync import register_sync_handler
from pm_pal.integrations.notion.config_store import (
    NotionConfigStore,
    NotionPageMapping,
    normalize_notion_page_id,
)
from pm_pal.integrations.notion.events import build_notion_source_url


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _build_connector_config(config_store: NotionConfigStore, project_id: str) -> NotionConfig | None:
    config = config_store.get(project_id)
    token = config.resolved_integration_token()
    if not token:
        return None
    auth = ConnectorAuthConfig(
        auth_type=ConnectorAuthType.bearer_token,
        token=token,
        extra={"token_env": "MARRDP_NOTION_TOKEN"},
    )
    return NotionConfig(
        auth=auth,
        base_url=config.resolved_base_url(),
        api_version=str(
            os.getenv("MARRDP_NOTION_API_VERSION", "2022-06-28") or "2022-06-28"
        ).strip(),
    )


def _page_edited_after(document_metadata: dict[str, Any], since: str) -> bool:
    since_dt = _parse_iso(since)
    if since_dt is None:
        return True
    edited = _parse_iso(str(document_metadata.get("last_edited_time") or ""))
    if edited is None:
        return True
    return edited > since_dt


def _sync_single_page(
    *,
    project_store: Any,
    project_id: str,
    page_id: str,
    mapping: NotionPageMapping,
    payload: dict[str, Any],
    connector: NotionConnector,
    upsert_source: Callable[..., dict[str, Any]] | None,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    since: str,
) -> dict[str, Any] | None:
    source_url = (
        str(payload.get("source_url") or "").strip()
        or mapping.source_url.strip()
        or build_notion_source_url(page_id)
    )
    title = str(payload.get("title") or mapping.title or "notion-page").strip()
    document = connector.get_content(source_url)
    metadata_extra = dict(document.metadata.extra or {})
    if since and not _page_edited_after(metadata_extra, since):
        return None
    metadata_extra.update(
        {
            "origin": "notion_sync",
            "mime_type": "text/markdown",
            "filename": f"{title}.md",
            "page_id": normalize_notion_page_id(page_id),
        }
    )
    if upsert_source is not None:
        result = upsert_source(
            project_store,
            project_id=project_id,
            title=title,
            source_type="notion",
            content=document.content_markdown,
            source_url=source_url,
            metadata_extra=metadata_extra,
            new_id=new_id,
            now=now,
        )
    else:
        from pm_pal.service.materials_service import create_source_version

        result = create_source_version(
            project_store,
            project_id=project_id,
            title=title,
            source_type="notion",
            content=document.content_markdown,
            source_url=source_url,
            is_prd=True,
            parent_source_id=None,
            metadata_extra=metadata_extra,
            new_id=new_id,
            now=now,
            event_kind="source_synced",
        )
    return {
        "source_id": result["id"],
        "version": result["version"],
        "checksum": result["checksum"],
        "source_url": source_url,
        "page_id": normalize_notion_page_id(page_id),
    }


def create_notion_sync_handler(
    *,
    project_store: Any,
    config_store: NotionConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    connector_factory: Callable[[NotionConfig | None], NotionConnector] | None = None,
    upsert_source: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[str, str, dict[str, Any]], dict[str, Any]]:
    """Handle Notion sync tasks.

    Compensation: enqueue manual sync via POST /api/projects/{id}/connectors/sync with
    provider=notion. When resource is empty, all mapped pages are re-fetched. Pass an
    optional since ISO timestamp in the sync payload (or rely on config.last_synced_at)
    to skip pages whose last_edited_time is not newer than since.
    """

    def handler(project_id: str, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = provider
        connector_config = _build_connector_config(config_store, project_id)
        connector = (
            connector_factory(connector_config)
            if connector_factory is not None
            else NotionConnector(config=connector_config)
        )
        config = config_store.get(project_id)
        trigger = str(payload.get("trigger") or "webhook").strip()
        since = str(payload.get("since") or "").strip()
        if trigger == "manual" and not since:
            since = config.last_synced_at

        page_id = normalize_notion_page_id(
            str(payload.get("page_id") or payload.get("resource") or "").strip()
        )
        if page_id:
            mapping = config.page_mappings.get(page_id)
            if mapping is None:
                raise ValueError(f"Notion page mapping not found for page_id: {page_id}")
            result = _sync_single_page(
                project_store=project_store,
                project_id=project_id,
                page_id=page_id,
                mapping=mapping,
                payload=payload,
                connector=connector,
                upsert_source=upsert_source,
                new_id=new_id,
                now=now,
                since=since,
            )
            if result is None:
                return {"skipped": True, "page_id": page_id, "reason": "not_edited_since"}
            config_store.touch_last_synced_at(project_id, synced_at=now(), updated_at=now())
            return result

        if trigger != "manual":
            raise ValueError("Notion sync payload is missing page_id")

        synced_pages: list[dict[str, Any]] = []
        for mapped_page_id, mapping in sorted(config.page_mappings.items()):
            page_result = _sync_single_page(
                project_store=project_store,
                project_id=project_id,
                page_id=mapped_page_id,
                mapping=mapping,
                payload={"title": mapping.title, "source_url": mapping.source_url},
                connector=connector,
                upsert_source=upsert_source,
                new_id=new_id,
                now=now,
                since=since,
            )
            if page_result is not None:
                synced_pages.append(page_result)
        config_store.touch_last_synced_at(project_id, synced_at=now(), updated_at=now())
        return {"synced_pages": synced_pages, "count": len(synced_pages), "since": since}

    return handler


def register_notion_sync_handler(
    *,
    project_store: Any,
    config_store: NotionConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    connector_factory: Callable[[NotionConfig | None], NotionConnector] | None = None,
    upsert_source: Callable[..., dict[str, Any]] | None = None,
) -> None:
    register_sync_handler(
        "notion",
        create_notion_sync_handler(
            project_store=project_store,
            config_store=config_store,
            new_id=new_id,
            now=now,
            connector_factory=connector_factory,
            upsert_source=upsert_source,
        ),
    )
