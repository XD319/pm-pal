from __future__ import annotations

import pytest

pytest.importorskip("aiosqlite")

from pm_pal.memory import (
    MemoryRepository,
    MemoryScope,
    MemoryScopeLevel,
    MemoryService,
)


@pytest.mark.asyncio
async def test_memory_service_save_and_read_round_trip(tmp_path) -> None:
    repository = MemoryRepository(tmp_path / "review_memory.sqlite3")
    service = MemoryService(repository)
    await service.initialize()

    saved = await service.save_memory(
        memory_type="risk_pattern",
        title="Export schema drift risk",
        summary="Cross-team export changes need versioning and rollback notes.",
        content="When export payloads are consumed by other teams, define compatibility policy and rollback.",
        scope=MemoryScope(
            level=MemoryScopeLevel.team,
            team_id="team-platform",
            requirement_type=["integration", "export"],
        ),
        applicability={
            "summary": "Applies when requirements introduce partner-facing exports.",
            "conditions": ["downstream consumer exists"],
            "signals": ["schema change", "partner integration"],
        },
        evidence=[
            {
                "kind": "review_case",
                "reference": "seed:export-contract-hardening",
                "summary": "Past export PRD regressed downstream consumers.",
            }
        ],
        confidence=0.88,
        reuse_score=0.72,
        expiry_hint="Revisit after export platform standardization.",
        tags=["export", "schema"],
        do_not_overapply="Do not require full rollback plans for internal-only CSV drafts.",
        created_by="alice",
        actor="review-service",
    )

    memories = await service.list_memory_by_scope(level="team", team_id="team-platform")

    assert len(memories) == 1
    record = memories[0]
    assert record.memory_id == saved.memory_id
    assert record.memory_type == "risk_pattern"
    assert record.scope.requirement_type == ["export", "integration"]
    assert record.tags == ["export", "schema"]
    assert record.do_not_overapply.startswith("Do not require")

    audit_rows = await repository.list_audit_rows(saved.memory_id)
    assert audit_rows.ok is True
    assert audit_rows.value is not None
    assert len(audit_rows.value) == 1
    assert audit_rows.value[0]["actor"] == "review-service"


@pytest.mark.asyncio
async def test_memory_scope_isolation(tmp_path) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()

    await service.save_memory(
        memory_type="team_rule",
        title="Team-specific review gate",
        summary="Platform team wants explicit migration owner.",
        content="Platform team requires named migration ownership before approval.",
        scope={
            "level": "team",
            "team_id": "team-platform",
            "requirement_type": ["migration"],
        },
    )
    await service.save_memory(
        memory_type="clarification_fact",
        title="Project owner already agreed on fallback",
        summary="Project phoenix confirmed manual fallback is acceptable.",
        content="Fallback can be manual during the first rollout window.",
        scope={
            "level": "project",
            "project_id": "phoenix",
            "requirement_type": ["rollout"],
        },
    )
    await service.save_memory(
        memory_type="review_case",
        title="Global sensitive workflow case",
        summary="Sensitive workflows need auditability.",
        content="If customer data crosses service boundaries, require explicit audit logging.",
        scope={"level": "global", "requirement_type": ["privacy"]},
    )

    team_memories = await service.list_memory_by_scope(
        level="team", team_id="team-platform"
    )
    project_memories = await service.list_memory_by_scope(
        level="project", project_id="phoenix"
    )
    global_memories = await service.list_memory_by_scope(level="global")

    assert [item.title for item in team_memories] == ["Team-specific review gate"]
    assert [item.title for item in project_memories] == [
        "Project owner already agreed on fallback"
    ]
    assert [item.title for item in global_memories] == [
        "Global sensitive workflow case"
    ]


@pytest.mark.asyncio
async def test_memory_query_filters_by_team_project_and_requirement_type(
    tmp_path,
) -> None:
    service = MemoryService(MemoryRepository(tmp_path / "review_memory.sqlite3"))
    await service.initialize()

    await service.save_memory(
        memory_type="risk_pattern",
        title="Payments rollback risk",
        summary="Billing changes need rollback notes.",
        content="Rollback ownership is required for payment-path changes.",
        scope={
            "level": "team",
            "team_id": "payments",
            "requirement_type": ["billing", "rollout"],
        },
        tags=["billing"],
    )
    await service.save_memory(
        memory_type="review_case",
        title="Phoenix onboarding case",
        summary="Onboarding flows need support fallback.",
        content="Support fallback reduced launch risk during the last onboarding redesign.",
        scope={
            "level": "project",
            "project_id": "phoenix",
            "requirement_type": ["onboarding"],
        },
        tags=["support"],
    )
    await service.save_memory(
        memory_type="team_rule",
        title="Payments team ADR rule",
        summary="Payments team requires ADR links for settlement changes.",
        content="Settlement-affecting requirements must link the latest ADR.",
        scope={"level": "team", "team_id": "payments", "requirement_type": ["billing"]},
        tags=["adr"],
    )

    billing_memories = await service.find_memories(
        team_id="payments", requirement_type="billing"
    )
    phoenix_memories = await service.find_memories(project_id="phoenix")
    rollout_memories = await service.find_memories(requirement_type="rollout")

    assert {item.title for item in billing_memories} == {
        "Payments rollback risk",
        "Payments team ADR rule",
    }
    assert [item.title for item in phoenix_memories] == ["Phoenix onboarding case"]
    assert [item.title for item in rollout_memories] == ["Payments rollback risk"]
