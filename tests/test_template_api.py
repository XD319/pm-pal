from __future__ import annotations

from fastapi.testclient import TestClient

from pm_pal.server.app import app


client = TestClient(app)


def test_list_templates_endpoint_returns_registry_records() -> None:
    response = client.get("/api/templates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] >= 1
    assert payload["templates"]
    assert {
        "template_id",
        "template_type",
        "version",
        "description",
        "is_default",
        "status",
    } <= set(payload["templates"][0])


def test_list_templates_by_type_endpoint_filters_by_template_type() -> None:
    response = client.get("/api/templates/adapter_prompt")

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_type"] == "adapter_prompt"
    assert payload["count"] == len(payload["templates"])
    assert payload["count"] >= 1
    assert {item["template_type"] for item in payload["templates"]} == {
        "adapter_prompt"
    }


def test_list_templates_endpoint_supports_version_filter() -> None:
    response = client.get("/api/templates", params={"version": "handoff_markdown_v1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert {item["version"] for item in payload["templates"]} == {"handoff_markdown_v1"}


def test_list_templates_by_type_returns_404_for_unknown_type() -> None:
    response = client.get("/api/templates/unknown_type")

    assert response.status_code == 404
    assert "unknown template_type" in response.json()["detail"]
