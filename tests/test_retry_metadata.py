from __future__ import annotations

import json

import pytest

from pm_pal.monitoring import (
    RetryOperationNotSupportedError,
    build_retry_metadata,
    read_audit_events,
    retry_metadata_for_status,
    retry_operation,
)
from pm_pal.notifications import (
    BaseNotifier,
    dispatch_notification,
    read_notification_records,
)
from pm_pal.service.review_service import build_delivery_handoff_outputs


class _BrokenNotifier(BaseNotifier):
    channel = "broken"
    description = "Always fail during payload generation."

    def build_payload(self, event):
        raise RuntimeError(f"cannot render {event.event_type}")


def test_retry_metadata_for_status_captures_failure_and_blocked_dependency() -> None:
    failed = retry_metadata_for_status(
        status="failed", non_blocking=True, error_message="renderer boom"
    )
    blocked = retry_metadata_for_status(
        status="skipped", non_blocking=True, error_message="execution_pack_path_missing"
    )
    exhausted = build_retry_metadata(
        retryable=True, attempt=3, max_attempts=3, last_error="still failing"
    )

    assert failed["retryable"] is True
    assert failed["state"] == "available"
    assert failed["recommended_action"] == "manual_retry"
    assert failed["last_error"] == "renderer boom"

    assert blocked["retryable"] is True
    assert blocked["state"] == "blocked"
    assert blocked["recommended_action"] == "retry_after_dependency_recovery"

    assert exhausted["state"] == "exhausted"
    assert exhausted["recommended_action"] == "escalate"


def test_build_delivery_handoff_outputs_persists_retry_metadata_for_non_blocking_steps(
    tmp_path,
) -> None:
    run_id = "20260308T040506Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    report_json_path = run_dir / "report.json"
    trace_path = run_dir / "run_trace.json"
    report_json_path.write_text(
        json.dumps({"trace": {}}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace_path.write_text(
        json.dumps({}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    run_output = {
        "run_dir": str(run_dir),
        "run_id": run_id,
        "report_paths": {
            "report_json": str(report_json_path),
            "run_trace": str(trace_path),
        },
        "result": {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Broken payload",
                    "acceptance_criteria": [],
                }
            ],
            "tasks": "invalid",
            "risks": [],
            "implementation_plan": {},
            "test_plan": {},
            "codex_prompt_handoff": {},
            "claude_code_prompt_handoff": {},
            "trace": {},
        },
    }

    artifact_paths = build_delivery_handoff_outputs(run_output)

    assert artifact_paths == {}
    trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))

    assert trace_payload["pack_builder"]["retry"]["retryable"] is True
    assert trace_payload["pack_builder"]["retry"]["state"] == "available"
    assert (
        trace_payload["pack_builder"]["packs"]["implementation_pack"]["retry"][
            "retryable"
        ]
        is True
    )

    assert trace_payload["handoff_renderer"]["retry"]["retryable"] is True
    assert trace_payload["handoff_renderer"]["retry"]["state"] == "blocked"

    assert trace_payload["bundle_builder"]["retry"]["retryable"] is True
    assert trace_payload["bundle_builder"]["retry"]["state"] == "available"


def test_retry_operation_replays_failed_artifact_generation(tmp_path) -> None:
    run_id = "20260308T040507Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    report_json_path = run_dir / "report.json"
    trace_path = run_dir / "run_trace.json"
    report_json_path.write_text(
        json.dumps({"trace": {}}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trace_path.write_text(
        json.dumps({}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    failed_run_output = {
        "run_dir": str(run_dir),
        "run_id": run_id,
        "report_paths": {
            "report_json": str(report_json_path),
            "run_trace": str(trace_path),
        },
        "result": {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Broken payload",
                    "acceptance_criteria": [],
                }
            ],
            "tasks": "invalid",
            "risks": [],
            "implementation_plan": {},
            "test_plan": {},
            "codex_prompt_handoff": {},
            "claude_code_prompt_handoff": {},
            "trace": {},
        },
    }
    build_delivery_handoff_outputs(failed_run_output)

    valid_report_payload = {
        "parsed_items": [
            {
                "id": "REQ-001",
                "description": "Support login",
                "acceptance_criteria": ["Login succeeds"],
            }
        ],
        "review_results": [
            {"id": "REQ-001", "description": "Support login", "issues": []}
        ],
        "tasks": [
            {
                "id": "TASK-001",
                "title": "Implement login",
                "requirement_ids": ["REQ-001"],
            }
        ],
        "risks": [
            {
                "id": "RISK-001",
                "description": "Auth regression",
                "impact": "low",
                "mitigation": "Run tests",
            }
        ],
        "implementation_plan": {
            "implementation_steps": ["Implement login safely"],
            "target_modules": ["backend.auth"],
            "constraints": [],
        },
        "test_plan": {
            "test_scope": ["Login API"],
            "edge_cases": ["Invalid credentials"],
            "regression_focus": ["Existing auth flow"],
        },
        "codex_prompt_handoff": {
            "agent_prompt": "Implement login safely.",
            "recommended_execution_order": ["Inspect auth flow", "Apply change"],
            "non_goals": [],
            "validation_checklist": ["Run login tests"],
        },
        "claude_code_prompt_handoff": {
            "agent_prompt": "Validate login changes.",
            "recommended_execution_order": ["Inspect changes", "Run tests"],
            "non_goals": [],
            "validation_checklist": ["Regression covered"],
        },
        "trace": {},
    }
    report_json_path.write_text(
        json.dumps(valid_report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = retry_operation(run_id, "artifact_generation", outputs_root=tmp_path)
    events = read_audit_events(run_dir)

    assert result["requested_operation"] == "artifact_generation"
    assert result["operation"] == "bundle_generation"
    assert result["before_status"] == "failed"
    assert result["after_status"] == "ok"
    assert result["after_error"] == ""
    assert result["artifacts"]["delivery_bundle"].endswith("delivery_bundle.json")
    assert (run_dir / "delivery_bundle.json").exists()
    assert events[-1]["operation"] == "retry_operation"
    assert events[-1]["status"] == "ok"
    assert events[-1]["details"]["target_operation"] == "bundle_generation"


def test_retry_operation_replays_failed_notification_dispatch(tmp_path) -> None:
    run_id = "20260308T040508Z"
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

    result = retry_operation(run_id, "notification_dispatch", outputs_root=tmp_path)
    notifications = read_notification_records(run_dir)
    audit_events = read_audit_events(run_dir)

    assert result["operation"] == "notification_dispatch"
    assert result["before_status"] == "failed"
    assert result["after_status"] == "dispatched"
    assert result["notifications_retried"] == 1
    retried = [
        item for item in notifications if item["dispatch_status"] == "dispatched"
    ]
    assert len(retried) == 2
    assert {item["channel"] for item in retried} == {"feishu", "wecom"}
    assert audit_events[-1]["operation"] == "retry_operation"
    assert audit_events[-1]["status"] == "dispatched"


def test_retry_operation_rejects_illegal_operation(tmp_path) -> None:
    run_id = "20260308T040509Z"
    (tmp_path / run_id).mkdir(parents=True)

    with pytest.raises(RetryOperationNotSupportedError):
        retry_operation(run_id, "approval", outputs_root=tmp_path)
