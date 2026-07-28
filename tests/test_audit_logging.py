from __future__ import annotations

from pathlib import Path

import pytest

from prd_pal.mcp_server import server as mcp_server
from prd_pal.monitoring import read_audit_events
from prd_pal.service import review_service


@pytest.mark.asyncio
async def test_review_prd_writes_review_and_bundle_generation_audit_events(
    tmp_path: Path,
    monkeypatch,
    sample_prd_text: str,
    sample_report_json: dict,
    write_report_files,
) -> None:
    source_path = tmp_path / "sample_prd.md"
    source_path.write_text(sample_prd_text, encoding="utf-8")
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
        assert isinstance(kwargs.get("canonical_review_request"), dict)

        resolved_run_id = run_id or fixed_run_id
        run_dir = tmp_path / resolved_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result = dict(sample_report_json)
        report_paths = write_report_files(run_dir, report_payload=result)
        return {
            "run_id": resolved_run_id,
            "run_dir": str(run_dir),
            "result": result,
            "report_paths": report_paths,
        }

    monkeypatch.setattr(review_service, "run_review", fake_run_review)

    result = await mcp_server.review_prd(
        source=str(source_path),
        options={
            "outputs_root": str(tmp_path),
            "client_metadata": {"session_id": "sess-1"},
        },
    )

    assert "error" not in result
    resolved_run_id = result["run_id"]
    events = read_audit_events(tmp_path / resolved_run_id)
    assert [event["operation"] for event in events] == ["review", "bundle_generation"]

    review_event, bundle_event = events
    assert review_event["run_id"] == resolved_run_id
    assert review_event["source"] == "mcp"
    assert review_event["client_metadata"]["session_id"] == "sess-1"
    assert review_event["details"]["tool_name"] == "review_prd"
    assert review_event["details"]["requirement_source"] == "source"
    assert review_event["details"]["selected_profile"] == "data_sensitive"
    assert (
        "selected data_sensitive"
        in review_event["details"]["profile_routing_reason"].lower()
    )
    assert review_event["details"]["memory_mode"] == "off"
    assert review_event["details"]["retrieved_memories"] == []
    assert review_event["details"]["rejected_memory_candidates"] == []
    assert review_event["details"]["memory_influence"] == {
        "findings": [],
        "clarification_questions": [],
        "open_questions": [],
    }
    assert review_event["retry"]["state"] == "not_needed"

    assert bundle_event["status"] == "ok"
    assert bundle_event["details"]["tool_name"] == "review_prd"
    assert bundle_event["details"]["component_statuses"]["bundle_builder"] == "ok"
    assert bundle_event["details"]["delivery_bundle_path"].endswith(
        "delivery_bundle.json"
    )


@pytest.mark.asyncio
async def test_workflow_tools_write_approval_handoff_and_execution_update_audit_events(
    tmp_path: Path, write_delivery_workspace
) -> None:
    bundle_id, run_dir = write_delivery_workspace(
        tmp_path,
        run_id="20260308T030405Z",
        bundle_status="draft",
        created_at="2026-03-08T03:04:05+00:00",
    )
    options = {
        "outputs_root": str(tmp_path),
        "client_metadata": {"request_id": "req-1"},
    }

    approval = mcp_server.approve_handoff(
        bundle_id=bundle_id,
        action="approve",
        reviewer="alice",
        comment="ship it",
        options=options,
    )
    handoff = await mcp_server.handoff_to_executor(bundle_id=bundle_id, options=options)
    update = mcp_server.update_execution_task(
        task_id=f"{bundle_id}:implementation_pack",
        status="assigned",
        actor="executor-gateway",
        assigned_to="codex-worker-1",
        detail="claimed",
        options=options,
    )

    assert "error" not in approval
    assert "error" not in handoff
    assert "error" not in update

    events = read_audit_events(run_dir)
    assert [event["operation"] for event in events] == [
        "approval",
        "handoff",
        "notification_dispatch",
        "notification_dispatch",
        "execution_update",
    ]

    approval_event = events[0]
    handoff_event = events[1]
    handoff_notification_events = events[2:4]
    update_event = events[4]
    assert approval_event["status"] == "approved"
    assert approval_event["actor"] == "alice"
    assert approval_event["client_metadata"]["request_id"] == "req-1"
    assert approval_event["details"]["action"] == "approve"

    assert handoff_event["status"] == "routed"
    assert handoff_event["details"]["tool_name"] == "handoff_to_executor"
    assert handoff_event["details"]["task_count"] == 2
    assert {event["status"] for event in handoff_notification_events} == {"dispatched"}
    assert {event["details"]["channel"] for event in handoff_notification_events} == {
        "feishu",
        "wecom",
    }
    assert {
        event["details"]["event_type"] for event in handoff_notification_events
    } == {"executor_handoff_created"}

    assert update_event["status"] == "assigned"
    assert update_event["actor"] == "executor-gateway"
    assert update_event["details"]["to_status"] == "assigned"
    assert update_event["details"]["assigned_to"] == "codex-worker-1"
    assert update_event["retry"]["state"] == "not_needed"


def test_block_by_risk_writes_notification_dispatch_audit_events(
    tmp_path: Path, write_delivery_workspace
) -> None:
    bundle_id, run_dir = write_delivery_workspace(
        tmp_path,
        run_id="20260308T040506Z",
        bundle_status="draft",
        created_at="2026-03-08T03:04:05+00:00",
    )

    result = mcp_server.approve_handoff(
        bundle_id=bundle_id,
        action="block_by_risk",
        reviewer="alice",
        comment="Critical auth regression risk",
        options={
            "outputs_root": str(tmp_path),
            "client_metadata": {"request_id": "req-risk-1"},
        },
    )

    assert "error" not in result
    events = read_audit_events(run_dir)
    assert [event["operation"] for event in events] == [
        "approval",
        "notification_dispatch",
        "notification_dispatch",
    ]
    approval_event = events[0]
    notification_events = events[1:]

    assert approval_event["status"] == "blocked_by_risk"
    assert approval_event["details"]["action"] == "block_by_risk"
    assert {event["status"] for event in notification_events} == {"dispatched"}
    assert {event["details"]["event_type"] for event in notification_events} == {
        "blocked_by_risk"
    }
    assert {event["details"]["channel"] for event in notification_events} == {
        "feishu",
        "wecom",
    }
    assert all(
        event["client_metadata"]["request_id"] == "req-risk-1"
        for event in notification_events
    )


def test_query_audit_events_filters_by_run_and_event_type(
    tmp_path: Path, write_delivery_workspace
) -> None:
    from prd_pal.monitoring import query_audit_events

    bundle_id, run_dir = write_delivery_workspace(
        tmp_path,
        run_id="20260308T040510Z",
        bundle_status="draft",
        created_at="2026-03-08T03:04:05+00:00",
    )
    options = {
        "outputs_root": str(tmp_path),
        "client_metadata": {"request_id": "req-audit-1"},
    }

    mcp_server.approve_handoff(
        bundle_id=bundle_id,
        action="block_by_risk",
        reviewer="alice",
        comment="Critical auth regression risk",
        options=options,
    )

    events = query_audit_events(
        tmp_path,
        run_id="20260308T040510Z",
        event_type="blocked_by_risk",
        status="dispatched",
    )

    assert len(events) == 2
    assert {event["details"]["event_type"] for event in events} == {"blocked_by_risk"}
    assert {event["status"] for event in events} == {"dispatched"}
