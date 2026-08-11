from __future__ import annotations

import pytest

from pm_pal.server import app as app_module
from pm_pal.server.job_state import (
    ReviewCreateRequest,
    resolve_review_inputs,
    resolve_runtime_llm_options,
)


@pytest.mark.asyncio
async def test_enqueue_review_keeps_legacy_prd_path_compatible(tmp_path, monkeypatch):
    prd_file = tmp_path / "legacy_prd.md"
    prd_file.write_text("# Legacy PRD", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_job(
        job,
        *,
        prd_text=None,
        prd_path=None,
        source=None,
        mode=None,
        llm_options=None,
        audit_context=None,
    ):
        captured["prd_text"] = prd_text
        captured["prd_path"] = prd_path
        captured["source"] = source
        captured["mode"] = mode
        captured["llm_options"] = llm_options
        captured["audit_context"] = audit_context
        job.status = "completed"

    monkeypatch.setattr(app_module, "_run_job", fake_run_job)
    monkeypatch.setattr(app_module, "make_run_id", lambda: "20260308T020301Z")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    review_inputs = resolve_review_inputs(ReviewCreateRequest(prd_path=str(prd_file)))
    result = await app_module._enqueue_review_run(
        **review_inputs,
        audit_context={
            "source": "web",
            "tool_name": "web.review.submit",
            "actor": "web",
            "client_metadata": {},
        },
    )
    job = app_module._jobs[result["run_id"]]
    await job.task

    assert result["run_id"] == "20260308T020301Z"
    assert captured["prd_text"] is None
    assert captured["prd_path"] == str(prd_file.resolve())
    assert captured["source"] is None
    assert captured["mode"] is None
    assert captured["llm_options"] in ({}, None)
    assert captured["audit_context"]["source"] == "web"
    app_module._jobs.clear()


@pytest.mark.asyncio
async def test_enqueue_review_prioritizes_source_over_legacy_fields(
    tmp_path, monkeypatch
):
    source_file = tmp_path / "source_prd.md"
    source_file.write_text("# Source PRD", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_run_job(
        job,
        *,
        prd_text=None,
        prd_path=None,
        source=None,
        mode=None,
        llm_options=None,
        audit_context=None,
    ):
        captured["prd_text"] = prd_text
        captured["prd_path"] = prd_path
        captured["source"] = source
        captured["mode"] = mode
        captured["llm_options"] = llm_options
        captured["audit_context"] = audit_context
        job.status = "completed"

    monkeypatch.setattr(app_module, "_run_job", fake_run_job)
    monkeypatch.setattr(app_module, "make_run_id", lambda: "20260308T020302Z")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    payload = ReviewCreateRequest(
        source=str(source_file),
        prd_text="ignored text",
        prd_path=str(source_file),
    )
    review_inputs = resolve_review_inputs(payload)
    result = await app_module._enqueue_review_run(
        **review_inputs,
        audit_context={
            "source": "web",
            "tool_name": "web.review.submit",
            "actor": "web",
            "client_metadata": {},
        },
    )
    job = app_module._jobs[result["run_id"]]
    await job.task

    assert result["run_id"] == "20260308T020302Z"
    assert captured["prd_text"] is None
    assert captured["prd_path"] is None
    assert captured["source"] == str(source_file)
    assert captured["mode"] is None
    assert captured["llm_options"] in ({}, None)
    assert captured["audit_context"]["source"] == "web"
    app_module._jobs.clear()


@pytest.mark.asyncio
async def test_enqueue_review_forwards_runtime_llm_options(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run_job(
        job,
        *,
        prd_text=None,
        prd_path=None,
        source=None,
        mode=None,
        llm_options=None,
        audit_context=None,
    ):
        captured["prd_text"] = prd_text
        captured["prd_path"] = prd_path
        captured["source"] = source
        captured["mode"] = mode
        captured["llm_options"] = llm_options
        captured["audit_context"] = audit_context
        job.status = "completed"

    monkeypatch.setattr(app_module, "_run_job", fake_run_job)
    monkeypatch.setattr(app_module, "make_run_id", lambda: "20260308T020304Z")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    payload = ReviewCreateRequest(
        prd_text="# Test PRD",
        llm="deepseek:deepseek-chat",
        temperature=0.1,
        reasoning_effort="low",
        llm_kwargs={"max_retries": 1},
    )
    review_inputs = resolve_review_inputs(payload)
    llm_options = resolve_runtime_llm_options(payload)
    result = await app_module._enqueue_review_run(
        **review_inputs,
        llm_options=llm_options,
        audit_context={
            "source": "web",
            "tool_name": "web.review.submit",
            "actor": "web",
            "client_metadata": {},
        },
    )
    job = app_module._jobs[result["run_id"]]
    await job.task

    assert result["run_id"] == "20260308T020304Z"
    assert captured["prd_text"] == "# Test PRD"
    assert captured["llm_options"] == {
        "llm": "deepseek:deepseek-chat",
        "temperature": 0.1,
        "reasoning_effort": "low",
        "llm_kwargs": {"max_retries": 1},
    }
    assert captured["audit_context"]["tool_name"] == "web.review.submit"
    app_module._jobs.clear()


@pytest.mark.asyncio
async def test_project_get_review_status_keeps_report_paths_for_completed_run(
    tmp_path, monkeypatch
):
    run_id = "20260308T020303Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "report.md").write_text("# Report", encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    (run_dir / "run_trace.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    result = await app_module._project_get_review_status(run_id)

    assert result["run_id"] == run_id
    assert result["status"] == "completed"
    assert result["report_paths"] == {
        "report_md": str(run_dir / "report.md"),
        "report_json": str(run_dir / "report.json"),
        "run_trace": str(run_dir / "run_trace.json"),
    }
    app_module._jobs.clear()


def test_feishu_submit_returns_gone_for_legacy_path(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    response = client.post(
        "/api/feishu/submit",
        json={
            "source": "feishu://docx/doc-token",
            "mode": "quick",
            "open_id": "ou_test_user",
            "tenant_key": "tenant-test",
        },
    )

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_endpoint_removed"
    app_module._jobs.clear()


def test_feishu_submit_rejects_invalid_payload(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    response = client.post("/api/feishu/submit", json={"mode": "quick"})

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_endpoint_removed"
