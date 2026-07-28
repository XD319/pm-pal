from __future__ import annotations

import json

from fastapi.testclient import TestClient

from prd_pal.server import app as app_module
from tests.project_review_helpers import (
    create_test_project,
    link_run_to_project,
    project_review_path,
)


def _build_client() -> TestClient:
    return TestClient(app_module.app)


def _write_feishu_run_fixture(
    tmp_path,
    run_id: str,
    *,
    submitter_open_id: str = "ou_owner",
    tenant_key: str = "tenant-a",
) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    report_payload = {
        "run_id": run_id,
        "mode": "quick",
        "review_mode": "quick",
        "trace": {"reviewer": {"status": "ok"}},
    }
    (run_dir / "report.md").write_text("# Review Report", encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "run_trace.json").write_text("{}", encoding="utf-8")
    (run_dir / "entry_context.json").write_text(
        json.dumps(
            {
                "source_origin": "feishu",
                "entry_mode": "plugin",
                "submitter_open_id": submitter_open_id,
                "tenant_key": tenant_key,
                "trigger_source": "feishu",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_get_review_result_allows_matching_feishu_context(tmp_path, monkeypatch):
    run_id = "20260409T120001Z"
    _write_feishu_run_fixture(tmp_path, run_id)
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = _build_client()
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(
        project_review_path(
            project_id,
            run_id,
            "/result?open_id=ou_owner&tenant_key=tenant-a",
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["result_page"] == {
        "path": f"/run/{run_id}?open_id=ou_owner&tenant_key=tenant-a&trigger_source=feishu&embed=feishu",
        "url": f"/run/{run_id}?open_id=ou_owner&tenant_key=tenant-a&trigger_source=feishu&embed=feishu",
    }


def test_api_key_auth_allows_project_review_with_valid_feishu_context_only(
    tmp_path, monkeypatch
):
    run_id = "20260409T120004Z"
    _write_feishu_run_fixture(tmp_path, run_id)
    monkeypatch.setenv("MARRDP_API_AUTH_DISABLED", "false")
    monkeypatch.setenv("MARRDP_API_KEY", "admin-key")
    monkeypatch.setenv("MARRDP_API_RATE_LIMIT_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = _build_client()
    project_id = create_test_project(client, headers={"X-API-Key": "admin-key"})
    link_run_to_project(project_id, run_id)
    run_response = client.get(
        project_review_path(
            project_id,
            run_id,
            "/result?open_id=ou_owner&tenant_key=tenant-a&embed=feishu",
        )
    )
    projects_response = client.get("/api/templates")

    assert run_response.status_code == 200
    assert run_response.json()["run_id"] == run_id
    assert projects_response.status_code == 401
    assert projects_response.json()["detail"]["code"] == "authentication_required"


def test_get_review_result_allows_project_route_without_feishu_acl(tmp_path, monkeypatch):
    run_id = "20260409T120002Z"
    _write_feishu_run_fixture(tmp_path, run_id)
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = _build_client()
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(
        project_review_path(
            project_id,
            run_id,
            "/result?open_id=ou_other&tenant_key=tenant-a",
        )
    )

    # Project-scoped routes rely on project membership; Feishu run ACL is enforced at redirect/H5 layer :-)
    assert response.status_code == 200
    assert response.json()["run_id"] == run_id


def test_get_review_status_for_web_run_does_not_force_feishu_result_page(
    tmp_path, monkeypatch
):
    run_id = "20260409T120003Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# Review Report", encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": "quick",
                "review_mode": "quick",
                "trace": {"reviewer": {"status": "ok"}},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "run_trace.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = _build_client()
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(
        project_review_path(
            project_id,
            run_id,
            "?open_id=ou_fake&tenant_key=tenant-fake",
        )
    )
    assert response.status_code == 200
    assert response.json()["result_page"] == {
        "path": f"/run/{run_id}",
        "url": f"/run/{run_id}",
    }
