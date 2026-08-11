from __future__ import annotations

import json

from fastapi.testclient import TestClient

from pm_pal.server import app as app_module
from tests.project_review_helpers import (
    create_test_project,
    link_run_to_project,
    project_review_path,
)


def test_get_review_result_returns_parsed_report_and_gating_metadata(
    tmp_path, monkeypatch
):
    run_id = "20260309T010203Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    report_payload = {
        "run_id": run_id,
        "schema_version": "v1.1",
        "mode": "full",
        "review_mode": "full",
        "gating": {
            "selected_mode": "full",
            "reasons": [
                "cross_system_hits=2 indicates external or multi-system coordination"
            ],
            "skipped": False,
        },
        "reviewers_used": ["product", "engineering", "qa"],
        "reviewers_skipped": [
            {
                "reviewer": "security",
                "reason": "no security-sensitive scope was detected",
            }
        ],
        "trace": {"reviewer": {"status": "ok"}},
    }
    (run_dir / "report.md").write_text("# Review Report", encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "run_trace.json").write_text("{}", encoding="utf-8")
    (run_dir / "review_report.json").write_text("{}", encoding="utf-8")
    (run_dir / "risk_items.json").write_text("{}", encoding="utf-8")
    (run_dir / "open_questions.json").write_text("{}", encoding="utf-8")
    (run_dir / "review_summary.md").write_text("# Review Summary", encoding="utf-8")

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(project_review_path(project_id, run_id, "/result"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["status"] == "completed"
    assert payload["mode"] == "full"
    assert payload["gating"]["selected_mode"] == "full"
    assert payload["reviewers_used"] == ["product", "engineering", "qa"]
    assert payload["reviewers_skipped"][0]["reviewer"] == "security"
    assert payload["result"] == report_payload
    app_module._jobs.clear()


def test_get_review_result_returns_completed_when_reporter_succeeds_after_degraded_steps(
    tmp_path, monkeypatch
):
    run_id = "20260309T010206Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    report_payload = {
        "run_id": run_id,
        "schema_version": "v1.1",
        "trace": {
            "planner": {
                "status": "error",
                "error_message": "parsed_items is empty - nothing to plan",
            },
            "risk": {
                "status": "error",
                "error_message": "structured_requirements is empty - nothing to assess",
            },
            "reviewer": {
                "status": "error",
                "error_message": "parsed_items is empty - nothing to review",
            },
            "reporter": {"status": "ok"},
            "pack_builder": {"status": "ok", "non_blocking": True},
        },
    }
    (run_dir / "report.md").write_text("# Review Report", encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "run_trace.json").write_text(
        json.dumps(report_payload["trace"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(project_review_path(project_id, run_id, "/result"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["result"] == report_payload
    app_module._jobs.clear()


def test_get_review_result_returns_404_for_missing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    response = client.get(
        project_review_path(project_id, "20260309T010204Z", "/result")
    )

    assert response.status_code == 404
    app_module._jobs.clear()


def test_get_review_result_returns_409_when_report_is_not_ready(tmp_path, monkeypatch):
    run_id = "20260309T010205Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()
    job = app_module.JobRecord(
        run_id=run_id, run_dir=run_dir, status="running", current_node="planner"
    )
    job.node_progress["planner"]["status"] = "running"
    app_module._jobs[run_id] = job

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(project_review_path(project_id, run_id, "/result"))

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "result_not_ready"
    assert detail["message"] == f"report.json not ready for run_id={run_id}"
    assert detail["run_id"] == run_id
    assert detail["status"] == "running"
    assert detail["progress"]["current_node"] == "planner"
    app_module._jobs.clear()
