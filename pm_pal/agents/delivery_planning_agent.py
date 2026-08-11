"""Delivery planning node that enriches the base plan with execution skills."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..skills import get_skill_executor, get_skill_spec
from ..state import ReviewState, plan_from_state
from ..utils.trace import trace_start

_AGENT = "delivery_planning"
_IMPLEMENTATION_SKILL = "implementation.plan"
_TEST_PLAN_SKILL = "test.plan.generate"
_CODEX_PROMPT_SKILL = "codex.prompt.generate"
_CLAUDE_CODE_PROMPT_SKILL = "claude_code.prompt.generate"


def _empty_implementation_plan() -> dict[str, list[str]]:
    return {
        "implementation_steps": [],
        "target_modules": [],
        "constraints": [],
    }


def _empty_test_plan() -> dict[str, list[str]]:
    return {
        "test_scope": [],
        "edge_cases": [],
        "regression_focus": [],
    }


def _empty_prompt_handoff() -> dict[str, Any]:
    return {
        "agent_prompt": "",
        "recommended_execution_order": [],
        "non_goals": [],
        "validation_checklist": [],
    }


def _collect_acceptance_criteria(parsed_items: list[dict[str, Any]]) -> list[str]:
    acceptance_criteria: list[str] = []
    seen: set[str] = set()
    for item in parsed_items:
        for criterion in item.get("acceptance_criteria", []) or []:
            text = str(criterion).strip()
            if text and text not in seen:
                seen.add(text)
                acceptance_criteria.append(text)
    return acceptance_criteria


def _collect_prompt_constraints(
    implementation_plan: dict[str, Any],
    risks: list[dict[str, Any]],
) -> list[str]:
    constraints: list[str] = []
    seen: set[str] = set()

    for item in implementation_plan.get("constraints", []) or []:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            constraints.append(text)

    for risk in risks[:5]:
        risk_text = str(risk.get("description", "")).strip()
        mitigation = str(risk.get("mitigation", "")).strip()
        if risk_text:
            constraint = f"Account for delivery risk: {risk_text}"
            if mitigation:
                constraint = f"{constraint}. Mitigation: {mitigation}"
            if constraint not in seen:
                seen.add(constraint)
                constraints.append(constraint)

    return constraints


async def _execute_skill(
    executor, skill_name: str, payload: dict[str, Any], trace: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        output = await executor.execute(
            get_skill_spec(skill_name),
            payload,
            trace=trace,
        )
        return output.model_dump(mode="python"), None
    except Exception:
        return None, skill_name


async def run(state: ReviewState) -> ReviewState:
    parsed_items: list[dict[str, Any]] = list(state.get("parsed_items", []) or [])
    risks: list[dict[str, Any]] = list(state.get("risks", []) or [])
    plan = plan_from_state(state)
    tasks: list[dict[str, Any]] = list(plan.get("tasks", []) or [])
    trace: dict[str, Any] = dict(state.get("trace", {}))

    payload = {
        "structured_requirements": parsed_items,
        "tasks": tasks,
        "risks": risks,
    }
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    span = trace_start(_AGENT, model="none", input_chars=len(payload_json))

    implementation_plan = _empty_implementation_plan()
    test_plan = _empty_test_plan()
    codex_prompt_handoff = _empty_prompt_handoff()
    claude_code_prompt_handoff = _empty_prompt_handoff()
    degraded_skills: list[str] = []
    executor = get_skill_executor()

    implementation_result, test_result = await asyncio.gather(
        _execute_skill(executor, _IMPLEMENTATION_SKILL, payload, trace),
        _execute_skill(executor, _TEST_PLAN_SKILL, payload, trace),
    )
    implementation_output, implementation_error = implementation_result
    test_output, test_error = test_result

    if implementation_output is not None:
        implementation_plan = implementation_output
    if test_output is not None:
        test_plan = test_output
    if implementation_error:
        degraded_skills.append(implementation_error)
    if test_error:
        degraded_skills.append(test_error)

    prompt_payload = {
        "implementation_plan": implementation_plan,
        "test_plan": test_plan,
        "constraints": _collect_prompt_constraints(implementation_plan, risks),
        "acceptance_criteria": _collect_acceptance_criteria(parsed_items),
    }

    codex_result, claude_result = await asyncio.gather(
        _execute_skill(executor, _CODEX_PROMPT_SKILL, prompt_payload, trace),
        _execute_skill(executor, _CLAUDE_CODE_PROMPT_SKILL, prompt_payload, trace),
    )
    codex_output, codex_error = codex_result
    claude_code_output, claude_error = claude_result

    if codex_output is not None:
        codex_prompt_handoff = codex_output
    if claude_code_output is not None:
        claude_code_prompt_handoff = claude_code_output
    if codex_error:
        degraded_skills.append(codex_error)
    if claude_error:
        degraded_skills.append(claude_error)

    span.set_attr("implementation_skill", _IMPLEMENTATION_SKILL)
    span.set_attr("test_plan_skill", _TEST_PLAN_SKILL)
    span.set_attr("codex_prompt_skill", _CODEX_PROMPT_SKILL)
    span.set_attr("claude_code_prompt_skill", _CLAUDE_CODE_PROMPT_SKILL)
    span.set_attr("degraded_skills", degraded_skills)
    trace[_AGENT] = span.end(
        status="ok",
        output_chars=len(
            json.dumps(
                {
                    "implementation_plan": implementation_plan,
                    "test_plan": test_plan,
                    "codex_prompt_handoff": codex_prompt_handoff,
                    "claude_code_prompt_handoff": claude_code_prompt_handoff,
                },
                ensure_ascii=False,
            )
        ),
    )

    return {
        "implementation_plan": implementation_plan,
        "test_plan": test_plan,
        "codex_prompt_handoff": codex_prompt_handoff,
        "claude_code_prompt_handoff": claude_code_prompt_handoff,
        "trace": trace,
    }
