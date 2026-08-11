from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from pm_pal.memory import (
    MemoryRepository,
    MemoryService,
    retrieve_memories_async,
    retrieve_memories_with_diagnostics_async,
)
from pm_pal.workflow import _apply_review_context


def _normalized_requirement_payload(
    summary: str, *, risk_hints: list[str] | None = None
) -> dict:
    return {
        "summary": summary,
        "risk_hints": list(risk_hints or []),
        "dependency_hints": [],
        "modules": [],
        "in_scope": [],
    }


@pytest.mark.asyncio
async def test_retrieve_memories_returns_empty_when_no_relevant_memory(
    tmp_path,
) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()
    await service.save_memory(
        memory_type="team_rule",
        title="Legacy migration rule",
        summary="Temporary rule for 2024 migration.",
        content="This run only needs temporary migration handling for the 2024 cutover.",
        scope={
            "level": "team",
            "team_id": "other-team",
            "requirement_type": ["task_request"],
        },
        confidence=0.4,
        reuse_score=0.2,
    )

    hits = await retrieve_memories_async(
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        normalized_requirement=_normalized_requirement_payload(
            "Recruiter dashboard", risk_hints=["login"]
        ),
        memory_mode="assist",
    )

    assert hits == []


@pytest.mark.asyncio
async def test_retrieve_memories_prefers_same_project_hit(tmp_path) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()
    await service.save_memory(
        memory_type="risk_pattern",
        title="Phoenix export rollback pattern",
        summary="Phoenix export flows need rollback ownership.",
        content="Partner export changes in Phoenix need rollback ownership and compatibility checks.",
        scope={
            "level": "project",
            "project_id": "phoenix",
            "requirement_type": ["product_requirement"],
        },
        confidence=0.82,
        reuse_score=0.88,
        tags=["export", "rollback"],
    )
    await service.save_memory(
        memory_type="risk_pattern",
        title="Other team export rule",
        summary="Other export flow.",
        content="Another team had export work.",
        scope={
            "level": "team",
            "team_id": "other-team",
            "requirement_type": ["product_requirement"],
        },
        confidence=0.9,
        reuse_score=0.9,
        tags=["export"],
    )

    hits = await retrieve_memories_async(
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        normalized_requirement=_normalized_requirement_payload(
            "Phoenix export change", risk_hints=["rollback", "partner export"]
        ),
        memory_mode="assist",
    )

    assert hits
    assert hits[0].project_id == "phoenix"
    assert "same_project" in hits[0].reasons


@pytest.mark.asyncio
async def test_retrieve_memories_strict_mode_only_returns_team_rules(tmp_path) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()
    await service.save_memory(
        memory_type="team_rule",
        title="Platform team approval gate",
        summary="Platform export changes require rollback owner.",
        content="Platform export changes require rollback owner before approval.",
        scope={
            "level": "team",
            "team_id": "team-platform",
            "requirement_type": ["product_requirement"],
        },
        confidence=0.85,
        reuse_score=0.9,
        tags=["rollback"],
    )
    await service.save_memory(
        memory_type="risk_pattern",
        title="Platform export regression pattern",
        summary="Export regressions can break downstream consumers.",
        content="Export regressions can break downstream consumers.",
        scope={
            "level": "team",
            "team_id": "team-platform",
            "requirement_type": ["product_requirement"],
        },
        confidence=0.95,
        reuse_score=0.95,
        tags=["rollback"],
    )

    hits = await retrieve_memories_async(
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "phoenix",
            "requirement_type": "product_requirement",
        },
        normalized_requirement=_normalized_requirement_payload(
            "Export change", risk_hints=["rollback"]
        ),
        memory_mode="strict",
    )

    assert hits
    assert all(item.memory_type == "team_rule" for item in hits)
    assert "strict_prefers_team_rule" in hits[0].reasons


@pytest.mark.asyncio
async def test_retrieve_memories_rejects_weakly_related_memory(tmp_path) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()
    await service.save_memory(
        memory_type="review_case",
        title="Analytics dashboard note",
        summary="Dashboard copy update.",
        content="Minor text tweak for analytics dashboard with no risk or dependency overlap.",
        scope={"level": "global", "requirement_type": ["product_requirement"]},
        confidence=0.61,
        reuse_score=0.44,
        tags=["copy"],
    )

    hits = await retrieve_memories_async(
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "",
            "requirement_type": "product_requirement",
        },
        normalized_requirement=_normalized_requirement_payload(
            "Payment OAuth rollout", risk_hints=["oauth", "audit", "rollback"]
        ),
        memory_mode="assist",
    )

    assert hits == []


@pytest.mark.asyncio
async def test_retrieve_memories_reports_rejected_candidates_with_reasons(
    tmp_path,
) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()
    await service.save_memory(
        memory_type="review_case",
        title="Unrelated copy note",
        summary="Copy-only tweak.",
        content="Minor UI copy update with no overlap.",
        scope={"level": "global", "requirement_type": ["product_requirement"]},
        confidence=0.61,
        reuse_score=0.44,
        tags=["copy"],
    )

    diagnostics = await retrieve_memories_with_diagnostics_async(
        memory_service=service,
        canonical_review_request={
            "team_id": "team-platform",
            "project_id": "",
            "requirement_type": "product_requirement",
        },
        normalized_requirement=_normalized_requirement_payload(
            "Payment OAuth rollout", risk_hints=["oauth", "audit", "rollback"]
        ),
        memory_mode="assist",
    )

    assert diagnostics.selected == ()
    assert diagnostics.considered_count == 1
    assert diagnostics.rejected_candidates
    assert diagnostics.rejected_candidates[0].reason == "score_below_threshold"


def test_apply_review_context_records_memory_usage_fields() -> None:
    updated = _apply_review_context(
        {
            "parallel_review": {
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "title": "Security gate missing",
                        "evidence": [{"source": "review_memory", "ref": "memory:1"}],
                    }
                ],
                "open_questions": [
                    {"question": "Who owns approval gate?", "memory_refs": ["memory:1"]}
                ],
                "clarification": {
                    "questions": [
                        {
                            "id": "clarify-1",
                            "question": "Who approves export release?",
                            "finding_ids": ["finding-1"],
                        }
                    ]
                },
            },
            "parallel_review_meta": {},
            "trace": {},
        },
        {
            "normalized_requirement": {"summary": "x"},
            "memory_hits": [],
            "similar_reviews_referenced": [],
            "structured_memory_hits": [
                {
                    "memory_id": "memory:1",
                    "memory_type": "team_rule",
                    "title": "Platform rule",
                    "usage_note": "Verify against current PRD.",
                }
            ],
            "memory_mode": "assist",
            "rejected_memory_candidates": [
                {
                    "memory_id": "memory:2",
                    "title": "Legacy note",
                    "reason": "score_below_threshold",
                }
            ],
            "memory_usage_notes": ["Verify against current PRD."],
            "normalizer_cache_hit": False,
            "rag_enabled": False,
        },
    )

    assert updated["memory_mode"] == "assist"
    assert updated["memory_usage"]["retrieved_memory_ids"] == ["memory:1"]
    assert updated["memory_usage"]["retrieved_memories"] == [
        {"memory_id": "memory:1", "title": "Platform rule"}
    ]
    assert (
        updated["parallel_review_meta"]["rejected_memory_candidates"][0]["memory_id"]
        == "memory:2"
    )
    assert (
        updated["parallel_review_meta"]["memory_influence"]["findings"][0]["finding_id"]
        == "finding-1"
    )
    assert (
        updated["parallel_review_meta"]["memory_influence"]["clarification_questions"][
            0
        ]["id"]
        == "clarify-1"
    )
    assert updated["parallel_review"]["memory_influence"]["open_questions"][0][
        "memory_refs"
    ] == ["memory:1"]
    assert updated["parallel_review_meta"]["memory_usage_notes"] == [
        "Verify against current PRD."
    ]
