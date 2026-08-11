"""Sync handler that ingests GitHub resources into project materials."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pm_pal.connectors.github import (
    GitHubConfig,
    GitHubConnector,
    build_github_connector_config,
)
from pm_pal.connectors.sync import register_sync_handler
from pm_pal.integrations.github.config_store import GitHubAuthMode, GitHubConfigStore


def _build_connector_config(
    config_store: GitHubConfigStore, project_id: str
) -> GitHubConfig | None:
    config = config_store.get(project_id)
    auth_mode = config.resolved_auth_mode()
    if auth_mode == GitHubAuthMode.pat:
        token = config.resolved_personal_access_token()
        if not token:
            return None
        return build_github_connector_config(
            auth_mode=auth_mode,
            app_id="",
            private_key="",
            installation_id="",
            personal_access_token=token,
            base_url=config.resolved_base_url(),
        )

    app_id = config.resolved_app_id()
    private_key = config.resolved_private_key()
    installation_id = config.resolved_installation_id()
    if not app_id or not private_key or not installation_id:
        return None
    return build_github_connector_config(
        auth_mode=auth_mode,
        app_id=app_id,
        private_key=private_key,
        installation_id=installation_id,
        personal_access_token="",
        base_url=config.resolved_base_url(),
    )


def create_github_sync_handler(
    *,
    project_store: Any,
    config_store: GitHubConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    connector_factory: Callable[[GitHubConfig | None], GitHubConnector] | None = None,
    upsert_source: Callable[..., dict[str, Any]] | None = None,
) -> Callable[[str, str, dict[str, Any]], dict[str, Any]]:
    def handler(
        project_id: str, provider: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        _ = provider
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            owner = str(payload.get("owner") or "").strip()
            repo = str(payload.get("repo") or "").strip()
            resource_kind = (
                str(payload.get("resource_kind") or "readme").strip().lower()
            )
            path = str(payload.get("path") or "").strip()
            number = payload.get("number")
            if resource_kind == "file" and path:
                source_url = f"github://{owner}/{repo}/file/{path.lstrip('/')}"
            elif resource_kind == "issue" and number is not None:
                source_url = f"github://{owner}/{repo}/issue/{int(number)}"
            elif resource_kind == "pull" and number is not None:
                source_url = f"github://{owner}/{repo}/pull/{int(number)}"
            else:
                source_url = f"github://{owner}/{repo}/readme"

        connector_config = _build_connector_config(config_store, project_id)
        connector = (
            connector_factory(connector_config)
            if connector_factory is not None
            else GitHubConnector(config=connector_config)
        )
        document = connector.get_content(source_url)
        title = str(payload.get("title") or document.title or "github-document").strip()
        metadata_extra = {
            "origin": "github_sync",
            "mime_type": "text/markdown",
            "filename": f"{title}.md",
            "connector": document.metadata.extra,
        }
        if upsert_source is not None:
            result = upsert_source(
                project_store,
                project_id=project_id,
                title=title,
                source_type="github",
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
                source_type="github",
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


def register_github_sync_handler(
    *,
    project_store: Any,
    config_store: GitHubConfigStore,
    new_id: Callable[[str], str],
    now: Callable[[], str],
    connector_factory: Callable[[GitHubConfig | None], GitHubConnector] | None = None,
    upsert_source: Callable[..., dict[str, Any]] | None = None,
) -> None:
    register_sync_handler(
        "github",
        create_github_sync_handler(
            project_store=project_store,
            config_store=config_store,
            new_id=new_id,
            now=now,
            connector_factory=connector_factory,
            upsert_source=upsert_source,
        ),
    )
