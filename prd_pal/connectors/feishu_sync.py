"""Sync handler that ingests Feishu documents into project materials."""
from __future__ import annotations

from typing import Any, Callable

from prd_pal.connectors.auth import ConnectorAuthConfig, ConnectorAuthType
from prd_pal.connectors.feishu import FeishuConfig, FeishuConnector
from prd_pal.connectors.sync import register_sync_handler
from prd_pal.integrations.feishu.config_store import FeishuConfigStore


def _build_connector_config(config_store: FeishuConfigStore, project_id: str) -> FeishuConfig | None:
    config = config_store.get(project_id)
    app_id = config.resolved_app_id()
    app_secret = config.resolved_app_secret()
    if not app_id or not app_secret:
        return None
    auth = ConnectorAuthConfig(
        auth_type=ConnectorAuthType.oauth_client_credentials,
        client_id=app_id,
        client_secret=app_secret,
    )
    return FeishuConfig(auth=auth, base_url=config.resolved_base_url())


def create_feishu_sync_handler(
    *,
    project_store: Any,
    config_store: FeishuConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    connector_factory: Callable[[FeishuConfig | None], FeishuConnector] | None = None,
    upsert_source: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[str, str, dict[str, Any]], dict[str, Any]]:
    def handler(project_id: str, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        _ = provider
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            doc_token = str(payload.get("doc_token") or "").strip()
            document_kind = str(payload.get("document_kind") or "docx").strip() or "docx"
            source_url = f"feishu://{document_kind}/{doc_token}"
        connector_config = _build_connector_config(config_store, project_id)
        connector = (
            connector_factory(connector_config)
            if connector_factory is not None
            else FeishuConnector(config=connector_config)
        )
        document = connector.get_content(source_url)
        title = str(payload.get("title") or document.title or "feishu-document").strip()
        metadata_extra = {
            "origin": "feishu_sync",
            "mime_type": "text/markdown",
            "filename": f"{title}.md",
            "connector": document.metadata.extra,
        }
        if upsert_source is not None:
            result = upsert_source(
                project_store,
                project_id=project_id,
                title=title,
                source_type="feishu",
                content=document.content_markdown,
                source_url=source_url,
                metadata_extra=metadata_extra,
                new_id=new_id,
                now=now,
            )
        else:
            from prd_pal.service.materials_service import create_source_version

            result = create_source_version(
                project_store,
                project_id=project_id,
                title=title,
                source_type="feishu",
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
        }

    return handler


def register_feishu_sync_handler(
    *,
    project_store: Any,
    config_store: FeishuConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    connector_factory: Callable[[FeishuConfig | None], FeishuConnector] | None = None,
    upsert_source: Callable[..., dict[str, Any]] | None = None,
) -> None:
    register_sync_handler(
        "feishu",
        create_feishu_sync_handler(
            project_store=project_store,
            config_store=config_store,
            new_id=new_id,
            now=now,
            connector_factory=connector_factory,
            upsert_source=upsert_source,
        ),
    )
