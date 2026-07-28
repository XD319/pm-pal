"""Provider settings, catalog fields, presets, connection probes, and redaction."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prd_pal.monitoring.audit import append_audit_event, read_audit_events
from prd_pal.server.job_state import write_job_snapshot
from prd_pal.server.project_space import create_project_space_router
from prd_pal.utils.redaction import is_sensitive_key, redact_mapping, redact_text


@pytest.fixture()
def master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MARRDP_SECRETS_MASTER_KEY", key)
    return key


@pytest.fixture()
def provider_client(tmp_path: Path, master_key: str):
    captured: dict[str, object] = {}

    async def enqueue_review(**kwargs):
        captured.update(kwargs)
        return {"run_id": "20260728T120001Z", "status": "queued"}

    app = FastAPI()
    app.include_router(
        create_project_space_router(
            db_path=tmp_path / "project_space.sqlite3",
            enqueue_review=enqueue_review,
        )
    )
    return TestClient(app), captured


def test_provider_catalog_includes_extra_fields(provider_client):
    client, _ = provider_client
    payload = client.get("/api/provider-catalog").json()
    azure = next(item for item in payload["providers"] if item["id"] == "azure_openai")
    field_names = {field["name"] for field in azure["fields"]}
    assert {"region", "deployment", "api_version"}.issubset(field_names)
    assert azure["install_hint"].startswith("pip install")


def test_model_preset_crud_and_default(provider_client, master_key: str):
    client, _ = provider_client
    connection = client.post(
        "/api/provider-connections",
        json={"name": "OpenAI", "provider": "openai", "api_key": "sk-test", "base_url": "https://api.example.com/v1"},
    ).json()
    create = client.post(
        "/api/model-presets",
        json={
            "name": "Fast stack",
            "connection_id": connection["id"],
            "fast_model": "gpt-4o-mini",
            "smart_model": "gpt-4o",
            "strategic_model": "gpt-4o",
            "temperature": 0.1,
            "reasoning_effort": "low",
            "is_default": False,
        },
    )
    assert create.status_code == 200
    preset_id = create.json()["id"]

    listed = client.get("/api/model-presets").json()["presets"]
    assert any(item["id"] == preset_id for item in listed)

    updated = client.patch(
        f"/api/model-presets/{preset_id}",
        json={
            "name": "Default stack",
            "connection_id": connection["id"],
            "fast_model": "gpt-4o-mini",
            "smart_model": "gpt-4o",
            "strategic_model": "gpt-4o",
            "temperature": 0.2,
            "reasoning_effort": "medium",
            "is_default": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["is_default"] == 1

    deleted = client.delete(f"/api/model-presets/{preset_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_connection_update_delete_and_public_extra_redaction(provider_client, master_key: str):
    client, _ = provider_client
    created = client.post(
        "/api/provider-connections",
        json={
            "name": "Azure",
            "provider": "azure_openai",
            "api_key": "sk-secret",
            "base_url": "https://example.openai.azure.com",
            "extra": {"region": "eastus", "deployment": "gpt-4o", "client_secret": "hidden"},
        },
    ).json()
    assert created["extra"] == {"region": "eastus", "deployment": "gpt-4o"}
    assert "client_secret" not in created["extra"]

    patched = client.patch(
        f"/api/provider-connections/{created['id']}",
        json={"name": "Azure prod", "extra": {"region": "westus", "deployment": "gpt-4o"}},
    ).json()
    assert patched["name"] == "Azure prod"
    assert patched["extra"]["region"] == "westus"

    deleted = client.delete(f"/api/provider-connections/{created['id']}")
    assert deleted.status_code == 200


def test_connection_test_missing_package_returns_409(provider_client, master_key: str, monkeypatch: pytest.MonkeyPatch):
    client, _ = provider_client
    connection = client.post(
        "/api/provider-connections",
        json={"name": "OpenAI", "provider": "openai", "api_key": "sk-test"},
    ).json()

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    response = client.post(f"/api/provider-connections/{connection['id']}/test")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["requires_package"] == "langchain_openai"
    assert "pip install" in detail["install_hint"]


def test_connection_test_uses_probe(provider_client, master_key: str, monkeypatch: pytest.MonkeyPatch):
    client, _ = provider_client
    connection = client.post(
        "/api/provider-connections",
        json={"name": "Ollama", "provider": "ollama", "base_url": "http://127.0.0.1:11434"},
    ).json()

    def fake_probe(provider, *, api_key="", base_url="", extra=None):
        assert provider == "ollama"
        return {"ok": True, "message": "mock probe ok"}

    monkeypatch.setattr("prd_pal.server.project_space.probe_provider_connection", fake_probe)

    response = client.post(f"/api/provider-connections/{connection['id']}/test")
    assert response.status_code == 200
    assert response.json()["message"] == "mock probe ok"


def test_project_review_uses_preset_server_side(provider_client, master_key: str):
    client, captured = provider_client
    connection = client.post(
        "/api/provider-connections",
        json={"name": "OpenAI", "provider": "openai", "api_key": "sk-test", "base_url": "https://api.example.com/v1"},
    ).json()
    preset = client.post(
        "/api/model-presets",
        json={
            "name": "Stack",
            "connection_id": connection["id"],
            "fast_model": "gpt-4o-mini",
            "smart_model": "gpt-4o",
            "strategic_model": "gpt-4o",
            "is_default": True,
        },
    ).json()
    project = client.post("/api/projects", json={"name": "Demo", "model_preset_id": preset["id"]}).json()
    source = client.post(
        f"/api/projects/{project['id']}/sources",
        json={"title": "PRD", "content": "# Hello", "is_prd": True},
    ).json()
    review = client.post(
        f"/api/projects/{project['id']}/reviews",
        json={"source_id": source["id"]},
    )
    assert review.status_code == 200
    llm_options = captured["llm_options"]
    assert llm_options["llm_kwargs"]["api_key"] == "sk-test"
    assert llm_options["llm_kwargs"]["base_url"] == "https://api.example.com/v1"
    assert "api_key" not in str(captured.get("audit_context", {}))


def test_redaction_helpers():
    payload = {
        "api_key": "sk-live",
        "Authorization": "Bearer abc",
        "client_secret": "hidden",
        "project_id": "project_1",
        "nested": {"access_token": "tok"},
    }
    redacted = redact_mapping(payload)
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["Authorization"] == "***REDACTED***"
    assert redacted["client_secret"] == "***REDACTED***"
    assert redacted["project_id"] == "project_1"
    assert redacted["nested"]["access_token"] == "***REDACTED***"
    assert is_sensitive_key("refresh_token")
    assert "Authorization=***REDACTED***" in redact_text("Authorization: Bearer sk-123")


def test_audit_and_job_snapshots_redact_secrets(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_job_snapshot(
        run_dir,
        {
            "run_id": "run-1",
            "status": "running",
            "config": {"api_key": "sk-test", "mode": "quick"},
        },
    )
    snapshot = json.loads((run_dir / "run_progress.json").read_text(encoding="utf-8"))
    assert snapshot["config"]["api_key"] == "***REDACTED***"
    assert snapshot["config"]["mode"] == "quick"

    append_audit_event(
        run_dir,
        operation="review_submission",
        status="accepted",
        client_metadata={"api_key": "sk-test", "project_id": "p1"},
        details={"Authorization": "Bearer abc"},
    )
    event = read_audit_events(run_dir)[0]
    assert event["client_metadata"]["api_key"] == "***REDACTED***"
    assert event["client_metadata"]["project_id"] == "p1"
    assert event["details"]["Authorization"] == "***REDACTED***"
