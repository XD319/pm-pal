from __future__ import annotations

import json

from fastapi.testclient import TestClient

from pm_pal.server import app as app_module
from tests.project_review_helpers import (
    create_test_project,
    link_run_to_project,
    project_review_path,
)


def _write_progress_snapshot(run_dir, run_id: str, *, status: str = "running") -> None:
    payload = {
        "run_id": run_id,
        "status": status,
        "created_at": "2026-03-11T14:41:49+00:00",
        "updated_at": "2026-03-11T14:43:44+00:00",
        "progress": {
            "percent": 60,
            "current_node": "delivery_planning",
            "nodes": {
                "parser": {
                    "status": "completed",
                    "runs": 1,
                    "last_start": "2026-03-11T14:41:49+00:00",
                    "last_end": "2026-03-11T14:42:49+00:00",
                },
                "delivery_planning": {
                    "status": "running",
                    "runs": 1,
                    "last_start": "2026-03-11T14:43:44+00:00",
                },
            },
            "updated_at": "2026-03-11T14:43:44+00:00",
            "error": "",
        },
        "report_paths": {
            "run_trace": str(run_dir / "run_trace.json"),
        },
    }
    (run_dir / app_module.RUN_PROGRESS_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_get_review_status_marks_orphaned_running_snapshot_as_failed(
    tmp_path, monkeypatch
):
    run_id = "20260311T144149Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    _write_progress_snapshot(run_dir, run_id)

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(project_review_path(project_id, run_id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error"]["code"] == "run_interrupted"
    assert payload["progress"]["current_node"] == "delivery_planning"
    assert payload["progress"]["nodes"]["delivery_planning"]["status"] == "running"
    assert "no longer active" in payload["progress"]["error"]
    app_module._jobs.clear()


def test_get_review_result_uses_persisted_failure_when_run_was_interrupted(
    tmp_path, monkeypatch
):
    run_id = "20260311T144149Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    _write_progress_snapshot(run_dir, run_id)

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(project_review_path(project_id, run_id, "/result"))

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "failed"
    assert detail["code"] == "run_interrupted"
    assert detail["progress"]["current_node"] == "delivery_planning"
    assert "no longer active" in detail["message"]
    app_module._jobs.clear()


def test_get_review_status_counts_failed_stages_toward_progress_percent(
    tmp_path, monkeypatch
):
    run_id = "20260311T144150Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    payload = {
        "run_id": run_id,
        "status": "running",
        "created_at": "2026-03-11T14:41:49+00:00",
        "updated_at": "2026-03-11T14:43:44+00:00",
        "progress": {
            "percent": 0,
            "current_node": "delivery_planning",
            "nodes": {
                "parser": {"status": "completed", "runs": 1},
                "parallel_start": {"status": "completed", "runs": 1},
                "planner": {"status": "failed", "runs": 1},
                "risk": {"status": "failed", "runs": 1},
                "review_join": {"status": "completed", "runs": 1},
                "delivery_planning": {"status": "running", "runs": 1},
            },
            "updated_at": "2026-03-11T14:43:44+00:00",
            "error": "",
        },
        "report_paths": {
            "run_trace": str(run_dir / "run_trace.json"),
        },
    }
    (run_dir / app_module.RUN_PROGRESS_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(project_review_path(project_id, run_id))

    assert response.status_code == 200
    assert response.json()["progress"]["percent"] == 55
    app_module._jobs.clear()


def test_app_lifespan_recovers_persisted_jobs_into_registry(tmp_path, monkeypatch):
    run_id = "20260311T144151Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    _write_progress_snapshot(run_dir, run_id)

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    with TestClient(app_module.app):
        assert run_id in app_module._jobs
        assert app_module._jobs[run_id].status == "failed"
        assert "no longer active" in app_module._jobs[run_id].error

    app_module._jobs.clear()
