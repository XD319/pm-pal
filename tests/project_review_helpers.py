"""Helpers for project-scoped HTTP integration tests."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from pm_pal.server import app as app_module


def link_run_to_project(
    project_id: str,
    run_id: str,
    *,
    source_id: str = "",
    created_at: str | None = None,
) -> None:
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(app_module.PROJECT_SPACE_DB_PATH) as connection:
        connection.execute(
            "DELETE FROM project_runs WHERE run_id = ?",
            (run_id,),
        )
        connection.execute(
            "INSERT INTO project_runs (project_id, run_id, source_id, created_at) VALUES (?, ?, ?, ?)",
            (project_id, run_id, source_id, timestamp),
        )
        connection.commit()


def create_test_project(
    client: TestClient,
    *,
    name: str = "Test Project",
    headers: dict[str, str] | None = None,
) -> str:
    response = client.post(
        "/api/projects",
        json={"name": name, "description": ""},
        headers=headers or {},
    )
    response.raise_for_status()
    return str(response.json()["id"])


def clear_project_run(run_id: str) -> None:
    with sqlite3.connect(app_module.PROJECT_SPACE_DB_PATH) as connection:
        connection.execute("DELETE FROM project_runs WHERE run_id = ?", (run_id,))
        connection.commit()


def project_review_path(project_id: str, run_id: str, suffix: str = "") -> str:
    return f"/api/projects/{project_id}/reviews/{run_id}{suffix}"
