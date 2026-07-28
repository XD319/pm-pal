from __future__ import annotations

import csv
import io
import json

from fastapi.testclient import TestClient

from prd_pal.server import app as app_module
from tests.project_review_helpers import (
    create_test_project,
    link_run_to_project,
    project_review_path,
)


def test_get_report_html_returns_browser_openable_document(tmp_path, monkeypatch):
    run_id = "20260326T080000Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    report_payload = {
        "run_id": run_id,
        "review_mode": "full",
        "reviewers_used": ["product", "engineering", "qa"],
        "source_metadata": {"title": "Campus Recruitment PRD"},
        "updated_at": "2026-03-26T08:30:00+00:00",
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(
        "# Requirement Review Report\n\n## Findings\n\n- Export path is missing ownership.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(
        project_review_path(project_id, run_id, "/report?format=html")
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'inline; filename="report.html"' == response.headers["content-disposition"]
    assert "<!DOCTYPE html>" in response.text
    assert "Campus Recruitment PRD" in response.text
    assert "2026-03-26 08:30:00 UTC" in response.text
    assert "product, engineering, qa" in response.text
    assert "<h2>Findings</h2>" in response.text


def test_get_report_csv_returns_excel_friendly_findings_table(tmp_path, monkeypatch):
    run_id = "20260326T081500Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    report_payload = {
        "run_id": run_id,
        "parallel_review": {
            "findings": [
                {
                    "finding_id": "finding-001",
                    "requirement_id": "REQ-002",
                    "severity": "high",
                    "category": "security",
                    "description": "Export lacks an approval gate.",
                    "suggested_action": "Require explicit manager approval before export.",
                    "reviewers": ["security"],
                }
            ]
        },
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(
        "# Requirement Review Report\n", encoding="utf-8"
    )

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.get(
        project_review_path(project_id, run_id, "/report?format=csv")
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        'attachment; filename="report.csv"' == response.headers["content-disposition"]
    )

    decoded = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(decoded)))
    assert rows == [
        {
            "id": "finding-001",
            "requirement": "REQ-002",
            "severity": "high",
            "category": "security",
            "description": "Export lacks an approval gate.",
            "suggestion": "Require explicit manager approval before export.",
            "reviewer": "security",
        }
    ]
