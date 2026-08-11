"""LLM-backed delivery planning skills."""

from __future__ import annotations

import json
from typing import Any

from ..planning_prompts import (
    CLAUDE_CODE_PROMPT_SYSTEM_PROMPT,
    CLAUDE_CODE_PROMPT_USER_PROMPT,
    CODEX_PROMPT_SYSTEM_PROMPT,
    CODEX_PROMPT_USER_PROMPT,
    IMPLEMENTATION_PLAN_SYSTEM_PROMPT,
    IMPLEMENTATION_PLAN_USER_PROMPT,
    TEST_PLAN_SYSTEM_PROMPT,
    TEST_PLAN_USER_PROMPT,
)
from ..schemas.planning_skill_schema import (
    CodingAgentPromptOutput,
    DeliveryPlanningSkillInput,
    ImplementationPlanOutput,
    PromptGenerationSkillInput,
    QaPlanningOutput,
    validate_coding_agent_prompt_output,
    validate_implementation_plan_output,
    validate_test_plan_generate_output,
)
from ..utils.llm_structured_call import llm_structured_call
from .executor import SkillSpec


def _planning_payload_json(payload: DeliveryPlanningSkillInput) -> tuple[str, str, str]:
    requirements_json = json.dumps(
        payload.structured_requirements, ensure_ascii=False, indent=2
    )
    tasks_json = json.dumps(payload.tasks, ensure_ascii=False, indent=2)
    risks_json = json.dumps(payload.risks, ensure_ascii=False, indent=2)
    return requirements_json, tasks_json, risks_json


def _prompt_generation_payload_json(
    payload: PromptGenerationSkillInput,
) -> tuple[str, str, str, str]:
    implementation_plan_json = json.dumps(
        payload.implementation_plan, ensure_ascii=False, indent=2
    )
    test_plan_json = json.dumps(payload.test_plan, ensure_ascii=False, indent=2)
    constraints_json = json.dumps(payload.constraints, ensure_ascii=False, indent=2)
    acceptance_criteria_json = json.dumps(
        payload.acceptance_criteria, ensure_ascii=False, indent=2
    )
    return (
        implementation_plan_json,
        test_plan_json,
        constraints_json,
        acceptance_criteria_json,
    )


async def _implementation_plan(payload: DeliveryPlanningSkillInput) -> dict[str, Any]:
    requirements_json, tasks_json, risks_json = _planning_payload_json(payload)
    prompt = (
        f"{IMPLEMENTATION_PLAN_SYSTEM_PROMPT}\n\n"
        f"{IMPLEMENTATION_PLAN_USER_PROMPT.format(requirements_json=requirements_json, tasks_json=tasks_json, risks_json=risks_json)}"
    )
    parsed = await llm_structured_call(
        prompt=prompt,
        schema=ImplementationPlanOutput,
        metadata={"agent_name": "implementation.plan", "run_id": ""},
    )
    return validate_implementation_plan_output(parsed).model_dump(mode="python")


async def _generate_test_plan(payload: DeliveryPlanningSkillInput) -> dict[str, Any]:
    requirements_json, tasks_json, risks_json = _planning_payload_json(payload)
    prompt = (
        f"{TEST_PLAN_SYSTEM_PROMPT}\n\n"
        f"{TEST_PLAN_USER_PROMPT.format(requirements_json=requirements_json, tasks_json=tasks_json, risks_json=risks_json)}"
    )
    parsed = await llm_structured_call(
        prompt=prompt,
        schema=QaPlanningOutput,
        metadata={"agent_name": "test.plan.generate", "run_id": ""},
    )
    return validate_test_plan_generate_output(parsed).model_dump(mode="python")


async def _generate_codex_prompt(payload: PromptGenerationSkillInput) -> dict[str, Any]:
    (
        implementation_plan_json,
        test_plan_json,
        constraints_json,
        acceptance_criteria_json,
    ) = _prompt_generation_payload_json(payload)
    prompt = (
        f"{CODEX_PROMPT_SYSTEM_PROMPT}\n\n"
        f"{CODEX_PROMPT_USER_PROMPT.format(implementation_plan_json=implementation_plan_json, test_plan_json=test_plan_json, constraints_json=constraints_json, acceptance_criteria_json=acceptance_criteria_json)}"
    )
    parsed = await llm_structured_call(
        prompt=prompt,
        schema=CodingAgentPromptOutput,
        metadata={"agent_name": "codex.prompt.generate", "run_id": ""},
    )
    return validate_coding_agent_prompt_output(parsed).model_dump(mode="python")


async def _generate_claude_code_prompt(
    payload: PromptGenerationSkillInput,
) -> dict[str, Any]:
    (
        implementation_plan_json,
        test_plan_json,
        constraints_json,
        acceptance_criteria_json,
    ) = _prompt_generation_payload_json(payload)
    prompt = (
        f"{CLAUDE_CODE_PROMPT_SYSTEM_PROMPT}\n\n"
        f"{CLAUDE_CODE_PROMPT_USER_PROMPT.format(implementation_plan_json=implementation_plan_json, test_plan_json=test_plan_json, constraints_json=constraints_json, acceptance_criteria_json=acceptance_criteria_json)}"
    )
    parsed = await llm_structured_call(
        prompt=prompt,
        schema=CodingAgentPromptOutput,
        metadata={"agent_name": "claude_code.prompt.generate", "run_id": ""},
    )
    return validate_coding_agent_prompt_output(parsed).model_dump(mode="python")


IMPLEMENTATION_PLAN_SKILL = SkillSpec(
    name="implementation.plan",
    input_model=DeliveryPlanningSkillInput,
    output_model=ImplementationPlanOutput,
    handler=_implementation_plan,
    config_version="implementation.plan@v1",
    cache_ttl_sec=300,
    template_id="review.delivery_planning.implementation",
)


TEST_PLAN_GENERATE_SKILL = SkillSpec(
    name="test.plan.generate",
    input_model=DeliveryPlanningSkillInput,
    output_model=QaPlanningOutput,
    handler=_generate_test_plan,
    config_version="test.plan.generate@v1",
    cache_ttl_sec=300,
    template_id="review.delivery_planning.test_plan",
)


CODEX_PROMPT_GENERATE_SKILL = SkillSpec(
    name="codex.prompt.generate",
    input_model=PromptGenerationSkillInput,
    output_model=CodingAgentPromptOutput,
    handler=_generate_codex_prompt,
    config_version="codex.prompt.generate@v1",
    cache_ttl_sec=300,
    template_id="review.delivery_planning.codex_prompt",
)


CLAUDE_CODE_PROMPT_GENERATE_SKILL = SkillSpec(
    name="claude_code.prompt.generate",
    input_model=PromptGenerationSkillInput,
    output_model=CodingAgentPromptOutput,
    handler=_generate_claude_code_prompt,
    config_version="claude_code.prompt.generate@v1",
    cache_ttl_sec=300,
    template_id="review.delivery_planning.claude_code_prompt",
)
