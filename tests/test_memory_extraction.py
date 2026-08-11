from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from pm_pal import run_review as run_review_module
from pm_pal.memory import MemoryRepository, MemoryService
from pm_pal.memory.extraction import (
    extract_memory_candidates,
    process_review_memory_extraction_async,
)
from pm_pal.monitoring import read_audit_events


def _mock_review_result() -> dict:
    return {
        "parallel_review": {
            "summary": {
                "overall_risk": "high",
                "in_scope": ["partner export", "rollback"],
                "out_of_scope": [],
            },
            "findings": [
                {
                    "finding_id": "finding-export-1",
                    "title": "Missing export rollback owner",
                    "detail": "Cross-team export changes must define a rollback owner and release gate before release.",
                    "description": "Cross-team export changes must define a rollback owner and release gate before release.",
                    "severity": "high",
                    "category": "integration",
                    "source_reviewer": "engineering",
                    "reviewers": ["engineering", "qa"],
                    "suggested_action": "Require a named rollback owner plus downstream compatibility notes before approval.",
                    "clarification_applied": False,
                    "user_clarification": "",
                },
                {
                    "finding_id": "finding-export-2",
                    "title": "Schema drift can break downstream consumers",
                    "detail": "Schema drift can break downstream consumers unless compatibility and rollback are defined.",
                    "description": "Schema drift can break downstream consumers unless compatibility and rollback are defined.",
                    "severity": "high",
                    "category": "architecture",
                    "source_reviewer": "qa",
                    "reviewers": ["qa"],
                    "suggested_action": "Capture compatibility checks and negative-case coverage for downstream ingestion.",
                    "clarification_applied": True,
                    "user_clarification": "Rollback must restore the previous export schema and preserve partner ingestion behavior.",
                },
                {
                    "finding_id": "finding-weak-1",
                    "title": "Need more detail?",
                    "detail": "Maybe clarify this later?",
                    "description": "Maybe clarify this later?",
                    "severity": "medium",
                    "category": "scope",
                    "source_reviewer": "product",
                    "reviewers": ["product"],
                    "suggested_action": "",
                    "clarification_applied": False,
                    "user_clarification": "",
                },
            ],
            "risk_items": [
                {
                    "id": "risk-export-1",
                    "title": "Export contract regression",
                    "detail": "Changing export shape can regress analytics and partner ingestion.",
                    "mitigation": "Version the schema and define rollback ownership before rollout.",
                    "severity": "high",
                    "category": "integration",
                }
            ],
            "clarification": {
                "triggered": True,
                "status": "answered",
                "questions": [],
                "answers_applied": [
                    {
                        "question_id": "clarify-export-1",
                        "question": "What rollback behavior is required?",
                        "answer": "Rollback must restore the previous export contract and keep partner ingestion functional.",
                        "reviewer": "qa",
                    }
                ],
                "findings_updated": [],
            },
        }
    }


def test_extract_memory_candidates_prefers_team_rule_and_risk_pattern() -> None:
    candidates = extract_memory_candidates(
        run_id="20260414T010203Z",
        review_result=_mock_review_result(),
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        review_profile={"selected_profile": "approval_workflow"},
    )

    memory_types = [item.memory_type for item in candidates]
    assert "team_rule" in memory_types
    assert "risk_pattern" in memory_types
    assert any(
        item.scope.level == "team"
        for item in candidates
        if item.memory_type in {"team_rule", "risk_pattern"}
    )


@pytest.mark.asyncio
async def test_memory_extraction_gatekeeps_duplicates_and_persists_small_kept_set(
    tmp_path,
) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()
    await service.save_memory(
        memory_type="risk_pattern",
        title="Reusable risk pattern: Export contract regression",
        summary="Changing export shape can regress analytics and partner ingestion.",
        content="Changing export shape can regress analytics and partner ingestion. Version the schema and define rollback ownership before rollout.",
        scope={
            "level": "team",
            "team_id": "team-platform",
            "requirement_type": ["product_requirement"],
        },
        tags=["risk-pattern", "integration"],
    )

    outcome = await process_review_memory_extraction_async(
        run_id="20260414T010203Z",
        run_dir=str(tmp_path / "20260414T010203Z"),
        review_result=_mock_review_result(),
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        review_profile={"selected_profile": "approval_workflow"},
        audit_context={"actor": "review-service", "source": "mcp"},
        max_memories=2,
    )

    assert len(outcome.candidates) >= 4
    assert len(outcome.kept) == 2
    assert all(
        item.memory_type in {"team_rule", "risk_pattern"} for item in outcome.kept
    )
    rejection_reasons = {item.reason for item in outcome.rejected}
    assert any(reason.startswith("duplicate_existing") for reason in rejection_reasons)
    assert "too_vague" in rejection_reasons

    stored_team = await service.list_memory_by_scope(
        level="team", team_id="team-platform"
    )
    assert len(stored_team) == 3
    assert any(item.memory_type == "team_rule" for item in stored_team)


@pytest.mark.asyncio
async def test_memory_extraction_writes_candidate_rejection_and_persist_audit_events(
    tmp_path,
) -> None:
    run_id = "20260414T030405Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()

    outcome = await process_review_memory_extraction_async(
        run_id=run_id,
        run_dir=str(run_dir),
        review_result=_mock_review_result(),
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        review_profile={"selected_profile": "approval_workflow"},
        audit_context={"actor": "review-service", "source": "mcp"},
        max_memories=2,
    )

    events = read_audit_events(run_dir)
    operations = [event["operation"] for event in events]
    assert operations == ["memory_candidates", "memory_gatekeeping", "memory_persisted"]
    assert events[0]["details"]["candidate_count"] == len(outcome.candidates)
    assert events[1]["details"]["rejected_count"] == len(outcome.rejected)
    assert events[2]["details"]["persisted_count"] == len(outcome.persisted)


@pytest.mark.asyncio
async def test_run_review_attaches_memory_extraction_without_breaking_main_result(
    tmp_path, monkeypatch
) -> None:
    class _FakeGraph:
        async def ainvoke(self, initial_state):
            return {
                "final_report": "# Review Report\n\nDone.",
                "trace": {"reporter": {"status": "ok"}},
                "parallel_review": _mock_review_result()["parallel_review"],
                "parallel_review_meta": {
                    "selected_mode": "full",
                    "review_mode": "full",
                },
                "review_mode": "full",
                "mode": "full",
                "metrics": {"coverage_ratio": 0.9},
                "high_risk_ratio": 0.2,
                "revision_round": 0,
            }

    monkeypatch.setattr(
        run_review_module, "build_review_graph", lambda progress_hook=None: _FakeGraph()
    )

    output = await run_review_module.run_review(
        "# mock prd",
        run_id="20260414T050607Z",
        outputs_root=tmp_path,
        review_memory_extract_enabled=True,
        review_memory_db_path=tmp_path / "review_memory.sqlite3",
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        review_profile={"selected_profile": "approval_workflow"},
        audit_context={"actor": "review-service", "source": "mcp"},
    )

    assert output["result"]["memory_extraction"]["persisted_count"] >= 1
    assert (tmp_path / "20260414T050607Z" / "report.json").exists()
    audit_ops = [
        event["operation"] for event in read_audit_events(tmp_path / "20260414T050607Z")
    ]
    assert "memory_candidates" in audit_ops
    assert "memory_persisted" in audit_ops
