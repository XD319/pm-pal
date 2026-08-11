"""Reusable risk-analysis subflow."""

from __future__ import annotations

import json
import os
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from ..agents.structured_runner import run_structured_node
from ..prompts import RISK_SYSTEM_PROMPT, RISK_USER_PROMPT
from ..schemas import RiskItem, RiskOutput, validate_risk_output
from ..skills import get_skill_executor, get_skill_spec
from ..state import PlanState, ReviewState, plan_from_state
from ..templates.registry import RISK_REVIEW_PROMPT
from ..utils.trace import trace_start

_SUBFLOW_ID = "risk_analysis.v1"
_EVIDENCE_NODE = "risk_analysis.evidence"
_GENERATION_NODE = "risk_analysis.generate"
_RISK_TOOL_ENV = "RISK_AGENT_ENABLE_CATALOG_TOOL"
_RISK_TOOL_TOP_K_ENV = "RISK_AGENT_CATALOG_TOP_K"


class RiskAnalysisContext(BaseModel):
    plan: dict[str, Any] = Field(default_factory=dict)
    run_dir: str = ""
    trace: dict[str, Any] = Field(default_factory=dict)
    node_path: str = "risk"
    subflow_id: str = _SUBFLOW_ID


class RiskAnalysisInput(BaseModel):
    structured_requirements: list[dict[str, Any]] = Field(default_factory=list)
    context: RiskAnalysisContext | None = None


class RiskEvidenceSummary(BaseModel):
    query: str = ""
    tool_status: str = "not_run"
    hit_count: int = 0
    top_ids: list[str] = Field(default_factory=list)
    snippets: list[str] = Field(default_factory=list)


class RiskToolAction(BaseModel):
    tool_name: str
    status: str
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: int = 0
    cache_hit: bool | None = None
    node_path: str = ""
    subflow_id: str = _SUBFLOW_ID


class RiskAnalysisOutput(BaseModel):
    risks: list[RiskItem] = Field(default_factory=list)
    evidence_summary: RiskEvidenceSummary = Field(default_factory=RiskEvidenceSummary)
    tool_actions: list[RiskToolAction] = Field(default_factory=list)


class RiskAnalysisState(TypedDict, total=False):
    structured_requirements: list[dict[str, Any]]
    context: dict[str, Any]
    evidence_hits: list[dict[str, Any]]
    evidence_summary: dict[str, Any]
    tool_actions: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    trace: dict[str, Any]
    trace_meta: dict[str, Any]


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _context_from_state(state: RiskAnalysisState) -> RiskAnalysisContext:
    return RiskAnalysisContext.model_validate(state.get("context") or {})


def _plan_data(context: RiskAnalysisContext) -> dict[str, Any]:
    plan = context.plan or {}
    return {
        "tasks": list(plan.get("tasks", []) or []),
        "milestones": list(plan.get("milestones", []) or []),
        "dependencies": list(plan.get("dependencies", []) or []),
        "estimation": dict(plan.get("estimation", {}) or {}),
    }


def _requirement_query(structured_requirements: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in structured_requirements[:10]:
        rid = str(item.get("id", "")).strip()
        desc = str(item.get("description", "")).strip()
        criteria = "; ".join(
            str(x).strip()
            for x in item.get("acceptance_criteria", [])
            if str(x).strip()
        )
        if rid or desc or criteria:
            lines.append(f"{rid}: {desc} | criteria: {criteria}".strip())
    return "\n".join(lines)


def _plan_query(
    structured_requirements: list[dict[str, Any]], plan: dict[str, Any]
) -> str:
    tasks = list(plan.get("tasks", []) or [])
    milestones = list(plan.get("milestones", []) or [])
    estimation = dict(plan.get("estimation", {}) or {})
    task_titles = "; ".join(
        str(t.get("title", "")) for t in tasks[:20] if t.get("title")
    )
    owners = ", ".join(
        sorted({str(t.get("owner", "")).strip() for t in tasks if t.get("owner")})
    )
    milestone_titles = "; ".join(
        str(m.get("title", "")) for m in milestones[:10] if m.get("title")
    )
    total_days = estimation.get("total_days", "")
    buffer_days = estimation.get("buffer_days", "")
    return (
        f"requirements:\n{_requirement_query(structured_requirements)}\n"
        f"tasks: {task_titles}\n"
        f"owners: {owners}\n"
        f"milestones: {milestone_titles}\n"
        f"total_days: {total_days}, buffer_days: {buffer_days}"
    )


def _evidence_for_prompt(hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "id": str(item.get("id", "")),
            "title": str(item.get("title", "")),
            "snippet": str(item.get("snippet", "")),
        }
        for item in hits
    ]


def _attach_fallback_evidence(
    risks: list[dict[str, Any]],
    evidence_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fallback_ids = [
        str(hit.get("id", "")) for hit in evidence_hits[:2] if hit.get("id")
    ]
    fallback_snippets = [
        str(hit.get("snippet", "")) for hit in evidence_hits[:2] if hit.get("snippet")
    ]
    for risk in risks:
        risk.setdefault("evidence_ids", [])
        risk.setdefault("evidence_snippets", [])
        if evidence_hits and not risk["evidence_ids"]:
            risk["evidence_ids"] = fallback_ids
        if evidence_hits and not risk["evidence_snippets"]:
            risk["evidence_snippets"] = fallback_snippets
    return risks


def _tool_action(
    *,
    status: str,
    query: str,
    duration_ms: int,
    hit_count: int,
    cache_hit: bool | None,
    node_path: str,
    top_ids: list[str],
) -> dict[str, Any]:
    return RiskToolAction(
        tool_name="risk_catalog.search",
        status=status,
        input_summary=query,
        output_summary=f"hits={hit_count}, ids={','.join(top_ids)}"
        if top_ids
        else f"hits={hit_count}",
        duration_ms=duration_ms,
        cache_hit=cache_hit,
        node_path=node_path,
    ).model_dump(mode="python")


async def _retrieve_evidence_node(state: RiskAnalysisState) -> RiskAnalysisState:
    context = _context_from_state(state)
    trace = dict(context.trace)
    plan = _plan_data(context)
    query = _plan_query(state.get("structured_requirements", []), plan)
    span = trace_start(_EVIDENCE_NODE, input_chars=len(query))
    span.set_attrs({"subflow_id": context.subflow_id, "node_path": _EVIDENCE_NODE})

    tool_enabled = _env_enabled(_RISK_TOOL_ENV, default=True)
    top_k_raw = os.getenv(_RISK_TOOL_TOP_K_ENV, "5")
    try:
        top_k = max(1, int(top_k_raw))
    except ValueError:
        top_k = 5

    evidence_hits: list[dict[str, Any]] = []
    tool_status = "degraded_disabled"
    tool_duration_ms = 0
    tool_error = ""
    cache_hit: bool | None = None
    top_ids: list[str] = []

    if tool_enabled:
        try:
            skill_output = await get_skill_executor().execute(
                get_skill_spec("risk_catalog.search"),
                {"query": query, "top_k": top_k},
                trace=trace,
            )
            evidence_hits = [
                item.model_dump(mode="python") for item in skill_output.hits
            ]
            skill_trace = trace.get("risk_catalog.search", {})
            tool_status = "ok"
            tool_duration_ms = int(skill_trace.get("duration_ms", 0) or 0)
            cache_hit = skill_trace.get("cache_hit")
            top_ids = [
                str(item.get("id", "")) for item in evidence_hits if item.get("id")
            ]
            if isinstance(skill_trace, dict):
                skill_trace["subflow_id"] = context.subflow_id
                skill_trace["node_path"] = f"{_EVIDENCE_NODE}.tool"
                trace["risk_catalog.search"] = skill_trace
        except Exception as exc:
            skill_trace = trace.get("risk_catalog.search", {})
            tool_status = "degraded_error"
            tool_duration_ms = int(skill_trace.get("duration_ms", 0) or 0)
            tool_error = str(exc)
            if isinstance(skill_trace, dict):
                skill_trace["subflow_id"] = context.subflow_id
                skill_trace["node_path"] = f"{_EVIDENCE_NODE}.tool"
                trace["risk_catalog.search"] = skill_trace

    evidence_summary = RiskEvidenceSummary(
        query=query,
        tool_status=tool_status,
        hit_count=len(evidence_hits),
        top_ids=top_ids,
        snippets=[
            str(item.get("snippet", ""))
            for item in evidence_hits[:2]
            if item.get("snippet")
        ],
    ).model_dump(mode="python")

    span.set_attrs(
        {
            "risk_catalog_tool_enabled": tool_enabled,
            "risk_catalog_tool_top_k": top_k,
            "risk_catalog_tool_status": tool_status,
            "risk_catalog_tool_duration_ms": tool_duration_ms,
            "risk_catalog_hits": len(evidence_hits),
            "risk_catalog_top_ids": top_ids,
        }
    )
    if tool_error:
        span.set_attr("risk_catalog_tool_error", tool_error)
    if not tool_enabled:
        span.set_attr(
            "risk_catalog_tool_reason",
            f"disabled by env {_RISK_TOOL_ENV}; evidence retrieval skipped",
        )

    trace[_EVIDENCE_NODE] = span.end(
        status="ok", output_chars=len(json.dumps(evidence_hits, ensure_ascii=False))
    )
    return {
        "evidence_hits": evidence_hits,
        "evidence_summary": evidence_summary,
        "tool_actions": [
            _tool_action(
                status=tool_status,
                query=query,
                duration_ms=tool_duration_ms,
                hit_count=len(evidence_hits),
                cache_hit=cache_hit,
                node_path=f"{_EVIDENCE_NODE}.tool",
                top_ids=top_ids,
            )
        ],
        "trace": trace,
        "trace_meta": {
            "tool_meta": {
                "risk_catalog_tool_enabled": tool_enabled,
                "risk_catalog_tool_top_k": top_k,
                "risk_catalog_tool_status": tool_status,
                "risk_catalog_tool_duration_ms": tool_duration_ms,
                "risk_catalog_hits": len(evidence_hits),
                "risk_catalog_top_ids": top_ids,
                **({"risk_catalog_tool_error": tool_error} if tool_error else {}),
                **(
                    {
                        "risk_catalog_tool_reason": f"disabled by env {_RISK_TOOL_ENV}; evidence retrieval skipped"
                    }
                    if not tool_enabled
                    else {}
                ),
            }
        },
    }


async def _generate_risks_node(state: RiskAnalysisState) -> RiskAnalysisState:
    context = _context_from_state(state)
    trace = dict(state.get("trace", {}))
    run_dir = context.run_dir
    structured_requirements = list(state.get("structured_requirements", []) or [])
    plan_data = _plan_data(context)
    payload_json = json.dumps(
        {"structured_requirements": structured_requirements, "plan": plan_data},
        ensure_ascii=False,
        indent=2,
    )
    span = trace_start(_GENERATION_NODE, input_chars=len(payload_json))
    span.set_attrs({"subflow_id": context.subflow_id, "node_path": _GENERATION_NODE})
    span.set_template(RISK_REVIEW_PROMPT)
    trace_meta = dict(state.get("trace_meta", {}))
    trace_meta["input_chars"] = len(payload_json)

    if not structured_requirements:
        trace[_GENERATION_NODE] = span.end(
            status="error",
            error_message="structured_requirements is empty - nothing to assess",
        )
        trace_meta.update(
            {
                "status": "error",
                "error_message": "structured_requirements is empty - nothing to assess",
                "model": "none",
                "raw_output_path": "",
                "output_chars": 0,
                "structured_mode": "not_run",
            }
        )
        return {"risks": [], "trace": trace, "trace_meta": trace_meta}

    evidence_hits = list(state.get("evidence_hits", []) or [])
    evidence_json = json.dumps(
        _evidence_for_prompt(evidence_hits), ensure_ascii=False, indent=2
    )
    prompt = (
        f"{RISK_SYSTEM_PROMPT}\n\n"
        f"{RISK_USER_PROMPT.format(requirements_json=json.dumps(structured_requirements, ensure_ascii=False, indent=2), plan_json=json.dumps(plan_data, ensure_ascii=False, indent=2), evidence_json=evidence_json)}"
    )

    result = await run_structured_node(
        agent_name="risk",
        trace_key=_GENERATION_NODE,
        raw_output_agent_name="risk",
        prompt=prompt,
        schema=RiskOutput,
        validate_output=validate_risk_output,
        empty_output=lambda: RiskOutput().model_dump(mode="python"),
        trace=trace,
        run_dir=run_dir,
        span=span,
        post_process=lambda output: {
            **output,
            "risks": _attach_fallback_evidence(output.get("risks", []), evidence_hits),
        },
    )
    trace_meta.update(
        {
            "status": result.status,
            "error_message": result.error_message,
            "model": result.model,
            "raw_output_path": result.raw_output_path,
            "output_chars": result.output_chars,
            "structured_mode": result.structured_mode,
        }
    )
    return {
        "risks": result.output.get("risks", []),
        "trace": result.trace,
        "trace_meta": trace_meta,
    }


def build_risk_analysis_subgraph():
    """Build and compile the reusable risk-analysis subgraph."""

    workflow = StateGraph(RiskAnalysisState)
    workflow.add_node("retrieve_evidence", _retrieve_evidence_node)
    workflow.add_node("generate_risks", _generate_risks_node)
    workflow.set_entry_point("retrieve_evidence")
    workflow.add_edge("retrieve_evidence", "generate_risks")
    workflow.add_edge("generate_risks", END)
    return workflow.compile()


async def run_risk_analysis_subflow(
    payload: RiskAnalysisInput | dict[str, Any],
) -> dict[str, Any]:
    """Invoke the compiled risk-analysis subflow and return execution details."""

    model = RiskAnalysisInput.model_validate(payload)
    graph = build_risk_analysis_subgraph()
    result = await graph.ainvoke(model.model_dump(mode="python"))
    if not isinstance(result, dict):
        raise ValueError("risk analysis subflow result must be an object")

    output = RiskAnalysisOutput(
        risks=result.get("risks", []),
        evidence_summary=result.get("evidence_summary", {}),
        tool_actions=result.get("tool_actions", []),
    )
    return {
        "output": output.model_dump(mode="python"),
        "trace": result.get("trace", {}),
        "trace_meta": result.get("trace_meta", {}),
    }


def _review_state_plan(plan: PlanState | dict[str, Any]) -> dict[str, Any]:
    return {
        "tasks": list(plan.get("tasks", []) or []),
        "milestones": list(plan.get("milestones", []) or []),
        "dependencies": list(plan.get("dependencies", []) or []),
        "estimation": dict(plan.get("estimation", {}) or {}),
    }


def _legacy_requirements_from_review_state(state: ReviewState) -> list[dict[str, Any]]:
    tasks = list(state.get("tasks", []) or [])
    by_requirement: dict[str, dict[str, Any]] = {}
    fallback_index = 0

    for task in tasks:
        task_title = str(task.get("title", "")).strip()
        task_owner = str(task.get("owner", "")).strip()
        criteria_parts = [
            part
            for part in (task_title, f"owner: {task_owner}" if task_owner else "")
            if part
        ]
        requirement_ids = [
            str(req_id).strip()
            for req_id in task.get("requirement_ids", [])
            if str(req_id).strip()
        ]

        if not requirement_ids:
            fallback_index += 1
            requirement_ids = [f"LEGACY-REQ-{fallback_index:03d}"]

        for requirement_id in requirement_ids:
            item = by_requirement.setdefault(
                requirement_id,
                {
                    "id": requirement_id,
                    "description": f"Derived from plan tasks for {requirement_id}",
                    "acceptance_criteria": [],
                },
            )
            if criteria_parts:
                criterion = " | ".join(criteria_parts)
                if criterion not in item["acceptance_criteria"]:
                    item["acceptance_criteria"].append(criterion)

    return list(by_requirement.values())


async def run_risk_analysis_from_review_state(state: ReviewState) -> ReviewState:
    """Adapter from the main ReviewState to the reusable risk subflow."""

    trace = dict(state.get("trace", {}))
    run_dir = state.get("run_dir", "")
    structured_requirements = list(state.get("parsed_items", []) or [])
    if not structured_requirements:
        structured_requirements = _legacy_requirements_from_review_state(state)
    plan = _review_state_plan(plan_from_state(state))
    input_json = json.dumps(
        {"structured_requirements": structured_requirements, "plan": plan},
        ensure_ascii=False,
        indent=2,
    )

    span = trace_start(
        "risk",
        input_chars=len(input_json),
        model="none" if not structured_requirements else "unknown",
    )
    span.set_attrs({"subflow_id": _SUBFLOW_ID, "node_path": "risk"})
    span.set_template(RISK_REVIEW_PROMPT)

    result = await run_risk_analysis_subflow(
        RiskAnalysisInput(
            structured_requirements=structured_requirements,
            context=RiskAnalysisContext(
                plan=plan,
                run_dir=run_dir,
                trace=trace,
                node_path="risk",
            ),
        )
    )

    next_trace = dict(result.get("trace", {}))
    trace_meta = dict(result.get("trace_meta", {}))
    if trace_meta.get("model"):
        span.model = str(trace_meta["model"])
    span.set_attrs(trace_meta.get("tool_meta", {}))
    if trace_meta.get("structured_mode"):
        span.set_attr("structured_mode", trace_meta["structured_mode"])
    span.set_attr("evidence_summary", result["output"].get("evidence_summary", {}))
    span.set_attr("tool_actions", result["output"].get("tool_actions", []))
    next_trace["risk"] = span.end(
        status=str(trace_meta.get("status", "ok")),
        output_chars=int(trace_meta.get("output_chars", 0) or 0),
        raw_output_path=str(trace_meta.get("raw_output_path", "") or ""),
        error_message=str(trace_meta.get("error_message", "") or ""),
    )

    return {
        "risks": result["output"].get("risks", []),
        "evidence": {
            "summary": result["output"].get("evidence_summary", {}),
            "tool_actions": result["output"].get("tool_actions", []),
        },
        "trace": next_trace,
    }
