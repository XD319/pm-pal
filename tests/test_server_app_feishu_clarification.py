from __future__ import annotations

import json

from fastapi.testclient import TestClient

from pm_pal.monitoring import read_audit_events
from pm_pal.server import app as app_module


def _build_client() -> TestClient:
    return TestClient(app_module.app)


def _write_run_payloads(
    tmp_path,
    run_id: str,
    *,
    clarification: dict,
    findings: list[dict],
    reviewer_summaries: list[dict],
) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    parallel_review = {
        "review_mode": "full",
        "findings": findings,
        "reviewer_summaries": reviewer_summaries,
        "clarification": clarification,
        "artifacts": {
            "review_result_json": str(run_dir / "review_result.json"),
            "review_report_json": str(run_dir / "review_report.json"),
            "review_report_md": str(run_dir / "review_report.md"),
            "review_summary_md": str(run_dir / "review_summary.md"),
        },
    }
    report_payload = {
        "run_id": run_id,
        "mode": "full",
        "review_mode": "full",
        "gating": {"selected_mode": "full", "reasons": [], "skipped": False},
        "parallel_review": parallel_review,
        "clarification": dict(clarification),
        "review_clarification": dict(clarification),
        "parallel_review_meta": {
            "selected_mode": "full",
            "review_mode": "full",
            "gating": {"selected_mode": "full", "reasons": [], "skipped": False},
            "artifact_paths": {
                "review_result_json": str(run_dir / "review_result.json")
            },
        },
        "trace": {"reviewer": {"status": "ok"}},
    }

    for name, content in {
        "report.json": report_payload,
        "review_result.json": parallel_review,
        "review_report.json": parallel_review,
    }.items():
        (run_dir / name).write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (run_dir / "report.md").write_text("# Review Report", encoding="utf-8")
    (run_dir / "run_trace.json").write_text("{}", encoding="utf-8")
    (run_dir / "review_report.md").write_text("# Review Report", encoding="utf-8")
    (run_dir / "review_summary.md").write_text("# Review Summary", encoding="utf-8")


def test_feishu_clarification_updates_and_returns_navigation(tmp_path, monkeypatch):
    run_id = "20260309T020206Z"
    _write_run_payloads(
        tmp_path,
        run_id,
        clarification={
            "triggered": True,
            "status": "pending",
            "questions": [
                {
                    "id": "clarify-123",
                    "question": "What measurable user outcome defines success?",
                    "reviewer": "product",
                    "ambiguity_type": "unanswerable",
                    "finding_ids": ["finding-clarify-1"],
                }
            ],
            "answers_applied": [],
            "findings_updated": [],
        },
        findings=[
            {
                "finding_id": "finding-clarify-1",
                "title": "Missing success metric",
                "detail": "The requirement does not define success.",
                "description": "The requirement does not define success.",
                "severity": "high",
                "category": "scope",
                "source_reviewer": "product",
                "reviewers": ["product"],
                "ambiguity_type": "unanswerable",
                "clarification_applied": False,
                "original_severity": "",
                "user_clarification": "",
            }
        ],
        reviewer_summaries=[
            {
                "reviewer": "product",
                "summary": "Need clarification before approval.",
                "status": "completed",
                "ambiguity_type": "missing_product_goal",
                "clarification_question": "What measurable user outcome defines success?",
                "notes": [],
            }
        ],
    )

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification",
        json={
            "run_id": run_id,
            "question_id": "clarify-123",
            "answer": "Success means recruiters can finish login and reach the dashboard within 30 seconds.",
            "open_id": "ou_test_user",
            "tenant_key": "tenant-test",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["clarification_status"] == "answered"
    assert payload["has_pending_questions"] is False
    assert payload["result_page"] == {
        "path": f"/run/{run_id}?trigger_source=feishu&open_id=ou_test_user&tenant_key=tenant-test&embed=feishu",
        "url": f"/run/{run_id}?trigger_source=feishu&open_id=ou_test_user&tenant_key=tenant-test&embed=feishu",
    }
    assert (
        payload["clarification"]["answers_applied"][0]["question_id"] == "clarify-123"
    )
    audit_events = read_audit_events(tmp_path / run_id)
    clarification_event = next(
        event for event in audit_events if event["operation"] == "clarification_answer"
    )
    assert clarification_event["actor"] == "ou_test_user"
    assert clarification_event["source"] == "feishu"
    assert clarification_event["details"]["question_ids"] == ["clarify-123"]


def test_feishu_clarification_returns_run_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification",
        json={
            "run_id": "20260309T020207Z",
            "question_id": "clarify-404",
            "answer": "answer",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"


def test_feishu_clarification_returns_controlled_error_when_gate_not_enabled(
    tmp_path, monkeypatch
):
    run_id = "20260309T020208Z"
    _write_run_payloads(
        tmp_path,
        run_id,
        clarification={
            "triggered": False,
            "status": "not_needed",
            "questions": [],
            "answers_applied": [],
            "findings_updated": [],
        },
        findings=[],
        reviewer_summaries=[],
    )

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification",
        json={
            "run_id": run_id,
            "question_id": "clarify-123",
            "answer": "answer",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "clarification_unavailable"


def test_feishu_clarification_accepts_repeat_answer_idempotently(tmp_path, monkeypatch):
    run_id = "20260309T020209Z"
    _write_run_payloads(
        tmp_path,
        run_id,
        clarification={
            "triggered": True,
            "status": "answered",
            "questions": [
                {
                    "id": "clarify-123",
                    "question": "What measurable user outcome defines success?",
                    "reviewer": "product",
                    "ambiguity_type": "unanswerable",
                    "finding_ids": ["finding-clarify-1"],
                }
            ],
            "answers_applied": [
                {
                    "question_id": "clarify-123",
                    "question": "What measurable user outcome defines success?",
                    "answer": "Existing answer.",
                    "reviewer": "product",
                }
            ],
            "findings_updated": [],
        },
        findings=[
            {
                "finding_id": "finding-clarify-1",
                "title": "Missing success metric",
                "detail": "The requirement does not define success.",
                "description": "The requirement does not define success.",
                "severity": "medium",
                "category": "scope",
                "source_reviewer": "product",
                "reviewers": ["product"],
                "ambiguity_type": "unanswerable",
                "clarification_applied": True,
                "original_severity": "high",
                "user_clarification": "Existing answer.",
            }
        ],
        reviewer_summaries=[],
    )

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification",
        json={
            "run_id": run_id,
            "question_id": "clarify-123",
            "answer": "Updated answer.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["clarification_status"] == "answered"
    assert payload["has_pending_questions"] is False
    assert payload["clarification"]["answers_applied"][0]["answer"] == "Updated answer."


def test_feishu_clarification_uses_request_context_when_payload_context_is_missing(
    tmp_path, monkeypatch
):
    run_id = "20260309T020211Z"
    _write_run_payloads(
        tmp_path,
        run_id,
        clarification={
            "triggered": True,
            "status": "pending",
            "questions": [
                {
                    "id": "clarify-ctx",
                    "question": "Which metric matters most?",
                    "reviewer": "product",
                    "ambiguity_type": "unanswerable",
                    "finding_ids": ["finding-ctx-1"],
                }
            ],
            "answers_applied": [],
            "findings_updated": [],
        },
        findings=[
            {
                "finding_id": "finding-ctx-1",
                "title": "Missing target metric",
                "detail": "Metric is missing.",
                "description": "Metric is missing.",
                "severity": "high",
                "category": "scope",
                "source_reviewer": "product",
                "reviewers": ["product"],
                "ambiguity_type": "unanswerable",
                "clarification_applied": False,
                "original_severity": "",
                "user_clarification": "",
            }
        ],
        reviewer_summaries=[],
    )
    (tmp_path / run_id / "entry_context.json").write_text(
        json.dumps(
            {
                "source_origin": "feishu",
                "entry_mode": "plugin",
                "submitter_open_id": "ou_request_user",
                "tenant_key": "tenant-request",
                "trigger_source": "feishu",
                "result_page_context": {
                    "trigger_source": "feishu",
                    "open_id": "ou_request_user",
                    "tenant_key": "tenant-request",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification?open_id=ou_request_user&tenant_key=tenant-request",
        json={
            "run_id": run_id,
            "question_id": "clarify-ctx",
            "answer": "Use weekly retained recruiters.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_page"]["url"] == (
        f"/run/{run_id}?trigger_source=feishu&open_id=ou_request_user&tenant_key=tenant-request&embed=feishu"
    )


def test_feishu_clarification_falls_back_to_entry_context_when_request_context_missing(
    tmp_path, monkeypatch
):
    run_id = "20260309T020213Z"
    _write_run_payloads(
        tmp_path,
        run_id,
        clarification={
            "triggered": True,
            "status": "pending",
            "questions": [
                {
                    "id": "clarify-fallback",
                    "question": "What is the target conversion rate?",
                    "reviewer": "product",
                    "ambiguity_type": "unanswerable",
                    "finding_ids": ["finding-fallback-1"],
                }
            ],
            "answers_applied": [],
            "findings_updated": [],
        },
        findings=[
            {
                "finding_id": "finding-fallback-1",
                "title": "Missing target conversion rate",
                "detail": "Target conversion rate is missing.",
                "description": "Target conversion rate is missing.",
                "severity": "high",
                "category": "scope",
                "source_reviewer": "product",
                "reviewers": ["product"],
                "ambiguity_type": "unanswerable",
                "clarification_applied": False,
                "original_severity": "",
                "user_clarification": "",
            }
        ],
        reviewer_summaries=[],
    )
    (tmp_path / run_id / "entry_context.json").write_text(
        json.dumps(
            {
                "source_origin": "feishu",
                "entry_mode": "plugin",
                "submitter_open_id": "ou_entry_user",
                "tenant_key": "tenant-entry",
                "trigger_source": "feishu",
                "result_page_context": {
                    "trigger_source": "feishu",
                    "open_id": "ou_entry_user",
                    "tenant_key": "tenant-entry",
                    "lang": "zh-CN",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification",
        json={
            "run_id": run_id,
            "question_id": "clarify-fallback",
            "answer": "Target conversion rate should reach 5%.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_page"]["url"] == (
        f"/run/{run_id}?trigger_source=feishu&open_id=ou_entry_user&tenant_key=tenant-entry&lang=zh-CN&embed=feishu"
    )


def test_feishu_clarification_result_page_degrades_when_open_id_is_missing(
    tmp_path, monkeypatch
):
    run_id = "20260309T020212Z"
    _write_run_payloads(
        tmp_path,
        run_id,
        clarification={
            "triggered": True,
            "status": "answered",
            "questions": [],
            "answers_applied": [],
            "findings_updated": [],
        },
        findings=[],
        reviewer_summaries=[],
    )
    (tmp_path / run_id / "entry_context.json").write_text(
        json.dumps(
            {
                "source_origin": "feishu",
                "entry_mode": "plugin",
                "submitter_open_id": "",
                "tenant_key": "tenant-only",
                "trigger_source": "feishu",
                "result_page_context": {
                    "trigger_source": "feishu",
                    "tenant_key": "tenant-only",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = app_module._build_result_page_payload(run_id, run_dir=tmp_path / run_id)
    assert payload == {
        "path": f"/run/{run_id}?trigger_source=feishu&tenant_key=tenant-only&embed=feishu",
        "url": f"/run/{run_id}?trigger_source=feishu&tenant_key=tenant-only&embed=feishu",
    }


def test_feishu_clarification_result_page_uses_entry_identity_when_request_context_is_forged(
    tmp_path, monkeypatch
):
    run_id = "20260309T020214Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    run_dir.joinpath("entry_context.json").write_text(
        json.dumps(
            {
                "source_origin": "feishu",
                "entry_mode": "plugin",
                "submitter_open_id": "ou_owner",
                "tenant_key": "tenant-owner",
                "trigger_source": "feishu",
                "result_page_context": {
                    "trigger_source": "feishu",
                    "open_id": "ou_owner",
                    "tenant_key": "tenant-owner",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)

    payload = app_module._build_result_page_payload(
        run_id,
        request_context={
            "trigger_source": "feishu",
            "open_id": "ou_forged",
            "tenant_key": "tenant-forged",
            "locale": "zh-CN",
        },
        run_dir=run_dir,
    )

    assert payload == {
        "path": f"/run/{run_id}?trigger_source=feishu&open_id=ou_owner&tenant_key=tenant-owner&locale=zh-CN&embed=feishu",
        "url": f"/run/{run_id}?trigger_source=feishu&open_id=ou_owner&tenant_key=tenant-owner&locale=zh-CN&embed=feishu",
    }


def test_feishu_clarification_rejects_invalid_payload(monkeypatch):
    monkeypatch.setenv("MARRDP_FEISHU_SIGNATURE_DISABLED", "true")
    client = _build_client()

    response = client.post(
        "/api/feishu/clarification",
        json={
            "run_id": "20260309T020210Z",
            "question_id": "",
            "answer": "",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "request_validation_error"
