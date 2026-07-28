"""HTTP routes for project-scoped Notion connector configuration."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .config_store import NotionConfigStore, NotionConnectorSecrets, NotionPageMapping


class NotionPageMappingInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_url: str = Field(default="", max_length=500)


class NotionConnectorConfigUpdate(BaseModel):
    integration_token: str | None = None
    signing_secret: str | None = None
    base_url: str | None = None
    page_mappings: dict[str, NotionPageMappingInput] | None = None
    last_synced_at: str | None = None


def register_notion_connector_config_routes(
    router: APIRouter,
    *,
    config_store: NotionConfigStore,
    get_project: Callable[[str], dict[str, Any]],
    now: Callable[[], str],
) -> None:
    @router.get("/projects/{project_id}/connectors/notion")
    async def get_notion_connector_config(project_id: str):
        get_project(project_id)
        config = config_store.get(project_id)
        return config_store.public_view(config)

    @router.put("/projects/{project_id}/connectors/notion")
    async def upsert_notion_connector_config(
        project_id: str, payload: NotionConnectorConfigUpdate
    ):
        get_project(project_id)
        mappings: dict[str, NotionPageMapping] | None = None
        if payload.page_mappings is not None:
            mappings = {
                page_id.strip(): NotionPageMapping(
                    title=item.title.strip(),
                    source_url=item.source_url.strip(),
                )
                for page_id, item in payload.page_mappings.items()
                if page_id.strip() and item.title.strip()
            }
        provided_secrets = any(
            value is not None for value in (payload.integration_token, payload.signing_secret)
        )
        secrets = None
        if provided_secrets:
            existing = config_store.get(project_id)
            secrets = NotionConnectorSecrets(
                integration_token=payload.integration_token
                if payload.integration_token is not None
                else existing.secrets.integration_token,
                signing_secret=payload.signing_secret
                if payload.signing_secret is not None
                else existing.secrets.signing_secret,
            )
        config = config_store.upsert(
            project_id,
            base_url=payload.base_url,
            page_mappings=mappings,
            secrets=secrets,
            last_synced_at=payload.last_synced_at,
            updated_at=now(),
        )
        return config_store.public_view(config)
