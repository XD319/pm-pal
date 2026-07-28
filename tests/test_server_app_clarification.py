from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from prd_pal.server import app as app_module
from tests.project_review_helpers import (
    create_test_project,
    link_run_to_project,
    project_review_path,
)
from prd_pal.workspace import ArtifactRepository, ArtifactVersion, ArtifactVersionStatus


def test_submit_review_clarification_updates_findings_and_returns_refreshed_result(
    tmp_path, monkeypatch
):
    run_id = "20260309T010206Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    parallel_review = {
        "review_mode": "full",
        "findings": [
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
        "reviewer_summaries": [
            {
                "reviewer": "product",
                "summary": "Need clarification before approval.",
                "status": "completed",
                "ambiguity_type": "missing_product_goal",
                "clarification_question": "What measurable user outcome defines success?",
                "notes": [],
            }
        ],
        "clarification": {
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
        "clarification": dict(parallel_review["clarification"]),
        "review_clarification": dict(parallel_review["clarification"]),
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

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.post(
        project_review_path(project_id, run_id, "/clarification"),
        json={
            "answers": [
                {
                    "question_id": "clarify-123",
                    "answer": "Success means recruiters can finish login and reach the dashboard within 30 seconds.",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["clarification"]["status"] == "answered"
    finding = payload["result"]["parallel_review"]["findings"][0]
    assert finding["clarification_applied"] is True
    assert finding["original_severity"] == "high"
    assert finding["severity"] == "medium"
    assert finding["user_clarification"].startswith(
        "Success means recruiters can finish login"
    )

    refreshed_parallel = json.loads(
        (run_dir / "review_result.json").read_text(encoding="utf-8")
    )
    assert refreshed_parallel["clarification"]["status"] == "answered"
    assert refreshed_parallel["findings"][0]["clarification_applied"] is True


def test_submit_review_clarification_applies_patch_when_patch_context_is_present(
    tmp_path, monkeypatch
):
    workspace_db_path = tmp_path / "workspace.sqlite3"
    artifact_source_path = tmp_path / "artifact.v1.json"
    artifact_source_path.write_text(
        json.dumps(
            {
                "artifact_id": "prd_doc",
                "version": 1,
                "title": "Checkout PRD",
                "metadata": {},
                "blocks": [
                    {
                        "block_id": "functional.payment_timeout",
                        "type": "requirement",
                        "title": "支付超时时间",
                        "content": "支付超时时间为30分钟。",
                        "meta": {"priority": "P2"},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    import asyncio

    artifact_repository = ArtifactRepository(workspace_db_path)
    asyncio.run(artifact_repository.initialize())
    asyncio.run(
        artifact_repository.upsert_version(
            ArtifactVersion(
                version_id="artifact-v1",
                workspace_id="ws-1",
                artifact_key="prd_doc",
                artifact_type="structured_prd",
                status=ArtifactVersionStatus.active,
                version_number=1,
                title="Checkout PRD",
                source_run_id="seed-run-1",
                created_at="2026-04-13T08:00:00+00:00",
                updated_at="2026-04-13T08:00:00+00:00",
                content_path=str(artifact_source_path),
            )
        )
    )

    run_id = "20260309T010208Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    patch_context = {
        "artifact_version_id": "artifact-v1",
        "workspace_db_path": str(workspace_db_path),
        "artifact_output_root": str(tmp_path / "artifact_outputs"),
        "failure_mode": "needs_review",
    }
    parallel_review = {
        "review_mode": "full",
        "findings": [
            {
                "finding_id": "finding-clarify-3",
                "title": "Missing timeout decision",
                "detail": "The requirement does not define timeout handling.",
                "description": "The requirement does not define timeout handling.",
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
        "reviewer_summaries": [
            {
                "reviewer": "product",
                "summary": "Need timeout clarification before approval.",
                "status": "completed",
                "ambiguity_type": "missing_product_goal",
                "clarification_question": "支付超时应该多久？",
                "notes": [],
            }
        ],
        "clarification": {
            "triggered": True,
            "status": "pending",
            "questions": [
                {
                    "id": "clarify-timeout-1",
                    "question": "支付超时应该多久？",
                    "reviewer": "product",
                    "ambiguity_type": "unanswerable",
                    "finding_ids": ["finding-clarify-3"],
                }
            ],
            "answers_applied": [],
            "findings_updated": [],
        },
        "artifacts": {
            "review_result_json": str(run_dir / "review_result.json"),
            "review_report_json": str(run_dir / "review_report.json"),
            "review_report_md": str(run_dir / "review_report.md"),
            "review_summary_md": str(run_dir / "review_summary.md"),
        },
        "clarification_patch_context": patch_context,
    }
    report_payload = {
        "run_id": run_id,
        "mode": "full",
        "review_mode": "full",
        "gating": {"selected_mode": "full", "reasons": [], "skipped": False},
        "parallel_review": parallel_review,
        "clarification": dict(parallel_review["clarification"]),
        "review_clarification": dict(parallel_review["clarification"]),
        "parallel_review_meta": {
            "selected_mode": "full",
            "review_mode": "full",
            "gating": {"selected_mode": "full", "reasons": [], "skipped": False},
            "artifact_paths": {
                "review_result_json": str(run_dir / "review_result.json")
            },
        },
        "trace": {"reviewer": {"status": "ok"}},
        "clarification_patch_context": patch_context,
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

    monkeypatch.setattr(app_module, "OUTPUTS_ROOT", tmp_path)
    app_module._jobs.clear()

    client = TestClient(app_module.app)
    project_id = create_test_project(client)
    link_run_to_project(project_id, run_id)
    response = client.post(
        project_review_path(project_id, run_id, "/clarification"),
        json={
            "answers": [
                {
                    "question_id": "clarify-timeout-1",
                    "answer": "改成15分钟，并在超时后释放库存。",
                }
            ],
            "patch": {
                "schema_version": "1.0",
                "patch_id": "patch-timeout-1",
                "artifact_id": "prd_doc",
                "base_version": 1,
                "clarification_id": "clarify-timeout-1",
                "author": {"type": "llm", "model": "test-model"},
                "summary": "缩短支付超时",
                "ops": [
                    {
                        "op_id": "op-1",
                        "action": "replace_text",
                        "target": {
                            "block_id": "functional.payment_timeout",
                            "field": "content",
                        },
                        "old_value": "支付超时时间为30分钟。",
                        "new_value": "支付超时时间为15分钟。",
                        "rationale": "根据澄清缩短超时时间",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact_patch"]["status"] == "applied"
    assert "只输出 patch JSON" in payload["artifact_patch"]["prompt"]
    assert payload["artifact_patch"]["apply_result"]["next_version_number"] == 2
    assert Path(payload["artifact_patch"]["apply_result"]["content_path"]).exists()

    refreshed_report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert refreshed_report["artifact_patch"]["status"] == "applied"
