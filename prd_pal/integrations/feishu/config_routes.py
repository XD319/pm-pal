"""HTTP routes for project-scoped Feishu connector configuration."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .config_store import FeishuConfigStore, FeishuConnectorSecrets, FeishuDocMapping


class FeishuDocMappingInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    document_kind: str = Field(default="docx", max_length=32)
    source_url: str = Field(default="", max_length=500)


class FeishuConnectorConfigUpdate(BaseModel):
    app_id: str | None = None
    app_secret: str | None = None
    encrypt_key: str | None = None
    verification_token: str | None = None
    webhook_secret: str | None = None
    base_url: str | None = None
    doc_mappings: dict[str, FeishuDocMappingInput] | None = None


def register_feishu_connector_config_routes(
    router: APIRouter,
    *,
    config_store: FeishuConfigStore,
    get_project: Callable[[str], dict[str, Any]],
    now: Callable[[], str],
) -> None:
    @router.get("/projects/{project_id}/connectors/feishu")
    async def get_feishu_connector_config(project_id: str):
        get_project(project_id)
        config = config_store.get(project_id)
        return config_store.public_view(config)

    @router.put("/projects/{project_id}/connectors/feishu")
    async def upsert_feishu_connector_config(
        project_id: str, payload: FeishuConnectorConfigUpdate
    ):
        get_project(project_id)
        mappings: dict[str, FeishuDocMapping] | None = None
        if payload.doc_mappings is not None:
            mappings = {
                token.strip(): FeishuDocMapping(
                    title=item.title.strip(),
                    document_kind=item.document_kind.strip() or "docx",
                    source_url=item.source_url.strip(),
                )
                for token, item in payload.doc_mappings.items()
                if token.strip() and item.title.strip()
            }
        provided_secrets = any(
            value is not None
            for value in (
                payload.app_secret,
                payload.encrypt_key,
                payload.verification_token,
                payload.webhook_secret,
            )
        )
        secrets = None
        if provided_secrets:
            existing = config_store.get(project_id)
            secrets = FeishuConnectorSecrets(
                app_secret=payload.app_secret
                if payload.app_secret is not None
                else existing.secrets.app_secret,
                encrypt_key=payload.encrypt_key
                if payload.encrypt_key is not None
                else existing.secrets.encrypt_key,
                verification_token=payload.verification_token
                if payload.verification_token is not None
                else existing.secrets.verification_token,
                webhook_secret=payload.webhook_secret
                if payload.webhook_secret is not None
                else existing.secrets.webhook_secret,
            )
        config = config_store.upsert(
            project_id,
            app_id=payload.app_id,
            base_url=payload.base_url,
            doc_mappings=mappings,
            secrets=secrets,
            updated_at=now(),
        )
        return config_store.public_view(config)
