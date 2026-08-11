"""Local-first project and provider APIs for the open-source workspace."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from pm_pal.connectors import ConnectorRegistry, get_connector_error_payload
from pm_pal.connectors.errors import ConnectorErrorCode
from pm_pal.connectors.feishu_sync import register_feishu_sync_handler
from pm_pal.connectors.github_sync import register_github_sync_handler
from pm_pal.connectors.notion_sync import register_notion_sync_handler
from pm_pal.connectors.sync import ConnectorSyncStore, register_connector_sync_routes
from pm_pal.connectors.sync.service import list_connector_summaries
from pm_pal.integrations.feishu.config_routes import (
    register_feishu_connector_config_routes,
)
from pm_pal.integrations.feishu.config_store import FeishuConfigStore
from pm_pal.integrations.github.config_routes import (
    register_github_connector_config_routes,
)
from pm_pal.integrations.github.config_store import GitHubConfigStore
from pm_pal.integrations.notion.config_routes import (
    register_notion_connector_config_routes,
)
from pm_pal.integrations.notion.config_store import NotionConfigStore
from pm_pal.project_domain.repository import ProjectDomainRepository
from pm_pal.project_domain.router import register_project_domain_routes
from pm_pal.runtime.llm_provider.generic.base import (
    SUPPORTED_PROVIDERS,
    provider_tier,
)
from pm_pal.server.job_state import (
    ClarificationAnswerRequest,
    RevisionConfirmRequest,
    RevisionInputRequest,
    RevisionStageRequest,
)
from pm_pal.server.provider_probe import package_pip_name, probe_provider_connection
from pm_pal.service.materials_service import (
    MAX_UPLOAD_BYTES,
    create_source_version,
    diff_sources,
    get_source_or_404,
    parse_upload_bytes,
    public_source,
    record_event,
    rollback_source,
)
from pm_pal.utils.redaction import is_sensitive_key, redact_mapping

LOCAL_PROVIDER = "ollama"
PROVIDER_PACKAGES = {
    "openai": "langchain_openai",
    "deepseek": "langchain_openai",
    "azure_openai": "langchain_openai",
    "ollama": "langchain_ollama",
    "anthropic": "langchain_anthropic",
    "groq": "langchain_groq",
    "google_genai": "langchain_google_genai",
    "google_vertexai": "langchain_google_vertexai",
    "bedrock": "langchain_aws",
    "cohere": "langchain_cohere",
    "mistralai": "langchain_mistralai",
    "fireworks": "langchain_fireworks",
    "huggingface": "langchain_huggingface",
    "gigachat": "langchain_gigachat",
    "netmind": "langchain_netmind",
}


def _catalog_field(
    name: str,
    label: str,
    *,
    field_type: str = "text",
    storage: str = "extra",
    required: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "type": field_type,
        "storage": storage,
        "required": required,
    }


PROVIDER_EXTRA_FIELDS: dict[str, list[dict[str, Any]]] = {
    "azure_openai": [
        _catalog_field("region", "Region"),
        _catalog_field("deployment", "Deployment"),
        _catalog_field("api_version", "API Version"),
    ],
    "google_vertexai": [
        _catalog_field("project", "GCP Project"),
        _catalog_field("location", "Location"),
    ],
    "bedrock": [
        _catalog_field("region", "AWS Region"),
    ],
}


def _provider_catalog_entry(provider: str) -> dict[str, Any]:
    package = PROVIDER_PACKAGES.get(provider, "langchain_community")
    fields: list[dict[str, Any]] = []
    if provider not in {LOCAL_PROVIDER, "bedrock", "google_vertexai"}:
        fields.append(
            _catalog_field(
                "api_key",
                "API Key",
                field_type="secret",
                storage="api_key",
                required=True,
            )
        )
    fields.append(_catalog_field("base_url", "Base URL", storage="base_url"))
    fields.extend(PROVIDER_EXTRA_FIELDS.get(provider, []))
    return {
        "id": provider,
        "label": provider.replace("_", " ").title(),
        "tier": provider_tier(provider),
        "requires_api_key": provider
        not in {LOCAL_PROVIDER, "bedrock", "google_vertexai"},
        "requires_package": package,
        "install_hint": f"pip install -U {package_pip_name(package)}",
        "available": importlib.util.find_spec(package) is not None,
        "fields": fields,
    }


def _public_extra(extra: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in extra.items() if not is_sensitive_key(str(key))
    }


def _connection_llm_kwargs(conn: dict[str, Any], secret: str) -> dict[str, Any]:
    llm_kwargs = {
        key: value
        for key, value in json.loads(conn.get("extra_json") or "{}").items()
        if value not in (None, "")
    }
    if secret:
        llm_kwargs["api_key"] = secret
    if conn.get("base_url"):
        llm_kwargs["base_url"] = conn["base_url"]
    return llm_kwargs


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(kind: str) -> str:
    return f"{kind}_{uuid.uuid4().hex[:12]}"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    model_preset_id: str | None = None
    product_id: str = ""
    owner_actor: str = ""
    delivery_target: dict[str, Any] = Field(default_factory=dict)
    source_bindings: list[dict[str, Any]] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    model_preset_id: str | None = None
    product_id: str | None = None
    owner_actor: str | None = None
    delivery_target: dict[str, Any] | None = None
    source_bindings: list[dict[str, Any]] | None = None


class SourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_type: str = "prd_text"
    content: str = ""
    source_url: str = ""
    is_prd: bool = True
    parent_source_id: str | None = None

    @model_validator(mode="after")
    def input_present(self):
        if not self.content.strip() and not self.source_url.strip():
            raise ValueError("Provide source content or a source URL.")
        return self


class SourceFromUrl(BaseModel):
    source_url: str = Field(min_length=1, max_length=2000)
    title: str = ""
    is_prd: bool = True


class SourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    source_url: str | None = None
    is_prd: bool | None = None


class ProjectReview(BaseModel):
    source_id: str | None = None
    mode: str = "quick"
    model_preset_id: str | None = None


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str
    api_key: str = ""
    base_url: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    extra: dict[str, Any] | None = None


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    connection_id: str
    model: str | None = None
    # Legacy aliases still accepted and collapsed onto model :-)
    fast_model: str | None = None
    smart_model: str | None = None
    strategic_model: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=2)
    reasoning_effort: str = Field(default="medium", pattern="^(low|medium|high)$")
    is_default: bool = False

    @model_validator(mode="after")
    def _require_model(self) -> PresetCreate:
        self.model = self.resolved_model()
        return self

    def resolved_model(self) -> str:
        for candidate in (
            self.model,
            self.smart_model,
            self.fast_model,
            self.strategic_model,
        ):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        raise ValueError("model is required")


class Store:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',model_preset_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,product_id TEXT NOT NULL DEFAULT '',owner_actor TEXT NOT NULL DEFAULT '',delivery_target_json TEXT NOT NULL DEFAULT '{}',source_bindings_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS project_sources (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,title TEXT NOT NULL,source_type TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',source_url TEXT NOT NULL DEFAULT '',is_prd INTEGER NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,parent_source_id TEXT,checksum TEXT NOT NULL DEFAULT '',metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS project_runs (project_id TEXT NOT NULL,run_id TEXT PRIMARY KEY,source_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_events (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL,source_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_connections (id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT NOT NULL,base_url TEXT NOT NULL DEFAULT '',extra_json TEXT NOT NULL DEFAULT '{}',secret_encrypted TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'configured',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_presets (id TEXT PRIMARY KEY,name TEXT NOT NULL,connection_id TEXT NOT NULL,model TEXT NOT NULL DEFAULT '',fast_model TEXT NOT NULL,smart_model TEXT NOT NULL,strategic_model TEXT NOT NULL,temperature REAL NOT NULL,reasoning_effort TEXT NOT NULL,is_default INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
""")
            project_cols = {
                row[1] for row in c.execute("PRAGMA table_info(projects)").fetchall()
            }
            for column, ddl in {
                "product_id": "TEXT NOT NULL DEFAULT ''",
                "owner_actor": "TEXT NOT NULL DEFAULT ''",
                "delivery_target_json": "TEXT NOT NULL DEFAULT '{}'",
                "source_bindings_json": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if column not in project_cols:
                    c.execute(f"ALTER TABLE projects ADD COLUMN {column} {ddl}")
            cols = {
                row[1]
                for row in c.execute("PRAGMA table_info(project_sources)").fetchall()
            }
            if "parent_source_id" not in cols:
                c.execute(
                    "ALTER TABLE project_sources ADD COLUMN parent_source_id TEXT"
                )
            if "checksum" not in cols:
                c.execute(
                    "ALTER TABLE project_sources ADD COLUMN checksum TEXT NOT NULL DEFAULT ''"
                )
            if "metadata_json" not in cols:
                c.execute(
                    "ALTER TABLE project_sources ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                )
            preset_cols = {
                row[1]
                for row in c.execute("PRAGMA table_info(model_presets)").fetchall()
            }
            if "model" not in preset_cols:
                c.execute(
                    "ALTER TABLE model_presets ADD COLUMN model TEXT NOT NULL DEFAULT ''"
                )
                c.execute(
                    "UPDATE model_presets SET model = COALESCE(NULLIF(smart_model, ''), NULLIF(fast_model, ''), NULLIF(strategic_model, ''), '') "
                    "WHERE model = '' OR model IS NULL"
                )
            c.commit()

    def rows(self, q, p=()):
        with sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            return [dict(row) for row in c.execute(q, p).fetchall()]

    def execute(self, q, p=()):
        with sqlite3.connect(self.path) as c:
            c.execute(q, p)
            c.commit()


def _public_preset(row: dict[str, Any]) -> dict[str, Any]:
    model = str(
        row.get("model")
        or row.get("smart_model")
        or row.get("fast_model")
        or row.get("strategic_model")
        or ""
    ).strip()
    payload = dict(row)
    payload["model"] = model
    return payload


class SecretBox:
    def __init__(self):
        key = (
            os.getenv("PM_PAL_SECRETS_MASTER_KEY", "").strip()
            or os.getenv("MARRDP_SECRETS_MASTER_KEY", "").strip()
        )
        self.box = Fernet(key.encode()) if key else None

    def encrypt(self, secret: str) -> str:
        if not secret:
            return ""
        if not self.box:
            raise HTTPException(
                400,
                detail="Set PM_PAL_SECRETS_MASTER_KEY before saving a provider API key.",
            )
        return self.box.encrypt(secret.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        if not self.box:
            raise HTTPException(
                503,
                detail="Provider secrets are locked: PM_PAL_SECRETS_MASTER_KEY is not configured.",
            )
        try:
            return self.box.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise HTTPException(
                503, detail="Provider secret cannot be decrypted with this master key."
            ) from exc


def create_project_space_router(
    *,
    db_path: Path,
    enqueue_review: Callable[..., Awaitable[dict[str, Any]]],
    get_run_status: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    get_run_result: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    stream_progress: Callable[[str], Awaitable[StreamingResponse]] | None = None,
    submit_clarification: Callable[
        [str, ClarificationAnswerRequest], Awaitable[dict[str, Any]]
    ]
    | None = None,
    update_revision_stage: Callable[
        [str, RevisionStageRequest], Awaitable[dict[str, Any]]
    ]
    | None = None,
    submit_revision_input: Callable[
        [str, RevisionInputRequest], Awaitable[dict[str, Any]]
    ]
    | None = None,
    generate_revision: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    confirm_revision: Callable[[str, RevisionConfirmRequest], Awaitable[dict[str, Any]]]
    | None = None,
    generate_roadmap: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    get_artifact_preview: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    get_report: Callable[[str, str], Awaitable[Response]] | None = None,
):
    store, secrets = Store(db_path), SecretBox()
    store.initialize()
    domain_repository = ProjectDomainRepository(db_path)
    domain_repository.initialize()
    sync_store = ConnectorSyncStore(db_path)
    sync_store.initialize()
    feishu_config_store = FeishuConfigStore(
        db_path,
        encrypt_secret=secrets.encrypt,
        decrypt_secret=secrets.decrypt,
    )
    feishu_config_store.initialize()
    github_config_store = GitHubConfigStore(
        db_path,
        encrypt_secret=secrets.encrypt,
        decrypt_secret=secrets.decrypt,
    )
    github_config_store.initialize()
    notion_config_store = NotionConfigStore(
        db_path,
        encrypt_secret=secrets.encrypt,
        decrypt_secret=secrets.decrypt,
    )
    notion_config_store.initialize()
    register_feishu_sync_handler(
        project_store=store,
        config_store=feishu_config_store,
        new_id=new_id,
        now=now,
    )
    register_github_sync_handler(
        project_store=store,
        config_store=github_config_store,
        new_id=new_id,
        now=now,
    )
    register_notion_sync_handler(
        project_store=store,
        config_store=notion_config_store,
        new_id=new_id,
        now=now,
    )
    router = APIRouter(prefix="/api", tags=["project-space"])

    def public_connection(row):
        row = dict(row)
        row["has_api_key"] = bool(row.pop("secret_encrypted", ""))
        row["api_key_masked"] = "********" if row["has_api_key"] else ""
        row["extra"] = _public_extra(json.loads(row.pop("extra_json", "{}")))
        return row

    def get_project(project_id):
        rows = store.rows("SELECT * FROM projects WHERE id=?", (project_id,))
        if not rows:
            raise HTTPException(404, detail="Project not found")
        item = rows[0]
        item["delivery_target"] = json.loads(
            item.pop("delivery_target_json", "{}") or "{}"
        )
        item["source_bindings"] = json.loads(
            item.pop("source_bindings_json", "[]") or "[]"
        )
        item["sources"] = [
            public_source(row)
            for row in store.rows(
                "SELECT id,title,source_type,source_url,is_prd,version,created_at,parent_source_id,checksum,metadata_json "
                "FROM project_sources WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
        ]
        item["runs"] = store.rows(
            "SELECT run_id,source_id,created_at FROM project_runs WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )
        return item

    @router.get("/provider-catalog")
    async def provider_catalog():
        return {
            "providers": [
                _provider_catalog_entry(p) for p in sorted(SUPPORTED_PROVIDERS)
            ]
        }

    @router.get("/provider-connections")
    async def list_connections():
        return {
            "connections": [
                public_connection(x)
                for x in store.rows(
                    "SELECT * FROM provider_connections ORDER BY updated_at DESC"
                )
            ],
            "master_key_configured": secrets.box is not None,
        }

    @router.post("/provider-connections")
    async def create_connection(p: ConnectionCreate):
        if p.provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(400, detail="Unsupported provider")
        if p.provider != LOCAL_PROVIDER and not p.api_key.strip():
            raise HTTPException(400, detail="An API key is required for this provider.")
        connection_id, timestamp = new_id("conn"), now()
        store.execute(
            "INSERT INTO provider_connections VALUES (?,?,?,?,?,?,?,?,?)",
            (
                connection_id,
                p.name.strip(),
                p.provider,
                p.base_url.strip(),
                json.dumps(p.extra),
                secrets.encrypt(p.api_key.strip()),
                "configured",
                timestamp,
                timestamp,
            ),
        )
        return public_connection(
            store.rows(
                "SELECT * FROM provider_connections WHERE id=?", (connection_id,)
            )[0]
        )

    @router.patch("/provider-connections/{connection_id}")
    async def update_connection(connection_id: str, p: ConnectionUpdate):
        rows = store.rows(
            "SELECT * FROM provider_connections WHERE id=?", (connection_id,)
        )
        if not rows:
            raise HTTPException(404, detail="Provider connection not found")
        old = rows[0]
        store.execute(
            "UPDATE provider_connections SET name=?,base_url=?,extra_json=?,secret_encrypted=?,updated_at=? WHERE id=?",
            (
                p.name.strip() if p.name is not None else old["name"],
                p.base_url.strip() if p.base_url is not None else old["base_url"],
                json.dumps(
                    p.extra if p.extra is not None else json.loads(old["extra_json"])
                ),
                secrets.encrypt(p.api_key.strip())
                if p.api_key is not None
                else old["secret_encrypted"],
                now(),
                connection_id,
            ),
        )
        return public_connection(
            store.rows(
                "SELECT * FROM provider_connections WHERE id=?", (connection_id,)
            )[0]
        )

    @router.delete("/provider-connections/{connection_id}")
    async def delete_connection(connection_id: str):
        store.execute("DELETE FROM provider_connections WHERE id=?", (connection_id,))
        return {"deleted": True}

    @router.post("/provider-connections/{connection_id}/test")
    async def test_connection(connection_id: str):
        rows = store.rows(
            "SELECT * FROM provider_connections WHERE id=?", (connection_id,)
        )
        if not rows:
            raise HTTPException(404, detail="Provider connection not found")
        item = rows[0]
        item["delivery_target"] = json.loads(
            item.pop("delivery_target_json", "{}") or "{}"
        )
        item["source_bindings"] = json.loads(
            item.pop("source_bindings_json", "[]") or "[]"
        )
        package = PROVIDER_PACKAGES.get(item["provider"], "langchain_community")
        if importlib.util.find_spec(package) is None:
            pip_name = package_pip_name(package)
            raise HTTPException(
                409,
                detail={
                    "message": f"Install dependency: pip install -U {pip_name}",
                    "requires_package": package,
                    "install_hint": f"pip install -U {pip_name}",
                },
            )
        secret = ""
        if item["provider"] != LOCAL_PROVIDER:
            secret = secrets.decrypt(item["secret_encrypted"])
        extra = json.loads(item.get("extra_json") or "{}")
        try:
            probe_result = probe_provider_connection(
                item["provider"],
                api_key=secret,
                base_url=item.get("base_url") or "",
                extra=extra,
            )
        except Exception as exc:
            raise HTTPException(502, detail=f"Connection probe failed: {exc}") from exc
        store.execute(
            "UPDATE provider_connections SET status=?,updated_at=? WHERE id=?",
            ("validated", now(), connection_id),
        )
        return {
            "ok": True,
            "status": "validated",
            "message": probe_result.get("message") or "Connection validated.",
        }

    @router.get("/model-presets")
    async def list_presets():
        return {
            "presets": [
                _public_preset(row)
                for row in store.rows(
                    "SELECT * FROM model_presets ORDER BY is_default DESC,updated_at DESC"
                )
            ]
        }

    def save_preset(preset_id, p: PresetCreate):
        if not store.rows(
            "SELECT id FROM provider_connections WHERE id=?", (p.connection_id,)
        ):
            raise HTTPException(400, detail="Provider connection not found")
        if p.is_default:
            store.execute("UPDATE model_presets SET is_default=0")
        model = p.resolved_model()
        stamp = now()
        # Keep legacy columns in sync so older DB rows remain readable :-)
        store.execute(
            "INSERT OR REPLACE INTO model_presets "
            "(id,name,connection_id,model,fast_model,smart_model,strategic_model,temperature,reasoning_effort,is_default,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                preset_id,
                p.name,
                p.connection_id,
                model,
                model,
                model,
                model,
                p.temperature,
                p.reasoning_effort,
                int(p.is_default),
                stamp,
                stamp,
            ),
        )
        return _public_preset(
            store.rows("SELECT * FROM model_presets WHERE id=?", (preset_id,))[0]
        )

    @router.post("/model-presets")
    async def create_preset(p: PresetCreate):
        return save_preset(new_id("preset"), p)

    @router.patch("/model-presets/{preset_id}")
    async def update_preset(preset_id: str, p: PresetCreate):
        if not store.rows("SELECT id FROM model_presets WHERE id=?", (preset_id,)):
            raise HTTPException(404, detail="Model preset not found")
        return save_preset(preset_id, p)

    @router.delete("/model-presets/{preset_id}")
    async def delete_preset(preset_id: str):
        store.execute("DELETE FROM model_presets WHERE id=?", (preset_id,))
        return {"deleted": True}

    @router.get("/projects")
    async def list_projects():
        return {
            "projects": store.rows(
                "SELECT p.*,COUNT(DISTINCT s.id) source_count,COUNT(DISTINCT r.run_id) run_count FROM projects p LEFT JOIN project_sources s ON s.project_id=p.id LEFT JOIN project_runs r ON r.project_id=p.id GROUP BY p.id ORDER BY p.updated_at DESC"
            )
        }

    @router.post("/projects")
    async def create_project(p: ProjectCreate):
        project_id, stamp = new_id("project"), now()
        store.execute(
            "INSERT INTO projects (id,name,description,model_preset_id,created_at,updated_at,product_id,owner_actor,delivery_target_json,source_bindings_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                p.name.strip(),
                p.description.strip(),
                p.model_preset_id,
                stamp,
                stamp,
                p.product_id.strip(),
                p.owner_actor.strip(),
                json.dumps(p.delivery_target),
                json.dumps(p.source_bindings),
            ),
        )
        return get_project(project_id)

    @router.get("/projects/{project_id}")
    async def project(project_id: str):
        return get_project(project_id)

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: str, p: ProjectUpdate):
        old = get_project(project_id)
        store.execute(
            "UPDATE projects SET name=?,description=?,model_preset_id=?,product_id=?,owner_actor=?,delivery_target_json=?,source_bindings_json=?,updated_at=? WHERE id=?",
            (
                p.name.strip() if p.name is not None else old["name"],
                p.description.strip()
                if p.description is not None
                else old["description"],
                p.model_preset_id
                if p.model_preset_id is not None
                else old["model_preset_id"],
                p.product_id.strip()
                if p.product_id is not None
                else old.get("product_id", ""),
                p.owner_actor.strip()
                if p.owner_actor is not None
                else old.get("owner_actor", ""),
                json.dumps(
                    p.delivery_target
                    if p.delivery_target is not None
                    else old["delivery_target"]
                ),
                json.dumps(
                    p.source_bindings
                    if p.source_bindings is not None
                    else old["source_bindings"]
                ),
                now(),
                project_id,
            ),
        )
        return get_project(project_id)

    @router.get("/projects/{project_id}/context")
    async def project_context(project_id: str):
        item = get_project(project_id)
        return {
            "project_id": project_id,
            "product_id": item.get("product_id", ""),
            "owner_actor": item.get("owner_actor", ""),
            "model_preset_id": item.get("model_preset_id"),
            "delivery_target": item["delivery_target"],
            "source_bindings": item["source_bindings"],
            "connectors": list_connector_summaries(sync_store, project_id=project_id),
            "stats": {"sources": len(item["sources"]), "runs": len(item["runs"])},
        }

    @router.post("/projects/{project_id}/sources")
    async def add_source(project_id: str, p: SourceCreate):
        get_project(project_id)
        return create_source_version(
            store,
            project_id=project_id,
            title=p.title.strip(),
            source_type=p.source_type,
            content=p.content,
            source_url=p.source_url.strip(),
            is_prd=p.is_prd,
            parent_source_id=p.parent_source_id,
            metadata_extra={"origin": "manual"},
            new_id=new_id,
            now=now,
        )

    @router.post("/projects/{project_id}/sources/from-url")
    async def add_source_from_url(project_id: str, p: SourceFromUrl):
        get_project(project_id)
        source_url = p.source_url.strip()
        try:
            connector = ConnectorRegistry().resolve(source_url)
            document = await asyncio.to_thread(connector.get_content, source_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            payload = get_connector_error_payload(exc)
            if payload is None:
                raise HTTPException(
                    status_code=400, detail=str(exc) or "Failed to fetch source."
                ) from exc
            status_by_code = {
                ConnectorErrorCode.authentication_failed: 401,
                ConnectorErrorCode.permission_denied: 403,
                ConnectorErrorCode.not_found: 404,
                ConnectorErrorCode.network_unavailable: 502,
                ConnectorErrorCode.rate_limited: 429,
            }
            message = payload.message
            if payload.code == ConnectorErrorCode.authentication_failed:
                message = (
                    f"{payload.message} "
                    "请在本机配置 MARRDP_FEISHU_APP_ID / MARRDP_FEISHU_APP_SECRET，"
                    "或在项目 connectors/feishu 中填写凭证。"
                )
            raise HTTPException(
                status_code=status_by_code.get(payload.code, 400),
                detail={
                    "code": str(payload.code),
                    "message": message,
                    "source": payload.source,
                },
            ) from exc
        source_type = (
            document.source_type.value
            if hasattr(document.source_type, "value")
            else str(document.source_type or "url")
        )
        title = (p.title or document.title or source_type).strip() or "Connected source"
        return create_source_version(
            store,
            project_id=project_id,
            title=title,
            source_type=source_type,
            content=document.content_markdown or "",
            source_url=source_url,
            is_prd=p.is_prd,
            parent_source_id=None,
            metadata_extra={
                "origin": "from_url",
                "mime_type": document.metadata.mime_type or "text/markdown",
                "filename": f"{title}.md",
                "connector": source_type,
            },
            new_id=new_id,
            now=now,
            event_kind="source_synced",
        )

    @router.post("/projects/{project_id}/sources/upload")
    async def upload_source(
        project_id: str,
        file: UploadFile = File(...),
        title: str = Form(""),
        parent_source_id: str | None = Form(None),
        is_prd: bool = Form(True),
    ):
        get_project(project_id)
        filename = (file.filename or "upload.txt").strip()
        raw = await file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                400, detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit."
            )
        content, mime_type = parse_upload_bytes(filename, raw)
        resolved_title = title.strip() or Path(filename).stem
        return create_source_version(
            store,
            project_id=project_id,
            title=resolved_title,
            source_type="upload",
            content=content,
            source_url="",
            is_prd=is_prd,
            parent_source_id=parent_source_id or None,
            metadata_extra={
                "origin": "upload",
                "filename": filename,
                "mime_type": mime_type,
            },
            new_id=new_id,
            now=now,
            event_kind="source_uploaded",
        )

    @router.get("/projects/{project_id}/sources/{source_id}")
    async def get_source(project_id: str, source_id: str):
        get_project(project_id)
        return public_source(get_source_or_404(store, project_id, source_id))

    @router.get("/projects/{project_id}/sources/{source_id}/diff")
    async def source_diff(project_id: str, source_id: str, against: str = Query(...)):
        get_project(project_id)
        return diff_sources(store, project_id, source_id, against)

    @router.post("/projects/{project_id}/sources/{source_id}/rollback")
    async def source_rollback(project_id: str, source_id: str):
        get_project(project_id)
        return rollback_source(
            store, project_id=project_id, source_id=source_id, new_id=new_id, now=now
        )

    @router.patch("/projects/{project_id}/sources/{source_id}")
    async def update_source(project_id: str, source_id: str, p: SourceUpdate):
        get_project(project_id)
        old = get_source_or_404(store, project_id, source_id)
        stamp = now()
        store.execute(
            "UPDATE project_sources SET title=?,source_url=?,is_prd=? WHERE id=?",
            (
                p.title.strip() if p.title is not None else old["title"],
                p.source_url.strip() if p.source_url is not None else old["source_url"],
                int(p.is_prd if p.is_prd is not None else old["is_prd"]),
                source_id,
            ),
        )
        record_event(
            store,
            project_id=project_id,
            kind="source_updated",
            label=f"{p.title or old['title']} v{old['version']}",
            source_id=source_id,
            new_id=new_id,
            now=now,
        )
        store.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id)
        )
        return public_source(get_source_or_404(store, project_id, source_id))

    @router.delete("/projects/{project_id}/sources/{source_id}")
    async def delete_source(project_id: str, source_id: str):
        get_project(project_id)
        old = get_source_or_404(store, project_id, source_id)
        store.execute(
            "DELETE FROM project_sources WHERE id=? AND project_id=?",
            (source_id, project_id),
        )
        record_event(
            store,
            project_id=project_id,
            kind="source_deleted",
            label=f"{old['title']} v{old['version']}",
            source_id=source_id,
            new_id=new_id,
            now=now,
        )
        store.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id)
        )
        return {"deleted": True, "id": source_id}

    @router.get("/projects/{project_id}/timeline")
    async def timeline(project_id: str):
        get_project(project_id)
        return {
            "events": store.rows(
                "SELECT created_at,kind,label,source_id FROM project_events WHERE project_id=? "
                "UNION ALL SELECT created_at,'review' kind,run_id label,NULL source_id FROM project_runs WHERE project_id=? "
                "ORDER BY created_at DESC",
                (project_id, project_id),
            )
        }

    @router.post("/projects/{project_id}/reviews")
    async def review(project_id: str, p: ProjectReview):
        item = get_project(project_id)
        source_id = p.source_id or (item["sources"][0]["id"] if item["sources"] else "")
        rows = store.rows(
            "SELECT * FROM project_sources WHERE id=? AND project_id=?",
            (source_id, project_id),
        )
        if not rows:
            raise HTTPException(
                400, detail="Add a project source before starting a review."
            )
        source = rows[0]
        preset_id = p.model_preset_id or item.get("model_preset_id")
        options = {}
        if preset_id:
            presets = store.rows("SELECT * FROM model_presets WHERE id=?", (preset_id,))
            if not presets:
                raise HTTPException(400, detail="Selected model preset not found")
            preset = presets[0]
            conn = store.rows(
                "SELECT * FROM provider_connections WHERE id=?",
                (preset["connection_id"],),
            )[0]
            model_name = (
                preset.get("model")
                or preset.get("smart_model")
                or preset.get("fast_model")
                or preset.get("strategic_model")
                or ""
            )
            options = {
                "llm": f"{conn['provider']}:{model_name}",
                "temperature": preset["temperature"],
                "reasoning_effort": preset["reasoning_effort"],
            }
            llm_kwargs = _connection_llm_kwargs(
                conn, secrets.decrypt(conn["secret_encrypted"])
            )
            if llm_kwargs:
                options["llm_kwargs"] = llm_kwargs
        result = await enqueue_review(
            prd_text=source["content"] or None,
            source=source["source_url"] or None,
            mode=p.mode,
            llm_options=options,
            audit_context={
                "source": "project_space",
                "actor": "local",
                "client_metadata": redact_mapping(
                    {
                        "project_id": project_id,
                        "source_id": source_id,
                        "model_preset_id": preset_id or "",
                    }
                ),
            },
        )
        store.execute(
            "INSERT INTO project_runs VALUES (?,?,?,?)",
            (project_id, result["run_id"], source_id, now()),
        )
        store.execute(
            "UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id)
        )
        return result | {"project_id": project_id}

    def ensure_project_run(project_id: str, run_id: str) -> None:
        get_project(project_id)
        if not store.rows(
            "SELECT run_id FROM project_runs WHERE project_id=? AND run_id=?",
            (project_id, run_id),
        ):
            raise HTTPException(404, detail="Review run is not part of this project")

    def require_handler(handler: Callable | None, name: str) -> Callable:
        if handler is None:
            raise HTTPException(503, detail=f"Review {name} service is unavailable")
        return handler

    @router.get("/projects/{project_id}/reviews/{run_id}")
    async def project_review_status(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(get_run_status, "status")(run_id)

    @router.get("/projects/{project_id}/reviews/{run_id}/result")
    async def project_review_result(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(get_run_result, "result")(run_id)

    @router.get("/projects/{project_id}/reviews/{run_id}/progress/stream")
    async def project_review_progress_stream(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(stream_progress, "progress stream")(run_id)

    @router.post("/projects/{project_id}/reviews/{run_id}/clarification")
    async def project_review_clarification(
        project_id: str, run_id: str, payload: ClarificationAnswerRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(submit_clarification, "clarification")(
            run_id, payload
        )

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-stage")
    async def project_review_revision_stage(
        project_id: str, run_id: str, payload: RevisionStageRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(update_revision_stage, "revision stage")(
            run_id, payload
        )

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-input")
    async def project_review_revision_input(
        project_id: str, run_id: str, payload: RevisionInputRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(submit_revision_input, "revision input")(
            run_id, payload
        )

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-generate")
    async def project_review_revision_generate(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(generate_revision, "revision generate")(run_id)

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-confirm")
    async def project_review_revision_confirm(
        project_id: str, run_id: str, payload: RevisionConfirmRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(confirm_revision, "revision confirm")(
            run_id, payload
        )

    @router.post("/projects/{project_id}/reviews/{run_id}/roadmap-generate")
    async def project_review_roadmap_generate(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(generate_roadmap, "roadmap")(run_id)

    @router.get("/projects/{project_id}/reviews/{run_id}/artifacts/{artifact_key}")
    async def project_review_artifact(project_id: str, run_id: str, artifact_key: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(get_artifact_preview, "artifact preview")(
            run_id, artifact_key
        )

    @router.get("/projects/{project_id}/reviews/{run_id}/report")
    async def project_review_report(
        project_id: str,
        run_id: str,
        format: str = Query(default="md"),
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(get_report, "report")(run_id, format)

    @router.get("/projects/by-run/{run_id}")
    async def lookup_project_by_run(run_id: str):
        rows = store.rows(
            "SELECT project_id FROM project_runs WHERE run_id=?", (run_id,)
        )
        if not rows:
            raise HTTPException(404, detail="Review run is not linked to a project")
        return {"project_id": rows[0]["project_id"], "run_id": run_id}

    register_connector_sync_routes(
        router,
        sync_store=sync_store,
        get_project=get_project,
        new_id=new_id,
        now=now,
    )

    register_feishu_connector_config_routes(
        router,
        config_store=feishu_config_store,
        get_project=get_project,
        now=now,
    )

    register_github_connector_config_routes(
        router,
        config_store=github_config_store,
        get_project=get_project,
        now=now,
    )

    register_notion_connector_config_routes(
        router,
        config_store=notion_config_store,
        get_project=get_project,
        now=now,
    )

    register_project_domain_routes(router, repository=domain_repository)

    return (
        router,
        feishu_config_store,
        sync_store,
        github_config_store,
        notion_config_store,
        store,
        domain_repository,
    )
