"""Planner agent node that generates a delivery plan from parsed items."""

from __future__ import annotations

import json
from typing import Any

from .structured_runner import run_structured_node
from ..prompts import PLANNER_SYSTEM_PROMPT, PLANNER_USER_PROMPT
from ..schemas import PlannerOutput, validate_planner_output
from ..state import ReviewState
from ..templates.registry import PLANNER_REVIEW_PROMPT
from ..utils.logging import get_logger
from ..utils.trace import trace_start

_AGENT = "planner"
log = get_logger(_AGENT)


async def run(state: ReviewState) -> ReviewState:
    """Produce a delivery plan from parsed requirement items."""

    parsed_items: list[dict] = state.get("parsed_items", [])
    trace: dict[str, Any] = dict(state.get("trace", {}))
    run_dir: str = state.get("run_dir", "")
    log.info("Planner started", extra={"node": _AGENT})

    if not parsed_items:
        span = trace_start(_AGENT, model="none", input_chars=0)
        trace[_AGENT] = span.end(
            status="error", error_message="parsed_items is empty - nothing to plan"
        )
        log.warning(
            "Planner completed with %s tasks and %s milestones",
            0,
            0,
            extra={"node": _AGENT},
        )
        return _empty_result(trace)

    items_json = json.dumps(parsed_items, ensure_ascii=False, indent=2)
    span = trace_start(_AGENT, input_chars=len(items_json))
    span.set_template(PLANNER_REVIEW_PROMPT)
    prompt = (
        f"{PLANNER_SYSTEM_PROMPT}\n\n"
        f"{PLANNER_USER_PROMPT.format(items_json=items_json)}"
    )

    result = await run_structured_node(
        agent_name=_AGENT,
        prompt=prompt,
        schema=PlannerOutput,
        validate_output=validate_planner_output,
        empty_output=lambda: PlannerOutput().model_dump(mode="python", by_alias=True),
        trace=trace,
        run_dir=run_dir,
        span=span,
        dump_by_alias=True,
    )

    tasks = result.output.get("tasks", [])
    milestones = result.output.get("milestones", [])
    log_fn = log.info if result.status == "ok" else log.warning
    log_fn(
        "Planner completed with %s tasks and %s milestones",
        len(tasks),
        len(milestones),
        extra={"node": _AGENT},
    )
    return {
        "plan": {
            "tasks": tasks,
            "milestones": milestones,
            "dependencies": result.output.get("dependencies", []),
            "estimation": result.output.get("estimation", {}),
        },
        "trace": result.trace,
    }


def _empty_result(trace: dict[str, Any]) -> ReviewState:
    return {
        "plan": {"tasks": [], "milestones": [], "dependencies": [], "estimation": {}},
        "trace": trace,
    }
