from __future__ import annotations

import json

from pm_pal.execution import ExecutionMode, ExecutionTask, TraceabilityMap
from pm_pal.packs.delivery_bundle import (
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)


def _bundle_with_report(tmp_path) -> DeliveryBundle:
    run_dir = tmp_path / "20260307T120001Z"
    run_dir.mkdir()
    report_payload = {
        "parsed_items": [{"id": "REQ-001", "description": "Login"}],
        "review_results": [{"id": "REQ-001", "issues": ["Clarify IdP"]}],
        "tasks": [
            {
                "id": "TASK-001",
                "title": "Implement login",
                "requirement_ids": ["REQ-001"],
            }
        ],
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "execution_pack.json").write_text("{}", encoding="utf-8")
    return DeliveryBundle(
        bundle_id="bundle-20260307T120001Z",
        created_at="2026-03-07T12:00:00+00:00",
        status=BundleStatus.approved,
        source_run_id="20260307T120001Z",
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
        metadata={"source_report_paths": {"report_json": str(run_dir / "report.json")}},
    )


def test_traceability_map_builds_and_queries_links(tmp_path) -> None:
    bundle = _bundle_with_report(tmp_path)
    tasks = [
        ExecutionTask(
            task_id="bundle-20260307T120001Z:implementation_pack",
            bundle_id=bundle.bundle_id,
            source_pack_type="implementation_pack",
            executor_type="codex",
            execution_mode=ExecutionMode.agent_assisted,
            status="pending",
            created_at="2026-03-07T12:00:00+00:00",
            updated_at="2026-03-07T12:00:00+00:00",
            metadata={"plan_task_id": "TASK-001"},
        )
    ]

    traceability = TraceabilityMap().build_from_bundle(bundle, tasks)

    requirement_links = traceability.query_by_requirement("REQ-001")
    task_links = traceability.query_by_execution_task(
        "bundle-20260307T120001Z:implementation_pack"
    )
    payload = traceability.to_dict()

    assert len(requirement_links) == 1
    assert requirement_links[0].link_type == "full"
    assert task_links[0].plan_task_id == "TASK-001"
    assert payload["counts"]["full"] == 1


def test_traceability_map_save_serializes_consistently(tmp_path) -> None:
    bundle = _bundle_with_report(tmp_path)
    traceability = TraceabilityMap().build_from_bundle(bundle, [])
    output_path = tmp_path / "traceability_map.json"

    traceability.save(output_path)

    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded["counts"]["total"] == len(traceability.links)
