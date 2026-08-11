"""HTTP routes for project-scoped GitHub connector configuration."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .config_store import GitHubAuthMode, GitHubConfigStore, GitHubConnectorSecrets, GitHubRepoMapping


class GitHubRepoMappingInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    owner: str = Field(default="", max_length=120)
    repo: str = Field(default="", max_length=120)
    paths: list[str] = Field(default_factory=list)
    include_readme: bool = True
    sync_issues: bool = True
    sync_pull_requests: bool = True


class GitHubConnectorConfigUpdate(BaseModel):
    auth_mode: str | None = None
    app_id: str | None = None
    private_key: str | None = None
    personal_access_token: str | None = None
    installation_id: str | None = None
    webhook_secret: str | None = None
    base_url: str | None = None
    repo_mappings: dict[str, GitHubRepoMappingInput] | None = None


def register_github_connector_config_routes(
    router: APIRouter,
    *,
    config_store: GitHubConfigStore,
    get_project: Callable[[str], dict[str, Any]],
    now: Callable[[], str],
) -> None:
    @router.get("/projects/{project_id}/connectors/github")
    async def get_github_connector_config(project_id: str):
        get_project(project_id)
        config = config_store.get(project_id)
        return config_store.public_view(config)

    @router.put("/projects/{project_id}/connectors/github")
    async def upsert_github_connector_config(
        project_id: str, payload: GitHubConnectorConfigUpdate
    ):
        get_project(project_id)
        mappings: dict[str, GitHubRepoMapping] | None = None
        if payload.repo_mappings is not None:
            mappings = {}
            for key, item in payload.repo_mappings.items():
                repo_key = key.strip()
                if not repo_key or not item.title.strip():
                    continue
                owner = item.owner.strip()
                repo = item.repo.strip()
                if not owner or not repo:
                    if "/" in repo_key:
                        owner, repo = repo_key.split("/", 1)
                mappings[repo_key] = GitHubRepoMapping(
                    title=item.title.strip(),
                    owner=owner,
                    repo=repo,
                    paths=tuple(path.strip() for path in item.paths if path.strip()),
                    include_readme=item.include_readme,
                    sync_issues=item.sync_issues,
                    sync_pull_requests=item.sync_pull_requests,
                )
        provided_secrets = any(
            value is not None
            for value in (
                payload.auth_mode,
                payload.private_key,
                payload.personal_access_token,
                payload.installation_id,
                payload.webhook_secret,
            )
        )
        secrets = None
        if provided_secrets:
            existing = config_store.get(project_id)
            auth_mode = (
                payload.auth_mode
                if payload.auth_mode is not None
                else existing.secrets.auth_mode
            )
            if auth_mode not in {GitHubAuthMode.app.value, GitHubAuthMode.pat.value}:
                auth_mode = GitHubAuthMode.pat.value
            secrets = GitHubConnectorSecrets(
                auth_mode=auth_mode,
                private_key=payload.private_key
                if payload.private_key is not None
                else existing.secrets.private_key,
                personal_access_token=payload.personal_access_token
                if payload.personal_access_token is not None
                else existing.secrets.personal_access_token,
                installation_id=payload.installation_id
                if payload.installation_id is not None
                else existing.secrets.installation_id,
                webhook_secret=payload.webhook_secret
                if payload.webhook_secret is not None
                else existing.secrets.webhook_secret,
            )
        config = config_store.upsert(
            project_id,
            app_id=payload.app_id,
            base_url=payload.base_url,
            repo_mappings=mappings,
            secrets=secrets,
            updated_at=now(),
        )
        return config_store.public_view(config)
