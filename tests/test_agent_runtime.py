from __future__ import annotations

import pytest

from pm_pal.agent_runtime.runtime import AgentRuntime, AgentRuntimeError


async def plan(prompt, schema):
    return {
        "summary": "Draft a PRD",
        "steps": [
            {"skill_id": "draft_prd", "purpose": "Write a PRD draft"},
            {
                "skill_id": "propose_external_write",
                "purpose": "Publish to Notion",
                "inputs": {"target_system": "notion"},
            },
        ],
    }


@pytest.mark.asyncio
async def test_runtime_uses_only_registered_skills_and_proposes_external_write():
    events = []

    async def emit(event):
        events.append(event)

    result = await AgentRuntime(planner=plan).run(
        request="Draft and publish a PRD",
        product={"name": "Mobile"},
        evidence=[{"id": "ev_1"}],
        context={},
        emit=emit,
    )
    assert result["artifacts"][0]["type"] == "document"
    assert result["proposals"][0]["target_system"] == "notion"
    assert any(event["type"] == "plan" for event in events)


@pytest.mark.asyncio
async def test_runtime_rejects_unregistered_model_skill():
    async def invalid_plan(prompt, schema):
        return {
            "summary": "Bad",
            "steps": [{"skill_id": "shell", "purpose": "Run arbitrary command"}],
        }

    async def emit(event):
        pass

    with pytest.raises(AgentRuntimeError, match="unregistered"):
        await AgentRuntime(planner=invalid_plan).run(
            request="x", product={}, evidence=[], context={}, emit=emit
        )
