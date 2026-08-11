from __future__ import annotations

from fastapi.testclient import TestClient

from pm_pal.server import app as app_module


def test_health_endpoint_returns_healthy_payload() -> None:
    with TestClient(app_module.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["service"] == "pm-pal"
    assert payload["deployment"]["mode"] == "single_instance"
    assert "connector_worker" in payload


def test_ready_endpoint_checks_startup_and_data_roots(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path / "outputs")
    monkeypatch.setattr(app_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(
        app_module, "PROJECT_SPACE_DB_PATH", tmp_path / "project_space.sqlite3"
    )
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)

    with TestClient(app_module.app) as client:
        response = client.get("/ready")

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["service"] == "pm-pal"
    assert payload["checks"]["startup_completed"] is True
    assert payload["checks"]["outputs_root_writable"] is True
    assert payload["checks"]["data_root_writable"] is True
    assert payload["checks"]["project_space_db_available"] is True
    assert payload["checks"]["connector_sync_worker_alive"] is True
