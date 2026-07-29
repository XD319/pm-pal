from __future__ import annotations

import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from prd_pal.integrations.feishu.config_store import FeishuConnectorConfig

from prd_pal.server import app as app_module
from tests.project_review_helpers import (
    clear_project_run,
    create_test_project,
    link_run_to_project,
    project_review_path,
)


def _build_client() -> TestClient:
    return TestClient(app_module.app)


def _reset_state() -> None:
    app_module._jobs.clear()
    app_module._reset_submission_rate_limits()


def test_create_project_review_accepts_authorized_bearer_request(
    tmp_path, monkeypatch
):
    run_id = "20260728T020301Z"
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_BEARER_TOKEN", "shared-bearer-token")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    monkeypatch.setattr(app_module, "make_run_id", lambda: run_id)
    monkeypatch.setattr(app_module, "_run_job", AsyncMock(return_value=None))
    clear_project_run(run_id)
    _reset_state()

    client = _build_client()
    auth_headers = {"Authorization": "Bearer shared-bearer-token"}
    project_id = create_test_project(client, headers=auth_headers)
    source = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "# Shared review", "is_prd": True},
        headers=auth_headers,
    ).json()
    response = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"source_id": source["id"]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    assert run_id in app_module._jobs
    _reset_state()


def test_create_project_review_rejects_unauthorized_request(tmp_path, monkeypatch):
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_KEY", "shared-api-key")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    monkeypatch.setattr(app_module, "_run_job", AsyncMock(return_value=None))
    _reset_state()

    client = _build_client()
    auth_headers = {"X-API-Key": "shared-api-key"}
    project_id = create_test_project(client, headers=auth_headers)
    source = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "# Shared review", "is_prd": True},
        headers=auth_headers,
    ).json()
    response = client.post(
        f"/api/projects/{project_id}/reviews",
        json={"source_id": source["id"]},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "authentication_required",
        "message": "Provide a valid X-API-Key header or Authorization: Bearer token.",
    }
    assert app_module._jobs == {}
    _reset_state()


def test_create_project_review_enforces_rate_limit_for_submission_endpoint(
    tmp_path, monkeypatch
):
    first_run_id = "20260728T020302Z"
    second_run_id = "20260728T020303Z"
    run_ids = iter([first_run_id, second_run_id])
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_KEY", "shared-api-key")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_WINDOW_SEC", "60")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    monkeypatch.setattr(app_module, "make_run_id", lambda: next(run_ids))
    monkeypatch.setattr(app_module, "_run_job", AsyncMock(return_value=None))
    clear_project_run(first_run_id)
    clear_project_run(second_run_id)
    _reset_state()

    client = _build_client()
    auth_headers = {"X-API-Key": "shared-api-key"}
    project_id = create_test_project(client, headers=auth_headers)
    source = client.post(
        f"/api/projects/{project_id}/sources",
        json={"title": "PRD", "content": "# Shared review", "is_prd": True},
        headers=auth_headers,
    ).json()
    payload = {"source_id": source["id"]}
    first = client.post(
        f"/api/projects/{project_id}/reviews",
        json=payload,
        headers=auth_headers,
    )
    second = client.post(
        f"/api/projects/{project_id}/reviews",
        json=payload,
        headers=auth_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"]
    assert second.json()["detail"]["code"] == "rate_limit_exceeded"
    assert second.json()["detail"]["limit"] == 1
    assert second.json()["detail"]["window_sec"] == 60
    _reset_state()


def test_feishu_events_challenge_returns_challenge(monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    _reset_state()

    client = _build_client()
    response = client.post(
        "/api/feishu/events",
        json={"type": "url_verification", "challenge": "challenge-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}


def test_feishu_events_rejects_missing_signature_secret_when_enabled(monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "false")
    monkeypatch.delenv("MARRDP_FEISHU_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        app_module._feishu_config_store,
        "get",
        lambda project_id: FeishuConnectorConfig(project_id=project_id),
    )
    _reset_state()

    client = _build_client()
    response = client.post(
        "/api/feishu/events",
        json={"type": "url_verification", "challenge": "challenge-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "feishu_signature_not_configured",
        "message": "Feishu signature verification is enabled but MARRDP_FEISHU_WEBHOOK_SECRET is not configured.",
    }


def test_feishu_events_reject_invalid_signature(monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_FEISHU_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC", "300")
    _reset_state()

    client = _build_client()
    response = client.post(
        "/api/feishu/events",
        json={"type": "url_verification", "challenge": "challenge-token"},
        headers={
            "X-Lark-Request-Timestamp": "1710000000",
            "X-Lark-Signature": "invalid-signature",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_feishu_signature"


def test_feishu_events_accepts_valid_signature(monkeypatch):
    import time

    from prd_pal.integrations.feishu.security import build_feishu_signature

    secret = "test-secret"
    timestamp = str(int(time.time()))
    body = json.dumps(
        {"type": "url_verification", "challenge": "challenge-token"}
    ).encode("utf-8")
    signature = build_feishu_signature(secret=secret, timestamp=timestamp, body=body)
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "false")
    monkeypatch.setenv("MARRDP_FEISHU_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC", "300")
    _reset_state()

    client = _build_client()
    response = client.post(
        "/api/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}
