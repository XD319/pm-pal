from __future__ import annotations

import json
from pathlib import Path

import pytest

from pm_pal.mcp_server import server as mcp_server
from pm_pal.notifications import (
    BaseNotifier,
    dispatch_notification,
    read_notification_records,
)
from pm_pal.service import execution_service, review_service
from pm_pal.service.review_service import ReviewResultSummary


@pytest.mark.asyncio
async def test_review_prd_routes_to_review_service_with_mock(
    monkeypatch, sample_prd_text: str
):
    fixed = ReviewResultSummary(
        run_id="20260304T120000Z",
        report_md_path="outputs/20260304T120000Z/report.md",
        report_json_path="outputs/20260304T120000Z/report.json",
        high_risk_ratio=0.25,
        coverage_ratio=0.8,
        revision_round=1,
        status="completed",
        run_trace_path="outputs/20260304T120000Z/run_trace.json",
        delivery_bundle_path="outputs/20260304T120000Z/delivery_bundle.json",
    )

    async def fake_review_prd_text_async(
        prd_text: str | None = None,
        *,
        prd_path: str | None = None,
        source: str | None = None,
        run_id: str | None = None,
        config_overrides: dict[str, object] | None = None,
    ) -> ReviewResultSummary:
        assert prd_text == sample_prd_text
        assert prd_path is None
        assert source is None
        assert run_id is None
        assert isinstance(config_overrides, dict)
        return fixed

    monkeypatch.setattr(
        review_service, "review_prd_text", lambda *args, **kwargs: fixed
    )
    monkeypatch.setattr(
        review_service, "review_prd_text_async", fake_review_prd_text_async
    )

    result = await mcp_server.review_prd(prd_text=sample_prd_text)

    assert "error" not in result
    assert result["run_id"] == fixed.run_id
    assert result["status"] == "completed"
    assert result["metrics"]["coverage_ratio"] == fixed.coverage_ratio
    assert result["metrics"]["high_risk_ratio"] == fixed.high_risk_ratio
    assert result["metrics"]["revision_round"] == fixed.revision_round
    assert result["artifacts"]["report_md_path"] == fixed.report_md_path
    assert result["artifacts"]["report_json_path"] == fixed.report_json_path
    assert result["artifacts"]["trace_path"] == fixed.run_trace_path
    assert result["artifacts"]["delivery_bundle_path"] == fixed.delivery_bundle_path


@pytest.mark.asyncio
async def test_review_prd_rejects_missing_prd_text_and_path():
    result = await mcp_server.review_prd()

    assert result["status"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_review_prd_rejects_both_prd_text_and_path():
    result = await mcp_server.review_prd(prd_text="text", prd_path="docs/sample_prd.md")

    assert result["status"] == "failed"
    assert result["error"]["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_review_prd_rejects_invalid_prd_path():
    result = await mcp_server.review_prd(prd_path="docs/not_exists_prd.md")

    assert result["status"] == "failed"
    assert result["error"]["code"] == "PRD_NOT_FOUND"


@pytest.mark.asyncio
async def test_review_prd_supports_local_source_and_persists_source_metadata(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "sample_prd.md"
    source_path.write_text(
        "# Campus Recruitment PRD\n\nEnable recruiter login.", encoding="utf-8"
    )
    fixed_run_id = "20260308T010203Z"

    async def fake_run_review(
        requirement_doc: str,
        *,
        run_id: str | None = None,
        outputs_root: str | None = None,
        progress_hook=None,
        **kwargs,
    ) -> dict[str, object]:
        assert requirement_doc == source_path.read_text(encoding="utf-8")
        assert str(outputs_root) == str(tmp_path)
        assert progress_hook is None or callable(progress_hook)
        assert isinstance(kwargs.get("review_profile"), dict)
        assert isinstance(kwargs.get("review_profile_pack"), dict)
        assert isinstance(kwargs.get("canonical_review_request"), dict)

        resolved_run_id = run_id or fixed_run_id
        run_dir = tmp_path / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "final_report": "# Requirement Review Report\n\nSummary.",
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Enable recruiter login",
                    "acceptance_criteria": ["Login succeeds"],
                }
            ],
            "review_results": [
                {
                    "id": "REQ-001",
                    "description": "Enable recruiter login",
                    "is_ambiguous": False,
                    "issues": [],
                }
            ],
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Implement recruiter login",
                    "owner": "BE",
                    "requirement_ids": ["REQ-001"],
                }
            ],
            "risks": [
                {
                    "id": "RISK-001",
                    "description": "Auth regression",
                    "impact": "low",
                    "mitigation": "Run auth regression tests",
                }
            ],
            "implementation_plan": {
                "implementation_steps": ["Update auth flow"],
                "target_modules": ["backend.auth"],
                "constraints": [],
            },
            "test_plan": {
                "test_scope": ["Recruiter login API"],
                "edge_cases": [],
                "regression_focus": ["Auth regression"],
            },
            "codex_prompt_handoff": {
                "agent_prompt": "Implement the login change.",
                "recommended_execution_order": ["Review auth flow", "Apply patch"],
                "non_goals": [],
                "validation_checklist": ["Run auth tests"],
            },
            "claude_code_prompt_handoff": {
                "agent_prompt": "Validate the login change.",
                "recommended_execution_order": ["Inspect diff", "Run tests"],
                "non_goals": [],
                "validation_checklist": ["Regression covered"],
            },
            "metrics": {"coverage_ratio": 1.0},
            "high_risk_ratio": 0.0,
            "revision_round": 0,
            "trace": {},
        }
        report_paths = {
            "report_md": str(run_dir / "report.md"),
            "report_json": str(run_dir / "report.json"),
            "run_trace": str(run_dir / "run_trace.json"),
        }
        (run_dir / "report.md").write_text(result["final_report"], encoding="utf-8")
        (run_dir / "report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "run_trace.json").write_text(
            json.dumps(result["trace"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "run_id": resolved_run_id,
            "run_dir": str(run_dir),
            "result": result,
            "report_paths": report_paths,
        }

    monkeypatch.setattr(review_service, "run_review", fake_run_review)

    result = await mcp_server.review_prd(
        source=str(source_path), options={"outputs_root": str(tmp_path)}
    )

    assert "error" not in result
    resolved_run_id = result["run_id"]
    report_payload = json.loads(
        (tmp_path / resolved_run_id / "report.json").read_text(encoding="utf-8")
    )
    trace_payload = json.loads(
        (tmp_path / resolved_run_id / "run_trace.json").read_text(encoding="utf-8")
    )
    bundle_payload = json.loads(
        (tmp_path / resolved_run_id / "delivery_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert report_payload["source_metadata"]["mime_type"] == "text/markdown"
    assert trace_payload["source_metadata"]["extra"]["extension"] == ".md"
    assert (
        bundle_payload["metadata"]["source_metadata"]["size_bytes"]
        == source_path.stat().st_size
    )


@pytest.mark.asyncio
async def test_review_prd_feishu_source_requires_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("MARRDP_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("MARRDP_FEISHU_APP_SECRET", raising=False)
    result = await mcp_server.review_prd(
        source="feishu://wiki/space/doc-token", options={"outputs_root": str(tmp_path)}
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "MARRDP_FEISHU_APP_ID" in result["error"]["message"]


@pytest.mark.asyncio
async def test_review_requirement_feishu_source_requires_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MARRDP_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("MARRDP_FEISHU_APP_SECRET", raising=False)

    result = await mcp_server.review_requirement(
        source="feishu://wiki/space/doc-token",
        options={"outputs_root": str(tmp_path)},
    )

    assert result["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "MARRDP_FEISHU_APP_ID" in result["error"]["message"]


@pytest.mark.asyncio
async def test_review_requirement_routes_to_review_service_with_metadata_alias(
    monkeypatch,
):
    captured: dict[str, object] = {}

    async def fake_review_requirement_for_mcp_async(
        *,
        prd_text: str | None,
        prd_path: str | None,
        source: str | None,
        options: dict[str, object] | None = None,
        invocation_meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        captured["options"] = options or {}
        captured["invocation_meta"] = invocation_meta or {}
        assert prd_text == "mock prd"
        assert prd_path is None
        assert source is None
        return {
            "review_id": "20260304T120000Z",
            "run_id": "20260304T120000Z",
            "findings": [],
            "open_questions": [],
            "risk_items": [],
            "conflicts": [],
            "report_path": "outputs/20260304T120000Z/report.json",
            "review_mode": "single_review",
        }

    monkeypatch.setattr(
        mcp_server,
        "review_requirement_for_mcp_async",
        fake_review_requirement_for_mcp_async,
    )

    result = await mcp_server.review_requirement(
        prd_text="mock prd",
        metadata={
            "outputs_root": "metadata-root",
            "client_metadata": {"channel": "cli"},
        },
        options={
            "outputs_root": "options-root",
            "review_mode_override": "parallel_review",
        },
    )

    assert "error" not in result
    assert result["review_id"] == "20260304T120000Z"
    assert result["run_id"] == "20260304T120000Z"
    assert "artifacts" not in result
    assert captured["options"]["outputs_root"] == "options-root"
    assert captured["options"]["review_mode_override"] == "parallel_review"
    assert captured["options"]["audit_context"]["tool_name"] == "review_requirement"
    assert captured["options"]["audit_context"]["client_metadata"]["channel"] == "cli"


@pytest.mark.asyncio
async def test_review_requirement_returns_review_only_payload_for_single_review(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "sample_prd.md"
    source_path.write_text(
        "# Campus Recruitment PRD\n\nClarify recruiter login success metrics.",
        encoding="utf-8",
    )
    fixed_run_id = "20260308T030405Z"

    async def fake_run_review(
        requirement_doc: str,
        *,
        run_id: str | None = None,
        outputs_root: str | None = None,
        progress_hook=None,
        review_mode_override: str | None = None,
        **kwargs,
    ) -> dict[str, object]:
        assert requirement_doc == source_path.read_text(encoding="utf-8")
        assert str(outputs_root) == str(tmp_path)
        assert progress_hook is None or callable(progress_hook)
        assert review_mode_override is None
        assert isinstance(kwargs.get("review_profile"), dict)
        assert isinstance(kwargs.get("review_profile_pack"), dict)
        assert isinstance(kwargs.get("canonical_review_request"), dict)

        resolved_run_id = run_id or fixed_run_id
        run_dir = tmp_path / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "final_report": "# Requirement Review Report\n\nSingle review facade.",
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Clarify recruiter login success metrics",
                    "acceptance_criteria": ["Recruiter login succeeds"],
                }
            ],
            "review_results": [
                {
                    "id": "REQ-001",
                    "is_clear": False,
                    "is_testable": True,
                    "is_ambiguous": True,
                    "issues": ["Success metric is missing"],
                    "suggestions": "Define measurable login completion criteria.",
                }
            ],
            "review_mode": "single_review",
            "review_open_questions": [
                {
                    "question": "What measurable success metric confirms recruiter login is complete?",
                    "reviewers": ["single_reviewer"],
                    "issues": ["Success metric is missing"],
                }
            ],
            "review_risk_items": [
                {
                    "title": "REQ-001",
                    "detail": "Success metric is missing",
                    "severity": "high",
                    "category": "review_quality",
                    "reviewers": ["single_reviewer"],
                }
            ],
            "parallel-review_meta": {
                "selected_mode": "single_review",
                "review_mode": "single_review",
            },
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Implement recruiter login",
                    "owner": "BE",
                    "requirement_ids": ["REQ-001"],
                }
            ],
            "risks": [
                {
                    "id": "RISK-001",
                    "description": "Auth regression",
                    "impact": "low",
                    "mitigation": "Run auth regression tests",
                }
            ],
            "implementation_plan": {
                "implementation_steps": ["Update auth flow"],
                "target_modules": ["backend.auth"],
                "constraints": [],
            },
            "test_plan": {
                "test_scope": ["Recruiter login API"],
                "edge_cases": [],
                "regression_focus": ["Auth regression"],
            },
            "codex_prompt_handoff": {
                "agent_prompt": "Implement the login change.",
                "recommended_execution_order": ["Review auth flow", "Apply patch"],
                "non_goals": [],
                "validation_checklist": ["Run auth tests"],
            },
            "claude_code_prompt_handoff": {
                "agent_prompt": "Validate the login change.",
                "recommended_execution_order": ["Inspect diff", "Run tests"],
                "non_goals": [],
                "validation_checklist": ["Regression covered"],
            },
            "metrics": {"coverage_ratio": 1.0},
            "high_risk_ratio": 0.5,
            "revision_round": 0,
            "trace": {},
        }
        report_paths = {
            "report_md": str(run_dir / "report.md"),
            "report_json": str(run_dir / "report.json"),
            "run_trace": str(run_dir / "run_trace.json"),
        }
        (run_dir / "report.md").write_text(result["final_report"], encoding="utf-8")
        (run_dir / "report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "run_trace.json").write_text(
            json.dumps(result["trace"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "run_id": resolved_run_id,
            "run_dir": str(run_dir),
            "result": result,
            "report_paths": report_paths,
        }

    monkeypatch.setattr(review_service, "run_review", fake_run_review)

    result = await mcp_server.review_requirement(
        source=str(source_path), options={"outputs_root": str(tmp_path)}
    )

    assert "error" not in result
    assert result["review_id"] == result["run_id"]
    assert result["review_mode"] == "single_review"
    assert result["report_path"] == str(tmp_path / result["run_id"] / "report.json")
    assert len(result["findings"]) == 1
    assert result["findings"][0]["requirement_id"] == "REQ-001"
    assert result["open_questions"][0]["question"].startswith(
        "What measurable success metric"
    )
    assert result["risk_items"][0]["severity"] == "high"
    assert result["conflicts"] == []
    assert "status" not in result
    assert "metrics" not in result
    assert "artifacts" not in result


def test_get_report_rejects_invalid_run_id_via_tool_handler(tmp_path):
    result = mcp_server.get_report(
        run_id="../escape", options={"outputs_root": str(tmp_path)}
    )

    assert result["error"]["code"] == "invalid_run_id"


def test_get_report_returns_not_found_via_tool_handler(tmp_path):
    result = mcp_server.get_report(
        run_id="20260304T010203Z", options={"outputs_root": str(tmp_path)}
    )

    assert result["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_generate_delivery_bundle_success(tmp_path, sample_report_json: dict):
    run_id = "20260304T010203Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    report_payload = sample_report_json
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text("# Report", encoding="utf-8")
    for filename in (
        "implementation_pack.json",
        "test_pack.json",
        "execution_pack.json",
    ):
        (run_dir / filename).write_text("{}", encoding="utf-8")

    result = await mcp_server.generate_delivery_bundle(
        run_id=run_id, options={"outputs_root": str(tmp_path)}
    )

    assert "error" not in result
    assert result["bundle_id"] == f"bundle-{run_id}"
    assert result["status"] == "draft"
    assert (run_dir / "delivery_bundle.json").exists()


@pytest.mark.asyncio
async def test_generate_delivery_bundle_missing_run_returns_error(tmp_path):
    result = await mcp_server.generate_delivery_bundle(
        run_id="20260304T010203Z", options={"outputs_root": str(tmp_path)}
    )

    assert result["error"]["code"] == "not_found"


def test_approve_handoff_success(tmp_path):
    run_id = "20260304T010204Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    bundle_payload = {
        "bundle_id": f"bundle-{run_id}",
        "bundle_version": "1.0",
        "created_at": "2026-03-07T12:00:00+00:00",
        "status": "draft",
        "source_run_id": run_id,
        "artifacts": {
            "prd_review_report": {
                "artifact_type": "prd_review_report",
                "path": str(run_dir / "prd_review_report.md"),
            },
            "open_questions": {
                "artifact_type": "open_questions",
                "path": str(run_dir / "open_questions.md"),
            },
            "scope_boundary": {
                "artifact_type": "scope_boundary",
                "path": str(run_dir / "scope_boundary.md"),
            },
            "tech_design_draft": {
                "artifact_type": "tech_design_draft",
                "path": str(run_dir / "tech_design_draft.md"),
            },
            "test_checklist": {
                "artifact_type": "test_checklist",
                "path": str(run_dir / "test_checklist.md"),
            },
            "implementation_pack": {
                "artifact_type": "implementation_pack",
                "path": str(run_dir / "implementation_pack.json"),
            },
            "test_pack": {
                "artifact_type": "test_pack",
                "path": str(run_dir / "test_pack.json"),
            },
            "execution_pack": {
                "artifact_type": "execution_pack",
                "path": str(run_dir / "execution_pack.json"),
            },
        },
        "approval_history": [],
        "metadata": {},
    }
    (run_dir / "delivery_bundle.json").write_text(
        json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = mcp_server.approve_handoff(
        bundle_id=f"bundle-{run_id}",
        action="approve",
        reviewer="alice",
        comment="Ready",
        options={"outputs_root": str(tmp_path)},
    )

    approval_records_payload = json.loads(
        (run_dir / "approval_records.json").read_text(encoding="utf-8")
    )
    snapshot_payload = json.loads(
        (run_dir / "status_snapshot.json").read_text(encoding="utf-8")
    )

    assert "error" not in result
    assert result["status"] == "approved"
    assert len(result["approval_history"]) == 1
    assert result["approval_records_path"] == str(run_dir / "approval_records.json")
    assert result["status_snapshot_path"] == str(run_dir / "status_snapshot.json")
    assert result["status_snapshot"]["bundle_status"] == "approved"
    assert approval_records_payload["approval_records"][0]["action"] == "approve"
    assert (
        approval_records_payload["approval_records"][0]["from_bundle_status"] == "draft"
    )
    assert (
        approval_records_payload["approval_records"][0]["to_bundle_status"]
        == "approved"
    )
    assert (
        approval_records_payload["approval_records"][0]["workspace_status"]
        == "confirmed"
    )
    assert approval_records_payload["approval_records"][0]["reviewer"] == "alice"
    assert snapshot_payload["run_id"] == run_id
    assert snapshot_payload["bundle_id"] == f"bundle-{run_id}"
    assert snapshot_payload["bundle_status"] == "approved"
    assert snapshot_payload["workspace_status"] == "confirmed"


def test_approve_handoff_invalid_transition_returns_error(tmp_path):
    run_id = "20260304T010205Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    bundle_payload = {
        "bundle_id": f"bundle-{run_id}",
        "bundle_version": "1.0",
        "created_at": "2026-03-07T12:00:00+00:00",
        "status": "approved",
        "source_run_id": run_id,
        "artifacts": {
            "prd_review_report": {
                "artifact_type": "prd_review_report",
                "path": str(run_dir / "prd_review_report.md"),
            },
            "open_questions": {
                "artifact_type": "open_questions",
                "path": str(run_dir / "open_questions.md"),
            },
            "scope_boundary": {
                "artifact_type": "scope_boundary",
                "path": str(run_dir / "scope_boundary.md"),
            },
            "tech_design_draft": {
                "artifact_type": "tech_design_draft",
                "path": str(run_dir / "tech_design_draft.md"),
            },
            "test_checklist": {
                "artifact_type": "test_checklist",
                "path": str(run_dir / "test_checklist.md"),
            },
            "implementation_pack": {
                "artifact_type": "implementation_pack",
                "path": str(run_dir / "implementation_pack.json"),
            },
            "test_pack": {
                "artifact_type": "test_pack",
                "path": str(run_dir / "test_pack.json"),
            },
            "execution_pack": {
                "artifact_type": "execution_pack",
                "path": str(run_dir / "execution_pack.json"),
            },
        },
        "approval_history": [],
        "metadata": {},
    }
    original_approval_records = {
        "approval_records": [
            {
                "record_id": "approval-existing",
                "run_id": run_id,
                "bundle_id": f"bundle-{run_id}",
                "timestamp": "2026-03-07T12:00:00+00:00",
                "action": "approve",
                "from_bundle_status": "draft",
                "to_bundle_status": "approved",
                "workspace_status": "confirmed",
                "reviewer": "alice",
                "comment": "Approved",
            }
        ]
    }
    original_snapshot = {
        "run_id": run_id,
        "bundle_id": f"bundle-{run_id}",
        "bundle_status": "approved",
        "workspace_status": "confirmed",
        "updated_at": "2026-03-07T12:00:00+00:00",
    }
    (run_dir / "delivery_bundle.json").write_text(
        json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "approval_records.json").write_text(
        json.dumps(original_approval_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "status_snapshot.json").write_text(
        json.dumps(original_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = mcp_server.approve_handoff(
        bundle_id=f"bundle-{run_id}",
        action="reset_to_draft",
        reviewer="alice",
        comment="Reopen",
        options={"outputs_root": str(tmp_path)},
    )

    reloaded_bundle = json.loads(
        (run_dir / "delivery_bundle.json").read_text(encoding="utf-8")
    )
    approval_records_payload = json.loads(
        (run_dir / "approval_records.json").read_text(encoding="utf-8")
    )
    snapshot_payload = json.loads(
        (run_dir / "status_snapshot.json").read_text(encoding="utf-8")
    )

    assert result["error"]["code"] == "invalid_input"
    assert reloaded_bundle["status"] == "approved"
    assert approval_records_payload == original_approval_records
    assert snapshot_payload == original_snapshot


def _write_review_workspace_fixture(tmp_path, run_id: str = "20260307T010205Z") -> str:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    bundle_payload = {
        "bundle_id": f"bundle-{run_id}",
        "bundle_version": "1.0",
        "created_at": "2026-03-07T12:00:00+00:00",
        "status": "draft",
        "source_run_id": run_id,
        "artifacts": {
            "prd_review_report": {
                "artifact_type": "prd_review_report",
                "path": str(run_dir / "prd_review_report.md"),
            },
            "open_questions": {
                "artifact_type": "open_questions",
                "path": str(run_dir / "open_questions.md"),
            },
            "scope_boundary": {
                "artifact_type": "scope_boundary",
                "path": str(run_dir / "scope_boundary.md"),
            },
            "tech_design_draft": {
                "artifact_type": "tech_design_draft",
                "path": str(run_dir / "tech_design_draft.md"),
            },
            "test_checklist": {
                "artifact_type": "test_checklist",
                "path": str(run_dir / "test_checklist.md"),
            },
            "implementation_pack": {
                "artifact_type": "implementation_pack",
                "path": str(run_dir / "implementation_pack.json"),
            },
            "test_pack": {
                "artifact_type": "test_pack",
                "path": str(run_dir / "test_pack.json"),
            },
            "execution_pack": {
                "artifact_type": "execution_pack",
                "path": str(run_dir / "execution_pack.json"),
            },
        },
        "approval_history": [],
        "metadata": {},
    }
    (run_dir / "delivery_bundle.json").write_text(
        json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = mcp_server.approve_handoff(
        bundle_id=f"bundle-{run_id}",
        action="approve",
        reviewer="alice",
        comment="Ready",
        options={"outputs_root": str(tmp_path)},
    )

    assert "error" not in result
    return f"bundle-{run_id}"


def test_get_review_workspace_by_run_id_returns_workspace_state(tmp_path):
    run_id = "20260304T010206Z"
    bundle_id = _write_review_workspace_fixture(tmp_path, run_id=run_id)

    result = mcp_server.get_review_workspace(
        run_id=run_id, options={"outputs_root": str(tmp_path)}
    )

    assert "error" not in result
    assert result["run_id"] == run_id
    assert result["bundle"]["bundle_id"] == bundle_id
    assert result["bundle"]["status"] == "approved"
    assert len(result["approval_history"]) == 1
    assert len(result["approval_records"]) == 1
    assert result["approval_records"][0]["action"] == "approve"
    assert result["status_snapshot"]["bundle_status"] == "approved"
    assert result["status_snapshot"]["workspace_status"] == "confirmed"
    assert result["paths"]["approval_records_path"].endswith("approval_records.json")


def test_get_review_workspace_by_bundle_id_returns_workspace_state(tmp_path):
    run_id = "20260304T010207Z"
    bundle_id = _write_review_workspace_fixture(tmp_path, run_id=run_id)

    result = mcp_server.get_review_workspace(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    assert "error" not in result
    assert result["run_id"] == run_id
    assert result["bundle"]["bundle_id"] == bundle_id
    assert result["approval_history"][0]["to_status"] == "approved"
    assert result["approval_records"][0]["bundle_id"] == bundle_id
    assert result["status_snapshot"]["run_id"] == run_id


def test_get_review_workspace_missing_files_returns_error(tmp_path):
    run_id = "20260304T010208Z"
    bundle_id = _write_review_workspace_fixture(tmp_path, run_id=run_id)
    (tmp_path / run_id / "status_snapshot.json").unlink()

    result = mcp_server.get_review_workspace(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    assert result["error"]["code"] == "not_found"
    assert "status_snapshot.json" in result["error"]["message"]


def _write_approved_bundle_fixture(tmp_path, run_id: str = "20260307T010206Z") -> str:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)

    implementation_pack = {
        "pack_type": "implementation_pack",
        "task_id": "TASK-001",
        "title": "Implement login",
        "summary": "Support recruiter login safely.",
        "context": "Repository auth flow context.",
        "target_modules": ["backend.auth"],
        "implementation_steps": ["Inspect auth flow", "Implement login change"],
        "constraints": ["Do not break existing auth flow"],
        "acceptance_criteria": ["Login succeeds"],
        "recommended_skills": ["pytest"],
        "agent_handoff": {
            "primary_agent": "codex",
            "supporting_agents": ["claude_code"],
            "goals": ["Implement login"],
            "expected_output": "Small safe auth patch",
        },
    }
    test_pack = {
        "pack_type": "test_pack",
        "task_id": "TASK-001",
        "title": "Test login",
        "summary": "Validate recruiter login flow.",
        "test_scope": ["Login API"],
        "edge_cases": ["Invalid credentials"],
        "acceptance_criteria": ["Regression covered"],
        "agent_handoff": {
            "primary_agent": "claude_code",
            "supporting_agents": ["codex"],
            "goals": ["Run auth checks"],
            "expected_output": "Validation summary",
        },
    }
    execution_pack = {
        "pack_type": "execution_pack",
        "pack_version": "1.0",
        "implementation_pack": implementation_pack,
        "test_pack": test_pack,
        "risk_pack": [{"id": "RISK-001", "summary": "Auth regression", "level": "low"}],
        "handoff_strategy": "sequential",
    }

    (run_dir / "implementation_pack.json").write_text(
        json.dumps(implementation_pack, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "test_pack.json").write_text(
        json.dumps(test_pack, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "execution_pack.json").write_text(
        json.dumps(execution_pack, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "parsed_items": [{"id": "REQ-001", "description": "Support login"}],
                "review_results": [
                    {"id": "REQ-001", "issues": ["Clarify SSO provider"]}
                ],
                "tasks": [
                    {
                        "id": "TASK-001",
                        "title": "Implement login",
                        "requirement_ids": ["REQ-001"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    bundle_payload = {
        "bundle_id": f"bundle-{run_id}",
        "bundle_version": "1.0",
        "created_at": "2026-03-07T12:00:00+00:00",
        "status": "approved",
        "source_run_id": run_id,
        "artifacts": {
            "prd_review_report": {
                "artifact_type": "prd_review_report",
                "path": str(run_dir / "prd_review_report.md"),
            },
            "open_questions": {
                "artifact_type": "open_questions",
                "path": str(run_dir / "open_questions.md"),
            },
            "scope_boundary": {
                "artifact_type": "scope_boundary",
                "path": str(run_dir / "scope_boundary.md"),
            },
            "tech_design_draft": {
                "artifact_type": "tech_design_draft",
                "path": str(run_dir / "tech_design_draft.md"),
            },
            "test_checklist": {
                "artifact_type": "test_checklist",
                "path": str(run_dir / "test_checklist.md"),
            },
            "implementation_pack": {
                "artifact_type": "implementation_pack",
                "path": str(run_dir / "implementation_pack.json"),
            },
            "test_pack": {
                "artifact_type": "test_pack",
                "path": str(run_dir / "test_pack.json"),
            },
            "execution_pack": {
                "artifact_type": "execution_pack",
                "path": str(run_dir / "execution_pack.json"),
            },
        },
        "approval_history": [],
        "metadata": {
            "source_report_paths": {"report_json": str(run_dir / "report.json")}
        },
    }
    (run_dir / "delivery_bundle.json").write_text(
        json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return bundle_payload["bundle_id"]


@pytest.mark.asyncio
async def test_handoff_to_executor_creates_persisted_tasks_and_traceability(tmp_path):
    bundle_id = _write_approved_bundle_fixture(tmp_path)
    run_dir = tmp_path / "20260307T010206Z"

    result = await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )
    tasks_payload = json.loads(
        (run_dir / "execution_tasks.json").read_text(encoding="utf-8")
    )["tasks"]
    notifications = read_notification_records(run_dir)
    task_by_source = {item["source_pack_type"]: item for item in tasks_payload}

    assert "error" not in result
    assert result["status"] == "routed"
    assert result["task_count"] == 2
    assert (run_dir / "execution_tasks.json").exists()
    assert (run_dir / "traceability_map.json").exists()
    assert (run_dir / "codex_request.json").exists()
    assert (run_dir / "claude_code_request.json").exists()
    assert task_by_source["implementation_pack"]["metadata"]["adapter_artifacts"][
        "request_path"
    ].endswith("codex_request.json")
    assert task_by_source["test_pack"]["metadata"]["adapter_artifacts"][
        "request_path"
    ].endswith("claude_code_request.json")
    assert (
        task_by_source["implementation_pack"]["metadata"]["adapter_artifacts"][
            "request_type"
        ]
        == "codex.run_pack"
    )
    assert (
        task_by_source["test_pack"]["metadata"]["adapter_artifacts"]["request_type"]
        == "claude_code.run_pack"
    )
    assert Path(
        task_by_source["implementation_pack"]["metadata"]["adapter_artifacts"][
            "context_path"
        ]
    ).exists()
    assert Path(
        task_by_source["test_pack"]["metadata"]["adapter_artifacts"]["context_path"]
    ).exists()
    assert len(notifications) == 2
    assert {item["event_type"] for item in notifications} == {
        "executor_handoff_created"
    }
    assert {item["channel"] for item in notifications} == {"feishu", "wecom"}
    assert all(item["dispatch_status"] == "dispatched" for item in notifications)
    assert all(item["payload"]["dry_run"] is True for item in notifications)


@pytest.mark.asyncio
async def test_prepare_agent_handoff_returns_requests_for_all_supported_agents(
    tmp_path,
):
    bundle_id = _write_approved_bundle_fixture(tmp_path)
    run_dir = tmp_path / "20260307T010206Z"

    result = await mcp_server.prepare_agent_handoff(
        run_id="20260307T010206Z",
        agent="all",
        options={"outputs_root": str(tmp_path)},
    )

    by_agent = {item["agent"]: item for item in result["requests"]}

    assert "error" not in result
    assert result["bundle_id"] == bundle_id
    assert result["status"] == "prepared"
    assert result["request_count"] == 3
    assert {item["agent"] for item in result["requests"]} == {
        "codex",
        "claude_code",
        "openclaw",
    }
    assert (run_dir / "codex_request.json").exists()
    assert (run_dir / "claude_code_request.json").exists()
    assert (run_dir / "openclaw_request.json").exists()
    assert by_agent["openclaw"]["source_pack_type"] == "implementation_pack"
    assert by_agent["openclaw"]["request"]["request_type"] == "openclaw.run_pack"
    assert by_agent["openclaw"]["request"]["input"]["verification_scope"]
    assert Path(by_agent["openclaw"]["context_path"]).exists()


@pytest.mark.asyncio
async def test_handoff_to_executor_returns_invalid_input_when_adapter_is_missing(
    tmp_path, monkeypatch
):
    bundle_id = _write_approved_bundle_fixture(tmp_path, run_id="20260307T010207Z")
    original_router = execution_service.ExecutorRouter

    monkeypatch.setattr(
        execution_service,
        "ExecutorRouter",
        lambda default_mode=execution_service.ExecutionMode.agent_assisted: (
            original_router(
                default_mode=default_mode,
                pack_specs=(("implementation_pack", "unknown_adapter"),),
            )
        ),
    )

    result = await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    assert result["error"]["code"] == "invalid_input"
    assert "unknown_adapter" in result["error"]["message"]
    assert not (tmp_path / "20260307T010207Z" / "execution_tasks.json").exists()


@pytest.mark.asyncio
async def test_get_execution_status_returns_bundle_tasks_and_task_lookup(tmp_path):
    bundle_id = _write_approved_bundle_fixture(tmp_path)
    run_dir = tmp_path / "20260307T010206Z"
    await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    by_bundle = mcp_server.get_execution_status(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )
    by_task = mcp_server.get_execution_status(
        task_id="bundle-20260307T010206Z:implementation_pack",
        options={"outputs_root": str(tmp_path)},
    )

    assert by_bundle["task_count"] == 2
    assert by_bundle["tasks"][0]["bundle_id"] == bundle_id
    assert by_bundle["tasks"][0]["metadata"]["adapter_artifacts"]["request_path"]
    assert by_task["task"]["task_id"] == "bundle-20260307T010206Z:implementation_pack"
    assert (
        by_task["task"]["metadata"]["adapter_artifacts"]["request_type"]
        == "codex.run_pack"
    )
    assert by_task["traceability"]["counts"]["total"] == 1
    assert (run_dir / "execution_tasks.json").exists()


@pytest.mark.asyncio
async def test_get_traceability_supports_bundle_and_requirement_queries(tmp_path):
    bundle_id = _write_approved_bundle_fixture(tmp_path)
    await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    by_bundle = mcp_server.get_traceability(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )
    by_requirement = mcp_server.get_traceability(
        requirement_id="REQ-001", options={"outputs_root": str(tmp_path)}
    )

    assert by_bundle["bundle_id"] == bundle_id
    assert by_bundle["counts"]["full"] >= 1
    assert by_requirement["count"] >= 1
    assert by_requirement["links"][0]["requirement_id"] == "REQ-001"


@pytest.mark.asyncio
async def test_update_execution_task_tool_persists_callback_writeback(tmp_path):
    run_id = "20260307T010208Z"
    bundle_id = _write_approved_bundle_fixture(tmp_path, run_id=run_id)
    run_dir = tmp_path / run_id
    await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    assigned = mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="assigned",
        actor="executor-gateway",
        assigned_to="codex-worker-1",
        detail="claim accepted",
        artifact_paths={"claim_receipt": str(run_dir / "claim.json")},
        options={"outputs_root": str(tmp_path)},
    )
    in_progress = mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="in_progress",
        actor="codex-worker-1",
        detail="patch in progress",
        options={"outputs_root": str(tmp_path)},
    )

    snapshot_payload = json.loads(
        (run_dir / "status_snapshot.json").read_text(encoding="utf-8")
    )
    trace_payload = json.loads((run_dir / "run_trace.json").read_text(encoding="utf-8"))

    assert "error" not in assigned
    assert "error" not in in_progress
    assert assigned["task"]["status"] == "assigned"
    assert assigned["task"]["assigned_to"] == "codex-worker-1"
    assert assigned["task"]["metadata"]["artifact_paths"]["claim_receipt"].endswith(
        "claim.json"
    )
    assert in_progress["task"]["status"] == "in_progress"
    assert snapshot_payload["execution"]["counts"]["in_progress"] == 1
    assert (
        snapshot_payload["execution"]["last_task_id"]
        == f"{bundle_id}:implementation_pack"
    )
    assert trace_payload["execution_updates"][-1]["to_status"] == "in_progress"
    assert trace_payload["execution_updates"][-1]["actor"] == "codex-worker-1"


@pytest.mark.asyncio
async def test_list_execution_tasks_tool_supports_status_filter(tmp_path):
    run_id = "20260307T010209Z"
    bundle_id = _write_approved_bundle_fixture(tmp_path, run_id=run_id)
    await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )
    mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="assigned",
        actor="executor-gateway",
        assigned_to="codex-worker-1",
        options={"outputs_root": str(tmp_path)},
    )

    by_bundle = mcp_server.list_execution_tasks(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )
    assigned_only = mcp_server.list_execution_tasks(
        status="assigned", options={"outputs_root": str(tmp_path)}
    )
    pending_only = mcp_server.list_execution_tasks(
        status="pending", options={"outputs_root": str(tmp_path)}
    )

    assert by_bundle["count"] == 2
    assert assigned_only["count"] == 1
    assert assigned_only["tasks"][0]["task_id"] == f"{bundle_id}:implementation_pack"
    assert pending_only["count"] == 1
    assert pending_only["tasks"][0]["task_id"] == f"{bundle_id}:test_pack"


def _write_draft_notification_bundle_fixture(
    tmp_path, run_id: str = "20260307T010210Z"
) -> tuple[str, Path]:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    bundle_id = f"bundle-{run_id}"
    bundle_payload = {
        "bundle_id": bundle_id,
        "bundle_version": "1.0",
        "created_at": "2026-03-07T12:00:00+00:00",
        "status": "draft",
        "source_run_id": run_id,
        "artifacts": {
            "prd_review_report": {
                "artifact_type": "prd_review_report",
                "path": str(run_dir / "prd_review_report.md"),
            },
            "open_questions": {
                "artifact_type": "open_questions",
                "path": str(run_dir / "open_questions.md"),
            },
            "scope_boundary": {
                "artifact_type": "scope_boundary",
                "path": str(run_dir / "scope_boundary.md"),
            },
            "tech_design_draft": {
                "artifact_type": "tech_design_draft",
                "path": str(run_dir / "tech_design_draft.md"),
            },
            "test_checklist": {
                "artifact_type": "test_checklist",
                "path": str(run_dir / "test_checklist.md"),
            },
            "implementation_pack": {
                "artifact_type": "implementation_pack",
                "path": str(run_dir / "implementation_pack.json"),
            },
            "test_pack": {
                "artifact_type": "test_pack",
                "path": str(run_dir / "test_pack.json"),
            },
            "execution_pack": {
                "artifact_type": "execution_pack",
                "path": str(run_dir / "execution_pack.json"),
            },
        },
        "approval_history": [],
        "metadata": {},
    }
    (run_dir / "delivery_bundle.json").write_text(
        json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return bundle_id, run_dir


def test_approve_handoff_writes_notifications_for_review_follow_up_states(tmp_path):
    bundle_id, run_dir = _write_draft_notification_bundle_fixture(tmp_path)

    need_more_info = mcp_server.approve_handoff(
        bundle_id=bundle_id,
        action="need_more_info",
        reviewer="alice",
        comment="Missing owner for OAuth onboarding",
        options={"outputs_root": str(tmp_path)},
    )
    blocked = mcp_server.approve_handoff(
        bundle_id=bundle_id,
        action="block_by_risk",
        reviewer="alice",
        comment="Critical auth regression risk",
        options={"outputs_root": str(tmp_path)},
    )

    notifications = read_notification_records(run_dir)

    assert "error" not in need_more_info
    assert "error" not in blocked
    assert len(notifications) == 4
    approval_notifications = [
        item for item in notifications if item["event_type"] == "approval_requested"
    ]
    blocked_notifications = [
        item for item in notifications if item["event_type"] == "blocked_by_risk"
    ]
    assert len(approval_notifications) == 2
    assert len(blocked_notifications) == 2
    assert {item["channel"] for item in approval_notifications} == {"feishu", "wecom"}
    assert {item["channel"] for item in blocked_notifications} == {"feishu", "wecom"}
    assert approval_notifications[0]["metadata"]["tool_name"] == "approve_handoff"
    assert {item["summary"] for item in approval_notifications} == {
        "Missing owner for OAuth onboarding"
    }
    assert {item["summary"] for item in blocked_notifications} == {
        "Critical auth regression risk"
    }


@pytest.mark.asyncio
async def test_update_execution_task_completed_writes_notification_record(tmp_path):
    run_id = "20260307T010211Z"
    bundle_id = _write_approved_bundle_fixture(tmp_path, run_id=run_id)
    run_dir = tmp_path / run_id
    await mcp_server.handoff_to_executor(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )

    mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="assigned",
        actor="executor-gateway",
        assigned_to="codex-worker-1",
        options={"outputs_root": str(tmp_path)},
    )
    mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="in_progress",
        actor="codex-worker-1",
        detail="working through auth changes",
        options={"outputs_root": str(tmp_path)},
    )
    completed = mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="completed",
        actor="codex-worker-1",
        result_summary="implemented and validated",
        options={"outputs_root": str(tmp_path)},
    )

    notifications = read_notification_records(run_dir)

    assert "error" not in completed
    completion_notifications = [
        item for item in notifications if item["event_type"] == "execution_completed"
    ]
    assert len(completion_notifications) == 2
    assert {item["channel"] for item in completion_notifications} == {"feishu", "wecom"}
    assert {item["task_id"] for item in completion_notifications} == {
        f"{bundle_id}:implementation_pack"
    }
    assert {item["summary"] for item in completion_notifications} == {
        "implemented and validated"
    }
    assert all(item["payload"]["dry_run"] is True for item in completion_notifications)


def test_get_template_registry_lists_registered_templates() -> None:
    result = mcp_server.get_template_registry()

    assert "error" not in result
    assert result["count"] >= 1
    assert result["templates"]
    assert {
        "template_id",
        "template_type",
        "version",
        "description",
        "is_default",
        "status",
    } <= set(result["templates"][0])


def test_get_template_registry_supports_type_and_id_filters() -> None:
    by_type = mcp_server.get_template_registry(template_type="adapter_prompt")
    by_id = mcp_server.get_template_registry(template_id="review.parser")

    assert "error" not in by_type
    assert by_type["template_type"] == "adapter_prompt"
    assert by_type["count"] == 3
    assert {item["template_type"] for item in by_type["templates"]} == {
        "adapter_prompt"
    }
    assert "error" not in by_id
    assert by_id["count"] == 1
    assert by_id["templates"][0]["template_id"] == "review.parser"
    assert by_id["templates"][0]["version"] == "v1.1"


def test_get_template_registry_returns_not_found_for_unknown_type() -> None:
    result = mcp_server.get_template_registry(template_type="unknown_type")

    assert result["count"] == 0
    assert result["error"]["code"] == "not_found"
    assert "unknown template_type" in result["error"]["message"]


class _BrokenNotifier(BaseNotifier):
    channel = "broken"
    description = "Always fail during payload generation."

    def build_payload(self, event):
        raise RuntimeError(f"cannot render {event.event_type}")


def test_get_audit_events_filters_by_event_type_and_status(tmp_path):
    bundle_id, run_dir = _write_draft_notification_bundle_fixture(
        tmp_path, run_id="20260307T010212Z"
    )

    mcp_server.approve_handoff(
        bundle_id=bundle_id,
        action="block_by_risk",
        reviewer="alice",
        comment="Critical auth regression risk",
        options={"outputs_root": str(tmp_path)},
    )

    result = mcp_server.get_audit_events(
        run_id="20260307T010212Z",
        event_type="blocked_by_risk",
        status="dispatched",
        options={"outputs_root": str(tmp_path)},
    )

    assert "error" not in result
    assert result["count"] == 2
    assert {event["details"]["event_type"] for event in result["events"]} == {
        "blocked_by_risk"
    }
    assert {event["status"] for event in result["events"]} == {"dispatched"}


def test_retry_operation_tool_retries_failed_notification_dispatch(tmp_path):
    run_id = "20260307T010213Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    dispatch_notification(
        run_dir,
        notification_type="blocked_by_risk",
        title="Bundle blocked by risk",
        summary="Critical dependency risk remains unresolved.",
        run_id=run_id,
        bundle_id=f"bundle-{run_id}",
        notifiers=[_BrokenNotifier()],
        audit_context={"tool_name": "approve_handoff", "source": "mcp"},
    )

    result = mcp_server.retry_operation(
        run_id=run_id,
        operation="notification_dispatch",
        options={"outputs_root": str(tmp_path)},
    )
    notifications = read_notification_records(run_dir)

    assert "error" not in result
    assert result["before_status"] == "failed"
    assert result["after_status"] == "dispatched"
    assert result["notifications_retried"] == 1
    retried = [
        item for item in notifications if item["dispatch_status"] == "dispatched"
    ]
    assert len(retried) == 2
    assert {item["channel"] for item in retried} == {"feishu", "wecom"}


def test_retry_operation_tool_rejects_invalid_operation(tmp_path):
    run_id = "20260307T010214Z"
    (tmp_path / run_id).mkdir(parents=True)

    result = mcp_server.retry_operation(
        run_id=run_id,
        operation="approval",
        options={"outputs_root": str(tmp_path)},
    )

    assert result["error"]["code"] == "invalid_operation"
    assert "operation must be one of" in result["error"]["message"]


@pytest.mark.asyncio
async def test_review_prd_notion_source_requires_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("MARRDP_NOTION_TOKEN", raising=False)
    result = await mcp_server.review_prd(
        source="notion://page/0123456789abcdef0123456789abcdef",
        options={"outputs_root": str(tmp_path)},
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "MARRDP_NOTION_TOKEN" in result["error"]["message"]


@pytest.mark.asyncio
async def test_review_requirement_notion_source_requires_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MARRDP_NOTION_TOKEN", raising=False)

    result = await mcp_server.review_requirement(
        source="notion://page/0123456789abcdef0123456789abcdef",
        options={"outputs_root": str(tmp_path)},
    )

    assert result["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "MARRDP_NOTION_TOKEN" in result["error"]["message"]
