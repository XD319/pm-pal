"""Persist GitHub connector auth and repo mappings in project_space DB."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class GitHubAuthMode(str, Enum):
    app = "app"
    pat = "pat"


@dataclass(frozen=True, slots=True)
class GitHubRepoMapping:
    title: str
    owner: str = ""
    repo: str = ""
    paths: tuple[str, ...] = ()
    include_readme: bool = True
    sync_issues: bool = True
    sync_pull_requests: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": self.title,
            "owner": self.owner,
            "repo": self.repo,
            "include_readme": self.include_readme,
            "sync_issues": self.sync_issues,
            "sync_pull_requests": self.sync_pull_requests,
        }
        if self.paths:
            payload["paths"] = list(self.paths)
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> "GitHubRepoMapping | None":
        if not isinstance(raw, dict):
            return None
        title = str(raw.get("title") or "").strip()
        if not title:
            return None
        paths_raw = raw.get("paths") or []
        paths: tuple[str, ...] = ()
        if isinstance(paths_raw, list):
            paths = tuple(str(item).strip() for item in paths_raw if str(item).strip())
        return cls(
            title=title,
            owner=str(raw.get("owner") or "").strip(),
            repo=str(raw.get("repo") or "").strip(),
            paths=paths,
            include_readme=bool(raw.get("include_readme", True)),
            sync_issues=bool(raw.get("sync_issues", True)),
            sync_pull_requests=bool(raw.get("sync_pull_requests", True)),
        )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}".strip("/")


@dataclass(frozen=True, slots=True)
class GitHubConnectorSecrets:
    auth_mode: str = GitHubAuthMode.pat.value
    private_key: str = ""
    personal_access_token: str = ""
    installation_id: str = ""
    webhook_secret: str = ""


@dataclass(frozen=True, slots=True)
class GitHubConnectorConfig:
    project_id: str = ""
    app_id: str = ""
    base_url: str = ""
    secrets: GitHubConnectorSecrets = field(default_factory=GitHubConnectorSecrets)
    repo_mappings: dict[str, GitHubRepoMapping] = field(default_factory=dict)
    updated_at: str = ""

    def resolved_auth_mode(self) -> GitHubAuthMode:
        raw = str(
            self.secrets.auth_mode or os.getenv("MARRDP_GITHUB_AUTH_MODE", "") or ""
        ).strip().lower()
        if raw == GitHubAuthMode.app.value:
            return GitHubAuthMode.app
        return GitHubAuthMode.pat

    def resolved_app_id(self) -> str:
        return str(self.app_id or os.getenv("MARRDP_GITHUB_APP_ID", "") or "").strip()

    def resolved_private_key(self) -> str:
        return str(
            self.secrets.private_key
            or os.getenv("MARRDP_GITHUB_PRIVATE_KEY", "")
            or ""
        ).strip()

    def resolved_installation_id(self) -> str:
        return str(
            self.secrets.installation_id
            or os.getenv("MARRDP_GITHUB_INSTALLATION_ID", "")
            or ""
        ).strip()

    def resolved_personal_access_token(self) -> str:
        return str(
            self.secrets.personal_access_token
            or os.getenv("GITHUB_TOKEN", "")
            or os.getenv("MARRDP_GITHUB_TOKEN", "")
            or ""
        ).strip()

    def resolved_webhook_secret(self) -> str:
        return str(
            self.secrets.webhook_secret
            or os.getenv("MARRDP_GITHUB_WEBHOOK_SECRET", "")
            or ""
        ).strip()

    def resolved_base_url(self) -> str:
        default = str(os.getenv("MARRDP_GITHUB_API_BASE_URL", "") or "").strip()
        fallback = default or "https://api.github.com"
        return str(self.base_url or fallback).strip().rstrip("/")


class GitHubConfigStore:
    def __init__(
        self,
        path: Path,
        *,
        encrypt_secret: Callable[[str], str] | None = None,
        decrypt_secret: Callable[[str], str] | None = None,
    ) -> None:
        self.path = path
        self._encrypt_secret = encrypt_secret
        self._decrypt_secret = decrypt_secret

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.executescript(
                """
CREATE TABLE IF NOT EXISTS connector_configs (
    project_id TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL,
    app_id TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    doc_mappings_json TEXT NOT NULL DEFAULT '{}',
    secrets_encrypted TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, provider)
);
"""
            )
            connection.commit()

    def get(self, project_id: str) -> GitHubConnectorConfig:
        row = self._row(project_id)
        if row is None:
            return GitHubConnectorConfig(project_id=str(project_id or "").strip())
        return self._from_row(row)

    def upsert(
        self,
        project_id: str,
        *,
        app_id: str | None = None,
        base_url: str | None = None,
        repo_mappings: dict[str, GitHubRepoMapping] | None = None,
        secrets: GitHubConnectorSecrets | None = None,
        merge_secrets: bool = True,
        updated_at: str,
    ) -> GitHubConnectorConfig:
        normalized_project_id = str(project_id or "").strip()
        existing = self.get(normalized_project_id)
        next_app_id = existing.app_id if app_id is None else str(app_id).strip()
        next_base_url = existing.base_url if base_url is None else str(base_url).strip()
        next_mappings = existing.repo_mappings if repo_mappings is None else repo_mappings
        next_secrets = existing.secrets
        if secrets is not None:
            next_secrets = (
                GitHubConnectorSecrets(
                    auth_mode=secrets.auth_mode or existing.secrets.auth_mode,
                    private_key=secrets.private_key or existing.secrets.private_key,
                    personal_access_token=secrets.personal_access_token
                    or existing.secrets.personal_access_token,
                    installation_id=secrets.installation_id
                    or existing.secrets.installation_id,
                    webhook_secret=secrets.webhook_secret
                    or existing.secrets.webhook_secret,
                )
                if merge_secrets
                else secrets
            )
        mappings_json = json.dumps(
            {
                key: mapping.to_dict()
                for key, mapping in sorted(next_mappings.items())
            }
        )
        secrets_json = json.dumps(
            {
                "auth_mode": next_secrets.auth_mode,
                "private_key": next_secrets.private_key,
                "personal_access_token": next_secrets.personal_access_token,
                "installation_id": next_secrets.installation_id,
                "webhook_secret": next_secrets.webhook_secret,
            }
        )
        encrypted = self._encrypt_secrets(secrets_json)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO connector_configs "
                "(project_id, provider, app_id, base_url, doc_mappings_json, secrets_encrypted, updated_at) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id, provider) DO UPDATE SET "
                "app_id=excluded.app_id, "
                "base_url=excluded.base_url, "
                "doc_mappings_json=excluded.doc_mappings_json, "
                "secrets_encrypted=excluded.secrets_encrypted, "
                "updated_at=excluded.updated_at",
                (
                    normalized_project_id,
                    "github",
                    next_app_id,
                    next_base_url,
                    mappings_json,
                    encrypted,
                    updated_at,
                ),
            )
            connection.commit()
        return self.get(normalized_project_id)

    def find_project_for_repo(self, full_name: str) -> tuple[str, GitHubRepoMapping] | None:
        normalized = str(full_name or "").strip().lower()
        if not normalized or "/" not in normalized:
            return None
        rows = self._rows(
            "SELECT project_id, doc_mappings_json FROM connector_configs WHERE provider=?",
            ("github",),
        )
        for row in rows:
            mappings = self._parse_mappings(row.get("doc_mappings_json"))
            for key, mapping in mappings.items():
                repo_key = str(key).strip().lower()
                mapping_name = mapping.full_name.lower()
                if repo_key == normalized or mapping_name == normalized:
                    return str(row["project_id"]), mapping
        return None

    def public_view(self, config: GitHubConnectorConfig) -> dict[str, Any]:
        auth_mode = config.resolved_auth_mode().value
        return {
            "project_id": config.project_id,
            "provider": "github",
            "auth_mode": auth_mode,
            "app_id": config.resolved_app_id(),
            "base_url": config.resolved_base_url(),
            "has_private_key": bool(config.resolved_private_key()),
            "has_personal_access_token": bool(config.resolved_personal_access_token()),
            "has_installation_id": bool(config.resolved_installation_id()),
            "has_webhook_secret": bool(config.resolved_webhook_secret()),
            "repo_mappings": {
                key: mapping.to_dict()
                for key, mapping in sorted(config.repo_mappings.items())
            },
            "updated_at": config.updated_at,
        }

    def _row(self, project_id: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT * FROM connector_configs WHERE project_id=? AND provider=?",
            (str(project_id or "").strip(), "github"),
        )
        return rows[0] if rows else None

    def _rows(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def _from_row(self, row: dict[str, Any]) -> GitHubConnectorConfig:
        secrets = self._decrypt_secrets(str(row.get("secrets_encrypted") or ""))
        return GitHubConnectorConfig(
            project_id=str(row.get("project_id") or "").strip(),
            app_id=str(row.get("app_id") or "").strip(),
            base_url=str(row.get("base_url") or "").strip(),
            secrets=secrets,
            repo_mappings=self._parse_mappings(row.get("doc_mappings_json")),
            updated_at=str(row.get("updated_at") or "").strip(),
        )

    def _parse_mappings(self, raw: Any) -> dict[str, GitHubRepoMapping]:
        try:
            decoded = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(decoded, dict):
            return {}
        mappings: dict[str, GitHubRepoMapping] = {}
        for key, value in decoded.items():
            mapping = GitHubRepoMapping.from_dict(value)
            if mapping is not None:
                mappings[str(key).strip()] = mapping
        return mappings

    def _encrypt_secrets(self, secrets_json: str) -> str:
        if not secrets_json.strip() or secrets_json == "{}":
            return ""
        if self._encrypt_secret is None:
            return secrets_json
        return self._encrypt_secret(secrets_json)

    def _decrypt_secrets(self, token: str) -> GitHubConnectorSecrets:
        if not token:
            return GitHubConnectorSecrets()
        raw = token
        if self._decrypt_secret is not None:
            try:
                raw = self._decrypt_secret(token)
            except Exception:
                raw = token
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return GitHubConnectorSecrets()
        if not isinstance(decoded, dict):
            return GitHubConnectorSecrets()
        auth_mode = str(decoded.get("auth_mode") or GitHubAuthMode.pat.value).strip()
        return GitHubConnectorSecrets(
            auth_mode=auth_mode,
            private_key=str(decoded.get("private_key") or "").strip(),
            personal_access_token=str(decoded.get("personal_access_token") or "").strip(),
            installation_id=str(decoded.get("installation_id") or "").strip(),
            webhook_secret=str(decoded.get("webhook_secret") or "").strip(),
        )
