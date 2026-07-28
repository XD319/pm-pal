"""Local-first project and provider APIs for the open-source workspace."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from prd_pal.runtime.llm_provider.generic.base import _SUPPORTED_PROVIDERS
from prd_pal.server.job_state import (
    ClarificationAnswerRequest,
    RevisionConfirmRequest,
    RevisionInputRequest,
    RevisionStageRequest,
)

LOCAL_PROVIDER = "ollama"
PROVIDER_PACKAGES = {"openai": "langchain_openai", "deepseek": "langchain_openai", "azure_openai": "langchain_openai", "ollama": "langchain_ollama", "anthropic": "langchain_anthropic", "groq": "langchain_groq", "google_genai": "langchain_google_genai", "google_vertexai": "langchain_google_vertexai", "bedrock": "langchain_aws", "cohere": "langchain_cohere", "mistralai": "langchain_mistralai", "fireworks": "langchain_fireworks", "huggingface": "langchain_huggingface", "gigachat": "langchain_gigachat", "netmind": "langchain_netmind"}

def now() -> str: return datetime.now(timezone.utc).isoformat()
def new_id(kind: str) -> str: return f"{kind}_{uuid.uuid4().hex[:12]}"

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    model_preset_id: str | None = None
class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    model_preset_id: str | None = None
class SourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_type: str = "prd_text"
    content: str = ""
    source_url: str = ""
    is_prd: bool = True
    @model_validator(mode="after")
    def input_present(self):
        if not self.content.strip() and not self.source_url.strip(): raise ValueError("Provide source content or a source URL.")
        return self
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
    fast_model: str = Field(min_length=1)
    smart_model: str = Field(min_length=1)
    strategic_model: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0, le=2)
    reasoning_effort: str = Field(default="medium", pattern="^(low|medium|high)$")
    is_default: bool = False

class Store:
    def __init__(self, path: Path): self.path = path
    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as c: c.executescript("""
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',model_preset_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_sources (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,title TEXT NOT NULL,source_type TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',source_url TEXT NOT NULL DEFAULT '',is_prd INTEGER NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_runs (project_id TEXT NOT NULL,run_id TEXT PRIMARY KEY,source_id TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_connections (id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT NOT NULL,base_url TEXT NOT NULL DEFAULT '',extra_json TEXT NOT NULL DEFAULT '{}',secret_encrypted TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'configured',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_presets (id TEXT PRIMARY KEY,name TEXT NOT NULL,connection_id TEXT NOT NULL,fast_model TEXT NOT NULL,smart_model TEXT NOT NULL,strategic_model TEXT NOT NULL,temperature REAL NOT NULL,reasoning_effort TEXT NOT NULL,is_default INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
""")
    def rows(self, q, p=()):
        with sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            return [dict(row) for row in c.execute(q, p).fetchall()]
    def execute(self, q, p=()):
        with sqlite3.connect(self.path) as c: c.execute(q, p); c.commit()

class SecretBox:
    def __init__(self):
        key = os.getenv("MARRDP_SECRETS_MASTER_KEY", "").strip()
        self.box = Fernet(key.encode()) if key else None
    def encrypt(self, secret: str) -> str:
        if not secret: return ""
        if not self.box: raise HTTPException(400, detail="Set MARRDP_SECRETS_MASTER_KEY before saving a provider API key.")
        return self.box.encrypt(secret.encode()).decode()
    def decrypt(self, token: str) -> str:
        if not token: return ""
        if not self.box: raise HTTPException(503, detail="Provider secrets are locked: MARRDP_SECRETS_MASTER_KEY is not configured.")
        try: return self.box.decrypt(token.encode()).decode()
        except InvalidToken as exc: raise HTTPException(503, detail="Provider secret cannot be decrypted with this master key.") from exc

def create_project_space_router(
    *,
    db_path: Path,
    enqueue_review: Callable[..., Awaitable[dict[str, Any]]],
    get_run_status: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    get_run_result: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    stream_progress: Callable[[str], Awaitable[StreamingResponse]] | None = None,
    submit_clarification: Callable[[str, ClarificationAnswerRequest], Awaitable[dict[str, Any]]] | None = None,
    update_revision_stage: Callable[[str, RevisionStageRequest], Awaitable[dict[str, Any]]] | None = None,
    submit_revision_input: Callable[[str, RevisionInputRequest], Awaitable[dict[str, Any]]] | None = None,
    generate_revision: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    confirm_revision: Callable[[str, RevisionConfirmRequest], Awaitable[dict[str, Any]]] | None = None,
    generate_roadmap: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    get_artifact_preview: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
    get_report: Callable[[str, str], Awaitable[Response]] | None = None,
) -> APIRouter:
    store, secrets = Store(db_path), SecretBox(); store.initialize()
    router = APIRouter(prefix="/api", tags=["project-space"])
    def public_connection(row):
        row["has_api_key"] = bool(row.pop("secret_encrypted", "")); row["api_key_masked"] = "••••••••" if row["has_api_key"] else ""; row["extra"] = json.loads(row.pop("extra_json", "{}")); return row
    def get_project(project_id):
        rows = store.rows("SELECT * FROM projects WHERE id=?", (project_id,))
        if not rows: raise HTTPException(404, detail="Project not found")
        item = rows[0]
        item["sources"] = store.rows("SELECT id,title,source_type,source_url,is_prd,version,created_at FROM project_sources WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        item["runs"] = store.rows("SELECT run_id,source_id,created_at FROM project_runs WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        return item
    @router.get("/provider-catalog")
    async def provider_catalog():
        return {"providers": [{"id": p, "label": p.replace("_", " ").title(), "requires_api_key": p != LOCAL_PROVIDER, "requires_package": PROVIDER_PACKAGES.get(p, "langchain-community"), "available": importlib.util.find_spec(PROVIDER_PACKAGES.get(p, "langchain_community")) is not None, "fields": ["api_key", "base_url"] if p not in {"ollama", "bedrock", "google_vertexai"} else ["base_url"]} for p in sorted(_SUPPORTED_PROVIDERS)]}
    @router.get("/provider-connections")
    async def list_connections(): return {"connections": [public_connection(x) for x in store.rows("SELECT * FROM provider_connections ORDER BY updated_at DESC")], "master_key_configured": secrets.box is not None}
    @router.post("/provider-connections")
    async def create_connection(p: ConnectionCreate):
        if p.provider not in _SUPPORTED_PROVIDERS: raise HTTPException(400, detail="Unsupported provider")
        if p.provider != LOCAL_PROVIDER and not p.api_key.strip(): raise HTTPException(400, detail="An API key is required for this provider.")
        connection_id, timestamp = new_id("conn"), now()
        store.execute("INSERT INTO provider_connections VALUES (?,?,?,?,?,?,?,?,?)", (connection_id, p.name.strip(), p.provider, p.base_url.strip(), json.dumps(p.extra), secrets.encrypt(p.api_key.strip()), "configured", timestamp, timestamp))
        return public_connection(store.rows("SELECT * FROM provider_connections WHERE id=?", (connection_id,))[0])
    @router.patch("/provider-connections/{connection_id}")
    async def update_connection(connection_id: str, p: ConnectionUpdate):
        rows = store.rows("SELECT * FROM provider_connections WHERE id=?", (connection_id,))
        if not rows: raise HTTPException(404, detail="Provider connection not found")
        old = rows[0]
        store.execute("UPDATE provider_connections SET name=?,base_url=?,extra_json=?,secret_encrypted=?,updated_at=? WHERE id=?", (p.name.strip() if p.name is not None else old["name"], p.base_url.strip() if p.base_url is not None else old["base_url"], json.dumps(p.extra if p.extra is not None else json.loads(old["extra_json"])), secrets.encrypt(p.api_key.strip()) if p.api_key is not None else old["secret_encrypted"], now(), connection_id))
        return public_connection(store.rows("SELECT * FROM provider_connections WHERE id=?", (connection_id,))[0])
    @router.delete("/provider-connections/{connection_id}")
    async def delete_connection(connection_id: str): store.execute("DELETE FROM provider_connections WHERE id=?", (connection_id,)); return {"deleted": True}
    @router.post("/provider-connections/{connection_id}/test")
    async def test_connection(connection_id: str):
        rows = store.rows("SELECT * FROM provider_connections WHERE id=?", (connection_id,))
        if not rows: raise HTTPException(404, detail="Provider connection not found")
        item = rows[0]; package = PROVIDER_PACKAGES.get(item["provider"], "langchain_community")
        if importlib.util.find_spec(package) is None: raise HTTPException(409, detail=f"Install dependency: pip install -U {package.replace('_', '-')}")
        if item["provider"] != LOCAL_PROVIDER: secrets.decrypt(item["secret_encrypted"])
        store.execute("UPDATE provider_connections SET status=?,updated_at=? WHERE id=?", ("validated", now(), connection_id))
        return {"ok": True, "status": "validated", "message": "Credentials and local dependency are configured. No billable model request was made."}
    @router.get("/model-presets")
    async def list_presets(): return {"presets": store.rows("SELECT * FROM model_presets ORDER BY is_default DESC,updated_at DESC")}
    def save_preset(preset_id, p):
        if not store.rows("SELECT id FROM provider_connections WHERE id=?", (p.connection_id,)): raise HTTPException(400, detail="Provider connection not found")
        if p.is_default: store.execute("UPDATE model_presets SET is_default=0")
        stamp = now(); store.execute("INSERT OR REPLACE INTO model_presets VALUES (?,?,?,?,?,?,?,?,?,?,?)", (preset_id, p.name, p.connection_id, p.fast_model, p.smart_model, p.strategic_model, p.temperature, p.reasoning_effort, int(p.is_default), stamp, stamp)); return store.rows("SELECT * FROM model_presets WHERE id=?", (preset_id,))[0]
    @router.post("/model-presets")
    async def create_preset(p: PresetCreate): return save_preset(new_id("preset"), p)
    @router.patch("/model-presets/{preset_id}")
    async def update_preset(preset_id: str, p: PresetCreate):
        if not store.rows("SELECT id FROM model_presets WHERE id=?", (preset_id,)): raise HTTPException(404, detail="Model preset not found")
        return save_preset(preset_id, p)
    @router.delete("/model-presets/{preset_id}")
    async def delete_preset(preset_id: str): store.execute("DELETE FROM model_presets WHERE id=?", (preset_id,)); return {"deleted": True}
    @router.get("/projects")
    async def list_projects(): return {"projects": store.rows("SELECT p.*,COUNT(DISTINCT s.id) source_count,COUNT(DISTINCT r.run_id) run_count FROM projects p LEFT JOIN project_sources s ON s.project_id=p.id LEFT JOIN project_runs r ON r.project_id=p.id GROUP BY p.id ORDER BY p.updated_at DESC")}
    @router.post("/projects")
    async def create_project(p: ProjectCreate):
        project_id, stamp = new_id("project"), now(); store.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)", (project_id, p.name.strip(), p.description.strip(), p.model_preset_id, stamp, stamp)); return get_project(project_id)
    @router.get("/projects/{project_id}")
    async def project(project_id: str): return get_project(project_id)
    @router.patch("/projects/{project_id}")
    async def update_project(project_id: str, p: ProjectUpdate):
        old = get_project(project_id); store.execute("UPDATE projects SET name=?,description=?,model_preset_id=?,updated_at=? WHERE id=?", (p.name.strip() if p.name is not None else old["name"], p.description.strip() if p.description is not None else old["description"], p.model_preset_id if p.model_preset_id is not None else old["model_preset_id"], now(), project_id)); return get_project(project_id)
    @router.post("/projects/{project_id}/sources")
    async def add_source(project_id: str, p: SourceCreate):
        get_project(project_id); source_id, stamp = new_id("source"), now(); version = len(store.rows("SELECT id FROM project_sources WHERE project_id=? AND title=?", (project_id, p.title))) + 1
        store.execute("INSERT INTO project_sources VALUES (?,?,?,?,?,?,?,?,?)", (source_id, project_id, p.title, p.source_type, p.content, p.source_url, int(p.is_prd), version, stamp)); store.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id)); return {"id": source_id, "version": version}
    @router.get("/projects/{project_id}/timeline")
    async def timeline(project_id: str):
        get_project(project_id); return {"events": store.rows("SELECT created_at,'source' kind,title label FROM project_sources WHERE project_id=? UNION ALL SELECT created_at,'review' kind,run_id label FROM project_runs WHERE project_id=? ORDER BY created_at DESC", (project_id, project_id))}
    @router.post("/projects/{project_id}/reviews")
    async def review(project_id: str, p: ProjectReview):
        item = get_project(project_id); source_id = p.source_id or (item["sources"][0]["id"] if item["sources"] else ""); rows = store.rows("SELECT * FROM project_sources WHERE id=? AND project_id=?", (source_id, project_id))
        if not rows: raise HTTPException(400, detail="Add a project source before starting a review.")
        source = rows[0]; preset_id = p.model_preset_id or item.get("model_preset_id"); options = {}
        if preset_id:
            presets = store.rows("SELECT * FROM model_presets WHERE id=?", (preset_id,))
            if not presets: raise HTTPException(400, detail="Selected model preset not found")
            preset = presets[0]; conn = store.rows("SELECT * FROM provider_connections WHERE id=?", (preset["connection_id"],))[0]
            options = {"fast_llm": f"{conn['provider']}:{preset['fast_model']}", "smart_llm": f"{conn['provider']}:{preset['smart_model']}", "strategic_llm": f"{conn['provider']}:{preset['strategic_model']}", "temperature": preset["temperature"], "reasoning_effort": preset["reasoning_effort"]}
            secret = secrets.decrypt(conn["secret_encrypted"])
            if secret:
                options["llm_kwargs"] = {"api_key": secret}
            if conn["base_url"]:
                options.setdefault("llm_kwargs", {})["base_url"] = conn["base_url"]
        result = await enqueue_review(prd_text=source["content"] or None, source=source["source_url"] or None, mode=p.mode, llm_options=options, audit_context={"source": "project_space", "actor": "local", "client_metadata": {"project_id": project_id, "source_id": source_id, "model_preset_id": preset_id or ""}})
        store.execute("INSERT INTO project_runs VALUES (?,?,?,?)", (project_id, result["run_id"], source_id, now())); store.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id)); return result | {"project_id": project_id}
    def ensure_project_run(project_id: str, run_id: str) -> None:
        get_project(project_id)
        if not store.rows("SELECT run_id FROM project_runs WHERE project_id=? AND run_id=?", (project_id, run_id)):
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
        return await require_handler(submit_clarification, "clarification")(run_id, payload)

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-stage")
    async def project_review_revision_stage(
        project_id: str, run_id: str, payload: RevisionStageRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(update_revision_stage, "revision stage")(run_id, payload)

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-input")
    async def project_review_revision_input(
        project_id: str, run_id: str, payload: RevisionInputRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(submit_revision_input, "revision input")(run_id, payload)

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-generate")
    async def project_review_revision_generate(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(generate_revision, "revision generate")(run_id)

    @router.post("/projects/{project_id}/reviews/{run_id}/revision-confirm")
    async def project_review_revision_confirm(
        project_id: str, run_id: str, payload: RevisionConfirmRequest
    ):
        ensure_project_run(project_id, run_id)
        return await require_handler(confirm_revision, "revision confirm")(run_id, payload)

    @router.post("/projects/{project_id}/reviews/{run_id}/roadmap-generate")
    async def project_review_roadmap_generate(project_id: str, run_id: str):
        ensure_project_run(project_id, run_id)
        return await require_handler(generate_roadmap, "roadmap")(run_id)

    @router.get("/projects/{project_id}/reviews/{run_id}/artifacts/{artifact_key}")
    async def project_review_artifact(
        project_id: str, run_id: str, artifact_key: str
    ):
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

    return router

