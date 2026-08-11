"""Persist Notion connector config and page mappings in project_space DB."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NOTION_ID_PATTERN = re.compile(r"([0-9a-fA-F]{32})")


@dataclass(frozen=True, slots=True)
class NotionPageMapping:
    title: str
    source_url: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"title": self.title}
        if self.source_url.strip():
            payload["source_url"] = self.source_url.strip()
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> NotionPageMapping | None:
        if not isinstance(raw, dict):
            return None
        title = str(raw.get("title") or "").strip()
        if not title:
            return None
        return cls(
            title=title,
            source_url=str(raw.get("source_url") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class NotionConnectorSecrets:
    integration_token: str = ""
    signing_secret: str = ""


@dataclass(frozen=True, slots=True)
class NotionConnectorConfig:
    project_id: str = ""
    base_url: str = ""
    secrets: NotionConnectorSecrets = field(default_factory=NotionConnectorSecrets)
    page_mappings: dict[str, NotionPageMapping] = field(default_factory=dict)
    last_synced_at: str = ""
    updated_at: str = ""

    def resolved_integration_token(self) -> str:
        return str(
            self.secrets.integration_token or os.getenv("MARRDP_NOTION_TOKEN", "") or ""
        ).strip()

    def resolved_signing_secret(self) -> str:
        return str(
            self.secrets.signing_secret
            or os.getenv("MARRDP_NOTION_SIGNING_SECRET", "")
            or os.getenv("MARRDP_NOTION_VERIFICATION_TOKEN", "")
            or ""
        ).strip()

    def resolved_base_url(self) -> str:
        default = str(os.getenv("MARRDP_NOTION_API_BASE_URL", "") or "").strip()
        fallback = default or "https://api.notion.com/v1"
        return str(self.base_url or fallback).strip().rstrip("/")


def normalize_notion_page_id(page_id: str) -> str:
    normalized = str(page_id or "").replace("-", "").strip().lower()
    match = _NOTION_ID_PATTERN.search(normalized)
    return match.group(1).lower() if match else normalized


class NotionConfigStore:
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
            cols = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(connector_configs)"
                ).fetchall()
            }
            if "last_synced_at" not in cols:
                connection.execute(
                    "ALTER TABLE connector_configs ADD COLUMN last_synced_at TEXT NOT NULL DEFAULT ''"
                )
            connection.commit()

    def get(self, project_id: str) -> NotionConnectorConfig:
        row = self._row(project_id)
        if row is None:
            return NotionConnectorConfig(project_id=str(project_id or "").strip())
        return self._from_row(row)

    def upsert(
        self,
        project_id: str,
        *,
        base_url: str | None = None,
        page_mappings: dict[str, NotionPageMapping] | None = None,
        secrets: NotionConnectorSecrets | None = None,
        merge_secrets: bool = True,
        last_synced_at: str | None = None,
        updated_at: str,
    ) -> NotionConnectorConfig:
        normalized_project_id = str(project_id or "").strip()
        existing = self.get(normalized_project_id)
        next_base_url = existing.base_url if base_url is None else str(base_url).strip()
        next_mappings = (
            existing.page_mappings if page_mappings is None else page_mappings
        )
        next_secrets = existing.secrets
        if secrets is not None:
            next_secrets = (
                NotionConnectorSecrets(
                    integration_token=secrets.integration_token
                    or existing.secrets.integration_token,
                    signing_secret=secrets.signing_secret
                    or existing.secrets.signing_secret,
                )
                if merge_secrets
                else secrets
            )
        next_last_synced_at = (
            existing.last_synced_at
            if last_synced_at is None
            else str(last_synced_at).strip()
        )
        mappings_json = json.dumps(
            {
                normalize_notion_page_id(page_id): mapping.to_dict()
                for page_id, mapping in sorted(next_mappings.items())
                if normalize_notion_page_id(page_id)
            }
        )
        secrets_json = json.dumps(
            {
                "integration_token": next_secrets.integration_token,
                "signing_secret": next_secrets.signing_secret,
            }
        )
        encrypted = self._encrypt_secrets(secrets_json)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO connector_configs "
                "(project_id, provider, app_id, base_url, doc_mappings_json, secrets_encrypted, "
                "updated_at, last_synced_at) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id, provider) DO UPDATE SET "
                "base_url=excluded.base_url, "
                "doc_mappings_json=excluded.doc_mappings_json, "
                "secrets_encrypted=excluded.secrets_encrypted, "
                "updated_at=excluded.updated_at, "
                "last_synced_at=excluded.last_synced_at",
                (
                    normalized_project_id,
                    "notion",
                    "",
                    next_base_url,
                    mappings_json,
                    encrypted,
                    updated_at,
                    next_last_synced_at,
                ),
            )
            connection.commit()
        return self.get(normalized_project_id)

    def touch_last_synced_at(
        self, project_id: str, *, synced_at: str, updated_at: str
    ) -> None:
        normalized_project_id = str(project_id or "").strip()
        existing = self.get(normalized_project_id)
        self.upsert(
            normalized_project_id,
            base_url=existing.base_url,
            page_mappings=existing.page_mappings,
            secrets=existing.secrets,
            merge_secrets=False,
            last_synced_at=synced_at,
            updated_at=updated_at,
        )

    def find_project_for_page_id(
        self, page_id: str
    ) -> tuple[str, NotionPageMapping] | None:
        normalized_page_id = normalize_notion_page_id(page_id)
        if not normalized_page_id:
            return None
        rows = self._rows(
            "SELECT project_id, doc_mappings_json FROM connector_configs WHERE provider=?",
            ("notion",),
        )
        for row in rows:
            mappings = self._parse_mappings(row.get("doc_mappings_json"))
            mapping = mappings.get(normalized_page_id)
            if mapping is not None:
                return str(row["project_id"]), mapping
        return None

    def public_view(self, config: NotionConnectorConfig) -> dict[str, Any]:
        return {
            "project_id": config.project_id,
            "provider": "notion",
            "base_url": config.resolved_base_url(),
            "has_integration_token": bool(config.resolved_integration_token()),
            "has_signing_secret": bool(config.resolved_signing_secret()),
            "page_mappings": {
                page_id: mapping.to_dict()
                for page_id, mapping in sorted(config.page_mappings.items())
            },
            "last_synced_at": config.last_synced_at,
            "updated_at": config.updated_at,
        }

    def _row(self, project_id: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT * FROM connector_configs WHERE project_id=? AND provider=?",
            (str(project_id or "").strip(), "notion"),
        )
        return rows[0] if rows else None

    def _rows(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def _from_row(self, row: dict[str, Any]) -> NotionConnectorConfig:
        secrets = self._decrypt_secrets(str(row.get("secrets_encrypted") or ""))
        return NotionConnectorConfig(
            project_id=str(row.get("project_id") or "").strip(),
            base_url=str(row.get("base_url") or "").strip(),
            secrets=secrets,
            page_mappings=self._parse_mappings(row.get("doc_mappings_json")),
            last_synced_at=str(row.get("last_synced_at") or "").strip(),
            updated_at=str(row.get("updated_at") or "").strip(),
        )

    def _parse_mappings(self, raw: Any) -> dict[str, NotionPageMapping]:
        try:
            decoded = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(decoded, dict):
            return {}
        mappings: dict[str, NotionPageMapping] = {}
        for page_id, value in decoded.items():
            mapping = NotionPageMapping.from_dict(value)
            normalized_page_id = normalize_notion_page_id(str(page_id))
            if mapping is not None and normalized_page_id:
                mappings[normalized_page_id] = mapping
        return mappings

    def _encrypt_secrets(self, secrets_json: str) -> str:
        if not secrets_json.strip() or secrets_json == "{}":
            return ""
        if self._encrypt_secret is None:
            return secrets_json
        return self._encrypt_secret(secrets_json)

    def _decrypt_secrets(self, token: str) -> NotionConnectorSecrets:
        if not token:
            return NotionConnectorSecrets()
        raw = token
        if self._decrypt_secret is not None:
            try:
                raw = self._decrypt_secret(token)
            except Exception:
                raw = token
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return NotionConnectorSecrets()
        if not isinstance(decoded, dict):
            return NotionConnectorSecrets()
        return NotionConnectorSecrets(
            integration_token=str(decoded.get("integration_token") or "").strip(),
            signing_secret=str(
                decoded.get("signing_secret") or decoded.get("verification_token") or ""
            ).strip(),
        )
