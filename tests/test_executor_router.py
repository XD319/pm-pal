from __future__ import annotations

import json

import pytest

from pm_pal.execution import (
    BundleNotApprovedError,
    ExecutionMode,
    ExecutionTask,
    ExecutorRouter,
)
from pm_pal.packs.delivery_bundle import (
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)


def _make_bundle(
    tmp_path, *, status: BundleStatus = BundleStatus.approved, high_risk: bool = False
) -> DeliveryBundle:
    run_dir = tmp_path / "20260307T120000Z"
    run_dir.mkdir()
    (run_dir / "implementation_pack.json").write_text(
        json.dumps(
            {"task_id": "PLAN-TASK-001", "title": "Implement login"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "test_pack.json").write_text(
        json.dumps(
            {"task_id": "PLAN-TASK-002", "title": "Test login"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "execution_pack.json").write_text(
        json.dumps(
            {"risk_pack": [{"id": "RISK-1", "level": "high" if high_risk else "low"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return DeliveryBundle(
        bundle_id="bundle-20260307T120000Z",
        created_at="2026-03-07T12:00:00+00:00",
        status=status,
        source_run_id="20260307T120000Z",
        artifacts=DeliveryArtifacts(
            prd_review_report=ArtifactRef(
                artifact_type="prd_review_report",
                path=str(run_dir / "prd_review_report.md"),
            ),
            open_questions=ArtifactRef(
                artifact_type="open_questions", path=str(run_dir / "open_questions.md")
            ),
            scope_boundary=ArtifactRef(
                artifact_type="scope_boundary", path=str(run_dir / "scope_boundary.md")
            ),
            tech_design_draft=ArtifactRef(
                artifact_type="tech_design_draft",
                path=str(run_dir / "tech_design_draft.md"),
            ),
            test_checklist=ArtifactRef(
                artifact_type="test_checklist", path=str(run_dir / "test_checklist.md")
            ),
            implementation_pack=ArtifactRef(
                artifact_type="implementation_pack",
                path=str(run_dir / "implementation_pack.json"),
            ),
            test_pack=ArtifactRef(
                artifact_type="test_pack", path=str(run_dir / "test_pack.json")
            ),
            execution_pack=ArtifactRef(
                artifact_type="execution_pack",
                path=str(run_dir / "execution_pack.json"),
            ),
        ),
        metadata={},
    )


def test_executor_router_builds_tasks_for_approved_bundle(tmp_path) -> None:
    bundle = _make_bundle(tmp_path)
    tasks = ExecutorRouter(default_mode=ExecutionMode.agent_auto).route(bundle)
    task_by_source = {task.source_pack_type: task for task in tasks}

    assert len(tasks) == 2
    assert isinstance(tasks[0], ExecutionTask)
    assert set(task_by_source) == {"implementation_pack", "test_pack"}
    assert {task.executor_type for task in tasks} == {"codex", "claude_code"}
    assert {task.execution_mode for task in tasks} == {"agent_auto"}
    assert (
        task_by_source["implementation_pack"]
        .metadata["artifact_path"]
        .endswith("implementation_pack.json")
    )
    assert (
        task_by_source["implementation_pack"].metadata["plan_task_id"]
        == "PLAN-TASK-001"
    )
    assert task_by_source["test_pack"].metadata["plan_task_id"] == "PLAN-TASK-002"


def test_executor_router_rejects_non_approved_bundle(tmp_path) -> None:
    bundle = _make_bundle(tmp_path, status=BundleStatus.draft)

    with pytest.raises(BundleNotApprovedError):
        ExecutorRouter().route(bundle)


def test_executor_router_downgrades_high_risk_bundle_mode(tmp_path) -> None:
    bundle = _make_bundle(tmp_path, high_risk=True)
    tasks = ExecutorRouter(default_mode=ExecutionMode.agent_auto).route(bundle)

    assert {task.execution_mode for task in tasks} == {"agent_assisted"}


def test_executor_router_supports_custom_pack_specs(tmp_path) -> None:
    bundle = _make_bundle(tmp_path)
    tasks = ExecutorRouter(
        pack_specs=(("implementation_pack", "custom_executor"),)
    ).route(bundle)

    assert len(tasks) == 1
    assert tasks[0].source_pack_type == "implementation_pack"
    assert tasks[0].executor_type == "custom_executor"


def test_executor_router_reassign_updates_executor_and_mode(tmp_path) -> None:
    task = ExecutorRouter().route(_make_bundle(tmp_path))[0]
    updated = ExecutorRouter().reassign(
        task, new_executor="human", new_mode=ExecutionMode.human_only
    )

    assert updated.executor_type == "human"
    assert updated.execution_mode == "human_only"
    assert updated.execution_log[-1].event_type == "assigned"
