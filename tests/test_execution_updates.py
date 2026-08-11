from __future__ import annotations

import json

from pm_pal.execution import ExecutionMode, ExecutionTask, ExecutionTaskStatus
from pm_pal.service.execution_service import (
    append_execution_event,
    handoff_to_executor_for_mcp,
    list_execution_tasks_for_mcp,
    update_execution_task_for_mcp,
)


def test_append_execution_event_adds_log_entry_without_changing_status() -> None:
    task = ExecutionTask(
        task_id="bundle-1:implementation_pack",
        bundle_id="bundle-1",
        source_pack_type="implementation_pack",
        executor_type="codex",
        execution_mode=ExecutionMode.agent_assisted,
        status=ExecutionTaskStatus.pending,
        created_at="2026-03-08T02:03:04+00:00",
        updated_at="2026-03-08T02:03:04+00:00",
    )

    updated = append_execution_event(
        task, actor="poller", event_type="heartbeat", detail="still running"
    )

    assert updated.status == "pending"
    assert updated.execution_log[-1].event_type == "heartbeat"
    assert updated.execution_log[-1].detail == "still running"
    assert updated.execution_log[-1].actor == "poller"
    assert updated.updated_at != task.updated_at


def test_update_execution_task_for_mcp_persists_tasks_snapshot_and_trace(
    tmp_path, write_delivery_workspace
) -> None:
    bundle_id, run_dir = write_delivery_workspace(tmp_path, bundle_status="approved")
    handoff_to_executor_for_mcp(
        bundle_id=bundle_id, options={"outputs_root": str(tmp_path)}
    )
    task_id = f"{bundle_id}:implementation_pack"

    update_execution_task_for_mcp(
        task_id=task_id,
        status="assigned",
        actor="executor-gateway",
        assigned_to="codex-worker-1",
        detail="claim accepted",
        artifact_paths={"claim_receipt": str(run_dir / "claim.json")},
        options={"outputs_root": str(tmp_path)},
    )
    update_execution_task_for_mcp(
        task_id=task_id,
        status="in_progress",
        actor="codex-worker-1",
        detail="patching auth flow",
        options={"outputs_root": str(tmp_path)},
    )
    update_execution_task_for_mcp(
        task_id=task_id,
        status="waiting_review",
        actor="codex-worker-1",
        detail="checkpoint ready",
        artifact_paths={"diff": str(run_dir / "changes.diff")},
        options={"outputs_root": str(tmp_path)},
    )
    update_execution_task_for_mcp(
        task_id=task_id,
        status="in_progress",
        actor="reviewer",
        detail="resume after checkpoint",
        options={"outputs_root": str(tmp_path)},
    )
    completed = update_execution_task_for_mcp(
        task_id=task_id,
        status="completed",
        actor="codex-worker-1",
        result_summary="implemented and validated",
        artifact_paths={"result_report": str(run_dir / "result.md")},
        options={"outputs_root": str(tmp_path)},
    )

    tasks_payload = json.loads(
        (run_dir / "execution_tasks.json").read_text(encoding="utf-8")
    )["tasks"]
    updated_task = next(item for item in tasks_payload if item["task_id"] == task_id)
    status_snapshot = json.loads(
        (run_dir / "status_snapshot.json").read_text(encoding="utf-8")
    )
    trace_payload = json.loads((run_dir / "run_trace.json").read_text(encoding="utf-8"))
    listed = list_execution_tasks_for_mcp(
        bundle_id=bundle_id, status="completed", options={"outputs_root": str(tmp_path)}
    )

    assert completed["task"]["status"] == "completed"
    assert completed["task"]["result_summary"] == "implemented and validated"
    assert updated_task["metadata"]["artifact_paths"]["claim_receipt"].endswith(
        "claim.json"
    )
    assert updated_task["metadata"]["artifact_paths"]["diff"].endswith("changes.diff")
    assert updated_task["metadata"]["artifact_paths"]["result_report"].endswith(
        "result.md"
    )
    assert status_snapshot["bundle_status"] == "approved"
    assert status_snapshot["execution"]["counts"]["completed"] == 1
    assert status_snapshot["execution"]["last_task_id"] == task_id
    assert status_snapshot["execution"]["last_status"] == "completed"
    assert len(trace_payload["execution_updates"]) == 5
    assert trace_payload["execution_updates"][0]["from_status"] == "pending"
    assert trace_payload["execution_updates"][0]["to_status"] == "assigned"
    assert trace_payload["execution_updates"][-1]["to_status"] == "completed"
    assert listed["count"] == 1
    assert listed["tasks"][0]["task_id"] == task_id
