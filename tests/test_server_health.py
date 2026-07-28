from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prd_pal.server import app as app_module


@pytest.fixture(autouse=True)
def _noop_product_decision_scheduler_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop_stop() -> None:
        return None

    monkeypatch.setattr(app_module._product_decision_scheduler, "stop", _noop_stop)


def test_health_endpoint_returns_healthy_payload() -> None:
    with TestClient(app_module.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["service"] == "prd-pal"


def test_ready_endpoint_checks_startup_and_outputs_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)

    with TestClient(app_module.app) as client:
        response = client.get("/ready")

    payload = response.json()
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["service"] == "prd-pal"
    assert payload["checks"]["startup_completed"] is True
    assert payload["checks"]["outputs_root_writable"] is True
