"""Reviewer agent node that evaluates requirements and plan coverage."""

from __future__ import annotations

import json
from typing import Any

from .structured_runner import run_structured_node
from ..prompts import REVIEWER_SYSTEM_PROMPT, REVIEWER_USER_PROMPT
from ..schemas import ReviewerOutput, validate_reviewer_output
from ..state import ReviewState, plan_from_state
from ..templates.registry import REVIEWER_REVIEW_PROMPT
from ..utils.logging import get_logger
from ..utils.trace import trace_start

_AGENT = "reviewer"
log = get_logger(_AGENT)
_VAGUE_TERMS = (
    "fast",
    "quick",
    "quickly",
    "timely",
    "reliable",
    "scalable",
    "user-friendly",
    "significant",
    "dramatic",
    "highly secure",
    "real time",
    "real-time",
    "fairness",
    "fair",
    "transparent",
    "transparency",
    "personalized",
    "improve outcomes",
    "from day one",
    "no manual intervention",
    "good",
    "better",
)


def _is_high_risk(result: dict[str, Any]) -> bool:
    flags = sum(
        [
            not result.get("is_clear", True),
            not result.get("is_testable", True),
            result.get("is_ambiguous", False),
        ]
    )
    return flags >= 2


def _compute_high_risk_ratio(review_results: list[dict[str, Any]]) -> float:
    total = len(review_results)
    if total == 0:
        return 0.0
    high_count = sum(1 for item in review_results if _is_high_risk(item))
    return high_count / total


def _compute_ambiguity_ratio(parsed_items: list[dict[str, Any]]) -> float:
    total = len(parsed_items)
    if total == 0:
        return 0.0

    high_count = 0
    for item in parsed_items:
        text_parts = [str(item.get("description", ""))]
        text_parts.extend(str(x) for x in item.get("acceptance_criteria", []))
        lowered = " ".join(text_parts).lower()
        if any(term in lowered for term in _VAGUE_TERMS):
            high_count += 1
    return high_count / total


async def run(state: ReviewState) -> ReviewState:
    """Review parsed items and cross-check them against the delivery plan."""

    parsed_items: list[dict] = state.get("parsed_items", [])
    trace: dict[str, Any] = dict(state.get("trace", {}))
    run_dir: str = state.get("run_dir", "")
    log.info("Reviewer started in quick mode", extra={"node": _AGENT})

    if not parsed_items:
        span = trace_start(_AGENT, model="none", input_chars=0)
        trace[_AGENT] = span.end(
            status="error", error_message="parsed_items is empty - nothing to review"
        )
        log.warning("Reviewer completed with %s findings", 0, extra={"node": _AGENT})
        return {
            "review_results": [],
            "plan_review": {},
            "high_risk_ratio": 0.0,
            "trace": trace,
        }

    items_json = json.dumps(parsed_items, ensure_ascii=False, indent=2)
    plan = plan_from_state(state)
    plan_data = {
        "tasks": plan.get("tasks", []),
        "milestones": plan.get("milestones", []),
        "estimation": plan.get("estimation", {}),
    }
    plan_json = json.dumps(plan_data, ensure_ascii=False, indent=2)

    input_chars = len(items_json) + len(plan_json)
    span = trace_start(_AGENT, input_chars=input_chars)
    span.set_template(REVIEWER_REVIEW_PROMPT)
    prompt = (
        f"{REVIEWER_SYSTEM_PROMPT}\n\n"
        f"{REVIEWER_USER_PROMPT.format(items_json=items_json, plan_json=plan_json)}"
    )

    result = await run_structured_node(
        agent_name=_AGENT,
        prompt=prompt,
        schema=ReviewerOutput,
        validate_output=validate_reviewer_output,
        empty_output=lambda: ReviewerOutput().model_dump(mode="python"),
        trace=trace,
        run_dir=run_dir,
        span=span,
    )

    review_results = result.output.get("review_results", [])
    high_risk_ratio = _compute_high_risk_ratio(review_results)
    ambiguity_ratio = _compute_ambiguity_ratio(parsed_items)
    high_risk_ratio = max(high_risk_ratio, ambiguity_ratio)
    reviewer_trace = result.trace.get(_AGENT)
    if isinstance(reviewer_trace, dict):
        reviewer_trace["high_risk_ratio"] = round(high_risk_ratio, 4)
        reviewer_trace["ambiguity_ratio"] = round(ambiguity_ratio, 4)
        reviewer_trace["revision_round"] = int(state.get("revision_round", 0) or 0)

    log_fn = log.info if result.status == "ok" else log.warning
    log_fn(
        "Reviewer completed with %s findings",
        len(review_results),
        extra={"node": _AGENT},
    )
    return {
        "review_results": review_results,
        "plan_review": result.output.get("plan_review", {}),
        "high_risk_ratio": high_risk_ratio,
        "trace": result.trace,
    }
