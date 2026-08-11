"""Minimal smoke validation for review-engine CLI and project-scoped FastAPI flows.

Usage:
    python eval/smoke_review_engine.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pm_pal.main as cli_module
from pm_pal.server import app as app_module
from tests.project_review_helpers import clear_project_run


def _run_cli_smoke(workspace: Path) -> dict[str, object]:
    input_path = workspace / "smoke_prd.md"
    input_path.write_text(
        "# Smoke PRD\n\n- Requirement: generate a review report.\n", encoding="utf-8"
    )

    run_dir = workspace / "cli-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = SimpleNamespace(
        run_id="20260310T110000Z",
        report_md_path=str(run_dir / "report.md"),
        report_json_path=str(run_dir / "report.json"),
        run_trace_path=str(run_dir / "run_trace.json"),
        implementation_pack_path="",
        test_pack_path="",
        execution_pack_path="",
        prd_v1_path="",
        task_bundle_v1_path="",
        delivery_bundle_path="",
        high_risk_ratio=0.0,
        coverage_ratio=1.0,
        revision_round=1,
        status="completed",
    )
    Path(summary.report_md_path).write_text("# Smoke Report", encoding="utf-8")
    Path(summary.report_json_path).write_text(
        json.dumps({"status": "completed"}, ensure_ascii=False), encoding="utf-8"
    )
    Path(summary.run_trace_path).write_text(
        json.dumps({"reporter": {"status": "ok"}}, ensure_ascii=False), encoding="utf-8"
    )

    original_review = cli_module.review_prd_text_async
    buffer = io.StringIO()
    try:
        cli_module.review_prd_text_async = AsyncMock(return_value=summary)
        with redirect_stdout(buffer):
            exit_code = cli_module.run_cli(["review", "--input", str(input_path)])
    finally:
        cli_module.review_prd_text_async = original_review

    output = buffer.getvalue()
    return {
        "passed": exit_code == 0
        and all(token in output for token in ("Report :", "State  :", "Trace  :")),
        "exit_code": exit_code,
        "output": output.strip(),
        "artifacts": {
            "report_md": summary.report_md_path,
            "report_json": summary.report_json_path,
            "run_trace": summary.run_trace_path,
        },
    }


def _run_fastapi_smoke(workspace: Path) -> dict[str, object]:
    outputs_root = workspace / "fastapi-outputs"
    outputs_root.mkdir(parents=True, exist_ok=True)
    run_id = "20260310T120000Z"

    async def fake_run_job(job, **_kwargs):
        report_dir = outputs_root / job.run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        report_payload = {
            "run_id": job.run_id,
            "status": "completed",
            "trace": {"reporter": {"status": "ok"}},
            "metrics": {"coverage_ratio": 1.0},
        }
        (report_dir / "report.md").write_text("# Smoke Report", encoding="utf-8")
        (report_dir / "report.json").write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (report_dir / "run_trace.json").write_text(
            json.dumps(report_payload["trace"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        job.status = "completed"
        job.report_paths = {
            "report_md": str(report_dir / "report.md"),
            "report_json": str(report_dir / "report.json"),
            "run_trace": str(report_dir / "run_trace.json"),
        }

    original_outputs = app_module.OUTPUTS_ROOT
    original_make_run_id = app_module.make_run_id
    original_run_job = app_module._run_job
    original_auth_disabled = os.environ.get("MARRDP_API_AUTH_DISABLED")
    original_pm_auth = os.environ.get("PM_PAL_API_AUTH_DISABLED")
    original_rate_disabled = os.environ.get("MARRDP_API_RATE_LIMIT_DISABLED")
    original_pm_rate = os.environ.get("PM_PAL_API_RATE_LIMIT_DISABLED")
    app_module._jobs.clear()
    app_module._reset_submission_rate_limits()
    clear_project_run(run_id)

    create_response = status_response = result_response = lookup_response = None
    project_id = ""
    try:
        os.environ["MARRDP_API_AUTH_DISABLED"] = "true"
        os.environ["PM_PAL_API_AUTH_DISABLED"] = "true"
        os.environ["MARRDP_API_RATE_LIMIT_DISABLED"] = "true"
        os.environ["PM_PAL_API_RATE_LIMIT_DISABLED"] = "true"
        app_module.OUTPUTS_ROOT = outputs_root
        app_module.make_run_id = lambda: run_id
        app_module._run_job = fake_run_job

        client = TestClient(app_module.app)
        project = client.post(
            "/api/projects", json={"name": "Smoke Project", "description": ""}
        )
        project.raise_for_status()
        project_id = str(project.json()["id"])

        source = client.post(
            f"/api/projects/{project_id}/sources",
            json={
                "title": "Smoke PRD",
                "source_type": "prd_text",
                "content": "# Smoke review",
                "is_prd": True,
            },
        )
        source.raise_for_status()
        source_id = str(source.json()["id"])

        create_response = client.post(
            f"/api/projects/{project_id}/reviews",
            json={"source_id": source_id, "mode": "quick"},
        )
        time.sleep(0.05)
        status_response = client.get(f"/api/projects/{project_id}/reviews/{run_id}")
        result_response = client.get(
            f"/api/projects/{project_id}/reviews/{run_id}/result"
        )
        lookup_response = client.get(f"/api/projects/by-run/{run_id}")
    finally:
        if original_auth_disabled is None:
            os.environ.pop("MARRDP_API_AUTH_DISABLED", None)
        else:
            os.environ["MARRDP_API_AUTH_DISABLED"] = original_auth_disabled
        if original_pm_auth is None:
            os.environ.pop("PM_PAL_API_AUTH_DISABLED", None)
        else:
            os.environ["PM_PAL_API_AUTH_DISABLED"] = original_pm_auth
        if original_rate_disabled is None:
            os.environ.pop("MARRDP_API_RATE_LIMIT_DISABLED", None)
        else:
            os.environ["MARRDP_API_RATE_LIMIT_DISABLED"] = original_rate_disabled
        if original_pm_rate is None:
            os.environ.pop("PM_PAL_API_RATE_LIMIT_DISABLED", None)
        else:
            os.environ["PM_PAL_API_RATE_LIMIT_DISABLED"] = original_pm_rate
        app_module.OUTPUTS_ROOT = original_outputs
        app_module.make_run_id = original_make_run_id
        app_module._run_job = original_run_job
        app_module._jobs.clear()
        app_module._reset_submission_rate_limits()
        clear_project_run(run_id)

    create_body = create_response.json() if create_response is not None else {}
    status_body = status_response.json() if status_response is not None else {}
    result_body = result_response.json() if result_response is not None else {}
    lookup_body = lookup_response.json() if lookup_response is not None else {}

    passed = (
        create_response is not None
        and status_response is not None
        and result_response is not None
        and lookup_response is not None
        and create_response.status_code == 200
        and status_response.status_code == 200
        and result_response.status_code == 200
        and lookup_response.status_code == 200
        and create_body.get("run_id") == run_id
        and status_body.get("status") == "completed"
        and result_body.get("status") == "completed"
        and lookup_body.get("project_id") == project_id
    )
    return {
        "passed": passed,
        "project_id": project_id,
        "create": create_body,
        "status": status_body,
        "result": result_body,
        "lookup": lookup_body,
    }


def main() -> None:
    out_path = PROJECT_ROOT / "eval" / "smoke_report.json"
    with TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir)
        cli_summary = _run_cli_smoke(workspace)
        api_summary = _run_fastapi_smoke(workspace)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": "review-engine-only",
        "checks": {
            "cli": cli_summary,
            "fastapi": api_summary,
        },
        "passed": bool(cli_summary["passed"] and api_summary["passed"]),
    }
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
