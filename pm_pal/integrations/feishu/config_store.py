"""Persist Feishu connector app config and doc mappings in project_space DB."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FeishuDocMapping:
    title: str
    document_kind: str = "docx"
    source_url: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"title": self.title, "document_kind": self.document_kind}
        if self.source_url.strip():
            payload["source_url"] = self.source_url.strip()
        return payload

    @classmethod
    def from_dict(cls, raw: Any) -> FeishuDocMapping | None:
        if not isinstance(raw, dict):
            return None
        title = str(raw.get("title") or "").strip()
        if not title:
            return None
        return cls(
            title=title,
            document_kind=str(raw.get("document_kind") or "docx").strip() or "docx",
            source_url=str(raw.get("source_url") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class FeishuConnectorSecrets:
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    webhook_secret: str = ""


@dataclass(frozen=True, slots=True)
class FeishuConnectorConfig:
    project_id: str = ""
    app_id: str = ""
    base_url: str = ""
    secrets: FeishuConnectorSecrets = field(default_factory=FeishuConnectorSecrets)
    doc_mappings: dict[str, FeishuDocMapping] = field(default_factory=dict)
    updated_at: str = ""

    def resolved_app_id(self) -> str:
        return str(self.app_id or os.getenv("MARRDP_FEISHU_APP_ID", "") or "").strip()

    def resolved_app_secret(self) -> str:
        return str(
            self.secrets.app_secret or os.getenv("MARRDP_FEISHU_APP_SECRET", "") or ""
        ).strip()

    def resolved_encrypt_key(self) -> str:
        return str(
            self.secrets.encrypt_key or os.getenv("MARRDP_FEISHU_ENCRYPT_KEY", "") or ""
        ).strip()

    def resolved_verification_token(self) -> str:
        return str(
            self.secrets.verification_token
            or os.getenv("MARRDP_FEISHU_VERIFICATION_TOKEN", "")
            or ""
        ).strip()

    def resolved_webhook_secret(self) -> str:
        return str(
            self.secrets.webhook_secret
            or os.getenv("MARRDP_FEISHU_WEBHOOK_SECRET", "")
            or ""
        ).strip()

    def resolved_base_url(self) -> str:
        default = str(os.getenv("MARRDP_FEISHU_OPEN_BASE_URL", "") or "").strip()
        fallback = default or "https://open.feishu.cn"
        return str(self.base_url or fallback).strip().rstrip("/")


class FeishuConfigStore:
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

    def get(self, project_id: str) -> FeishuConnectorConfig:
        row = self._row(project_id)
        if row is None:
            return FeishuConnectorConfig(project_id=str(project_id or "").strip())
        return self._from_row(row)

    def upsert(
        self,
        project_id: str,
        *,
        app_id: str | None = None,
        base_url: str | None = None,
        doc_mappings: dict[str, FeishuDocMapping] | None = None,
        secrets: FeishuConnectorSecrets | None = None,
        merge_secrets: bool = True,
        updated_at: str,
    ) -> FeishuConnectorConfig:
        normalized_project_id = str(project_id or "").strip()
        existing = self.get(normalized_project_id)
        next_app_id = existing.app_id if app_id is None else str(app_id).strip()
        next_base_url = existing.base_url if base_url is None else str(base_url).strip()
        next_mappings = existing.doc_mappings if doc_mappings is None else doc_mappings
        next_secrets = existing.secrets
        if secrets is not None:
            next_secrets = (
                FeishuConnectorSecrets(
                    app_secret=secrets.app_secret or existing.secrets.app_secret,
                    encrypt_key=secrets.encrypt_key or existing.secrets.encrypt_key,
                    verification_token=secrets.verification_token
                    or existing.secrets.verification_token,
                    webhook_secret=secrets.webhook_secret
                    or existing.secrets.webhook_secret,
                )
                if merge_secrets
                else secrets
            )
        mappings_json = json.dumps(
            {
                token: mapping.to_dict()
                for token, mapping in sorted(next_mappings.items())
            }
        )
        secrets_json = json.dumps(
            {
                "app_secret": next_secrets.app_secret,
                "encrypt_key": next_secrets.encrypt_key,
                "verification_token": next_secrets.verification_token,
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
                    "feishu",
                    next_app_id,
                    next_base_url,
                    mappings_json,
                    encrypted,
                    updated_at,
                ),
            )
            connection.commit()
        return self.get(normalized_project_id)

    def find_project_for_doc_token(
        self, doc_token: str
    ) -> tuple[str, FeishuDocMapping] | None:
        normalized_token = str(doc_token or "").strip()
        if not normalized_token:
            return None
        rows = self._rows(
            "SELECT project_id, doc_mappings_json FROM connector_configs WHERE provider=?",
            ("feishu",),
        )
        for row in rows:
            mappings = self._parse_mappings(row.get("doc_mappings_json"))
            mapping = mappings.get(normalized_token)
            if mapping is not None:
                return str(row["project_id"]), mapping
        return None

    def public_view(self, config: FeishuConnectorConfig) -> dict[str, Any]:
        return {
            "project_id": config.project_id,
            "provider": "feishu",
            "app_id": config.resolved_app_id(),
            "base_url": config.resolved_base_url(),
            "has_app_secret": bool(config.resolved_app_secret()),
            "has_encrypt_key": bool(config.resolved_encrypt_key()),
            "has_verification_token": bool(config.resolved_verification_token()),
            "has_webhook_secret": bool(config.resolved_webhook_secret()),
            "doc_mappings": {
                token: mapping.to_dict()
                for token, mapping in sorted(config.doc_mappings.items())
            },
            "updated_at": config.updated_at,
        }

    def _row(self, project_id: str) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT * FROM connector_configs WHERE project_id=? AND provider=?",
            (str(project_id or "").strip(), "feishu"),
        )
        return rows[0] if rows else None

    def _rows(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def _from_row(self, row: dict[str, Any]) -> FeishuConnectorConfig:
        secrets = self._decrypt_secrets(str(row.get("secrets_encrypted") or ""))
        return FeishuConnectorConfig(
            project_id=str(row.get("project_id") or "").strip(),
            app_id=str(row.get("app_id") or "").strip(),
            base_url=str(row.get("base_url") or "").strip(),
            secrets=secrets,
            doc_mappings=self._parse_mappings(row.get("doc_mappings_json")),
            updated_at=str(row.get("updated_at") or "").strip(),
        )

    def _parse_mappings(self, raw: Any) -> dict[str, FeishuDocMapping]:
        try:
            decoded = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(decoded, dict):
            return {}
        mappings: dict[str, FeishuDocMapping] = {}
        for token, value in decoded.items():
            mapping = FeishuDocMapping.from_dict(value)
            if mapping is not None:
                mappings[str(token).strip()] = mapping
        return mappings

    def _encrypt_secrets(self, secrets_json: str) -> str:
        if not secrets_json.strip() or secrets_json == "{}":
            return ""
        if self._encrypt_secret is None:
            return secrets_json
        return self._encrypt_secret(secrets_json)

    def _decrypt_secrets(self, token: str) -> FeishuConnectorSecrets:
        if not token:
            return FeishuConnectorSecrets()
        raw = token
        if self._decrypt_secret is not None:
            try:
                raw = self._decrypt_secret(token)
            except Exception:
                raw = token
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return FeishuConnectorSecrets()
        if not isinstance(decoded, dict):
            return FeishuConnectorSecrets()
        return FeishuConnectorSecrets(
            app_secret=str(decoded.get("app_secret") or "").strip(),
            encrypt_key=str(decoded.get("encrypt_key") or "").strip(),
            verification_token=str(decoded.get("verification_token") or "").strip(),
            webhook_secret=str(decoded.get("webhook_secret") or "").strip(),
        )
