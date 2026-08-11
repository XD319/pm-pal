"""Requirement-review workflow with parser-driven parallel planner/risk fan-out."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from langgraph.graph import END, StateGraph

from .agents import (
    delivery_planning_agent,
    planner_agent,
    parser_agent,
    reporter_agent,
    reviewer_agent,
    risk_agent,
)
from .memory import MemoryService, retrieve_memories_with_diagnostics_async
from .review import decide_review_mode, run_parallel_review_async
from .state import ReviewState, plan_from_state
from .review.memory_store import FileBackedMemoryStore, NoopMemoryStore
from .review.normalizer_cache import (
    FileBackedNormalizerCache,
    InMemoryNormalizerCache,
    normalize_requirement_with_cache,
)
from .templates.registry import CLARIFY_PARSER_REVIEW_PROMPT, PARSER_REVIEW_PROMPT
from .utils.logging import get_logger
from .utils.trace import trace_start

_MAX_REVISION_ROUNDS = 2
_HIGH_RISK_THRESHOLD = 0.4
_PARALLEL_TRACE_KEY = "planner_risk_parallel"
_PARALLEL_REVIEW_META_KEY = "parallel-review_meta"
_ALLOWED_REVIEW_MODES = {"auto", "quick", "full", "single_review", "parallel_review"}
ProgressHook = Callable[[str, str, ReviewState], None]
log = get_logger("workflow")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def run_risk_analysis_from_review_state(state: ReviewState) -> ReviewState:
    return await risk_agent.run(state)


async def _clarify_node(state: ReviewState) -> ReviewState:
    clarify_state: ReviewState = dict(state)
    clarify_state["parser_prompt_version"] = CLARIFY_PARSER_REVIEW_PROMPT.version
    return await parser_agent.run(clarify_state)


def _parallel_start_node(state: ReviewState) -> ReviewState:
    trace: dict[str, Any] = dict(state.get("trace", {}))
    existing = trace.get(_PARALLEL_TRACE_KEY, {})
    round_no = int(state.get("revision_round", 0) or 0)
    trace[_PARALLEL_TRACE_KEY] = {
        **(existing if isinstance(existing, dict) else {}),
        "start": _utc_now_iso(),
        "status": "running",
        "round": round_no,
        "branches": ["planner", "risk"],
        "fan_out": "parser->planner+risk",
    }
    return {"trace": trace}


def _review_join_node(state: ReviewState) -> ReviewState:
    trace: dict[str, Any] = dict(state.get("trace", {}))
    plan = plan_from_state(state)
    planner_span = (
        trace.get("planner", {}) if isinstance(trace.get("planner"), dict) else {}
    )
    risk_span = trace.get("risk", {}) if isinstance(trace.get("risk"), dict) else {}
    start_span = (
        trace.get(_PARALLEL_TRACE_KEY, {})
        if isinstance(trace.get(_PARALLEL_TRACE_KEY), dict)
        else {}
    )

    planner_ready = isinstance(state.get("plan"), dict)
    risk_ready = isinstance(state.get("evidence"), dict)
    validation_errors: list[str] = []
    if not planner_ready:
        validation_errors.append("planner output missing state.plan")
    if not risk_ready:
        validation_errors.append("risk output missing state.evidence")

    planner_start = _parse_iso(planner_span.get("start"))
    planner_end = _parse_iso(planner_span.get("end"))
    risk_start = _parse_iso(risk_span.get("start"))
    risk_end = _parse_iso(risk_span.get("end"))
    parallel_start = _parse_iso(start_span.get("start")) or planner_start or risk_start
    parallel_end = max(
        [dt for dt in (planner_end, risk_end) if dt is not None], default=None
    )

    planner_duration_ms = int(planner_span.get("duration_ms", 0) or 0)
    risk_duration_ms = int(risk_span.get("duration_ms", 0) or 0)
    sum_step_time_ms = planner_duration_ms + risk_duration_ms
    parallel_wall_time_ms = (
        int((parallel_end - parallel_start).total_seconds() * 1000)
        if parallel_start and parallel_end
        else 0
    )
    overlap_ms = (
        int(
            (
                min(planner_end, risk_end) - max(planner_start, risk_start)
            ).total_seconds()
            * 1000
        )
        if planner_start and planner_end and risk_start and risk_end
        else 0
    )
    overlap_ms = max(0, overlap_ms)
    saved_ms = max(0, sum_step_time_ms - parallel_wall_time_ms)
    speedup_ratio = (
        round(sum_step_time_ms / parallel_wall_time_ms, 3)
        if parallel_wall_time_ms > 0
        else 1.0
    )

    trace[_PARALLEL_TRACE_KEY] = {
        **start_span,
        "end": parallel_end.isoformat() if parallel_end else _utc_now_iso(),
        "status": "ok" if not validation_errors else "error",
        "join_node": "review_join",
        "fan_in": "planner+risk->reviewer",
        "planner_ready": planner_ready,
        "risk_ready": risk_ready,
        "validation_errors": validation_errors,
        "parallel_wall_time_ms": parallel_wall_time_ms,
        "sum_step_time_ms": sum_step_time_ms,
        "overlap_ms": overlap_ms,
        "saved_ms": saved_ms,
        "speedup_ratio": speedup_ratio,
        "parallelized": True,
    }

    return {
        "tasks": plan.get("tasks", []),
        "milestones": plan.get("milestones", []),
        "dependencies": plan.get("dependencies", []),
        "estimation": plan.get("estimation", {}),
        "trace": trace,
    }


def _route_decider_node(state: ReviewState) -> ReviewState:
    trace: dict[str, Any] = dict(state.get("trace", {}))
    revision_round = int(state.get("revision_round", 0) or 0)
    ratio = float(state.get("high_risk_ratio", 0.0) or 0.0)

    should_loop = ratio > _HIGH_RISK_THRESHOLD and revision_round < _MAX_REVISION_ROUNDS
    if should_loop:
        next_round = revision_round + 1
        reason = (
            f"loop: high_risk_ratio={ratio:.3f} > {_HIGH_RISK_THRESHOLD:.2f} "
            f"and revision_round={revision_round} < {_MAX_REVISION_ROUNDS}"
        )
    elif ratio <= _HIGH_RISK_THRESHOLD:
        next_round = revision_round
        reason = f"finish: high_risk_ratio={ratio:.3f} <= {_HIGH_RISK_THRESHOLD:.2f}"
    else:
        next_round = revision_round
        reason = f"finish: revision_round={revision_round} reached limit {_MAX_REVISION_ROUNDS}"

    routing_event = {
        "round": next_round if should_loop else revision_round,
        "ratio": ratio,
        "reason": reason,
        "decision": "clarify" if should_loop else "reporter",
    }
    routing_rounds = list(trace.get("routing_rounds", []))
    routing_rounds.append(routing_event)
    trace["routing_rounds"] = routing_rounds
    trace["router"] = routing_event

    return {
        "revision_round": next_round if should_loop else revision_round,
        "routing_reason": reason,
        "parser_prompt_version": PARSER_REVIEW_PROMPT.version,
        "trace": trace,
    }


def _route_next(state: ReviewState) -> str:
    routing_reason = str(state.get("routing_reason", "") or "")
    if routing_reason.startswith("loop:"):
        return "clarify"
    return "reporter"


def _not_needed_clarification() -> dict[str, Any]:
    return {
        "triggered": False,
        "status": "not_needed",
        "questions": [],
        "answers_applied": [],
        "findings_updated": [],
    }


def _build_async_node(
    node_name: str,
    node_fn: Callable[[ReviewState], Awaitable[ReviewState]],
    progress_hook: ProgressHook | None,
) -> Callable[[ReviewState], Awaitable[ReviewState]]:
    async def _runner(state: ReviewState) -> ReviewState:
        started = perf_counter()
        log.info("node started", extra={"node": node_name})
        if progress_hook:
            progress_hook("start", node_name, state)
        try:
            update = await node_fn(state)
        except Exception:
            log.error("node failed", extra={"node": node_name}, exc_info=True)
            raise
        duration_ms = round((perf_counter() - started) * 1000)
        log.info(
            "node completed", extra={"node": node_name, "duration_ms": duration_ms}
        )
        if progress_hook:
            merged_state: ReviewState = dict(state)
            if isinstance(update, dict):
                merged_state.update(update)
            progress_hook("end", node_name, merged_state)
        return update

    return _runner


def _build_sync_node(
    node_name: str,
    node_fn: Callable[[ReviewState], ReviewState],
    progress_hook: ProgressHook | None,
) -> Callable[[ReviewState], ReviewState]:
    def _runner(state: ReviewState) -> ReviewState:
        started = perf_counter()
        log.info("node started", extra={"node": node_name})
        if progress_hook:
            progress_hook("start", node_name, state)
        try:
            update = node_fn(state)
        except Exception:
            log.error("node failed", extra={"node": node_name}, exc_info=True)
            raise
        duration_ms = round((perf_counter() - started) * 1000)
        log.info(
            "node completed", extra={"node": node_name, "duration_ms": duration_ms}
        )
        if progress_hook:
            merged_state: ReviewState = dict(state)
            if isinstance(update, dict):
                merged_state.update(update)
            progress_hook("end", node_name, merged_state)
        return update

    return _runner


def _resolve_requested_mode(state: ReviewState) -> str:
    mode = str(state.get("mode", "") or "").strip().lower()
    if mode in {"auto", "quick", "full"}:
        return mode

    override = str(state.get("review_mode_override", "") or "").strip().lower()
    legacy_map = {"single_review": "quick", "parallel_review": "full"}
    return legacy_map.get(
        override, override if override in {"auto", "quick", "full"} else "auto"
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_normalizer_cache(state: ReviewState):
    cache_config = dict(state.get("normalizer_cache_config", {}) or {})
    cache_path = str(
        cache_config.get("path") or os.getenv("NORMALIZER_CACHE_PATH", "")
    ).strip()
    if cache_path:
        return FileBackedNormalizerCache(cache_path)
    return InMemoryNormalizerCache()


def _resolve_memory_store(state: ReviewState):
    memory_config = dict(state.get("memory_config", {}) or {})
    enabled = memory_config.get("enabled")
    if enabled is None:
        enabled = _truthy(os.getenv("REVIEW_MEMORY_ENABLED", ""))
    storage_path = str(
        memory_config.get("path") or os.getenv("REVIEW_MEMORY_PATH", "")
    ).strip()
    seeds_dir = str(
        memory_config.get("seeds_dir") or os.getenv("REVIEW_MEMORY_SEEDS_DIR", "")
    ).strip()
    if not storage_path and enabled:
        run_dir = str(state.get("run_dir", "") or "").strip()
        if run_dir:
            storage_path = str(
                Path(run_dir).resolve().parent / "_memory" / "review_memory.json"
            )
    if not storage_path:
        return NoopMemoryStore()
    return FileBackedMemoryStore(storage_path, seeds_dir=seeds_dir or None)


async def _prepare_review_context(state: ReviewState) -> dict[str, Any]:
    requirement_doc = str(state.get("requirement_doc", "") or "")
    cache = _resolve_normalizer_cache(state)
    cache_result = normalize_requirement_with_cache(requirement_doc, cache=cache)
    memory_store = _resolve_memory_store(state)
    memory_hit_objects = memory_store.retrieve_similar(
        cache_result.requirement, limit=3
    )
    memory_hits = [item.to_dict() for item in memory_hit_objects]
    similar_reviews_referenced = [
        str(item.get("reference_id", "") or "").strip()
        for item in memory_hits
        if str(item.get("reference_id", "") or "").strip()
    ]
    memory_retrieval_config = dict(state.get("memory_retrieval_config", {}) or {})
    memory_mode = (
        str(memory_retrieval_config.get("mode", "off") or "off").strip().lower()
        or "off"
    )
    structured_memory_hits: list[dict[str, Any]] = []
    structured_memory_hits_objects: list[Any] = []
    memory_usage_notes: list[str] = []
    if memory_mode != "off":
        try:
            memory_service = MemoryService.from_db_path(
                memory_retrieval_config.get("db_path")
            )
            await memory_service.initialize()
            retrieval = await retrieve_memories_with_diagnostics_async(
                memory_service=memory_service,
                canonical_review_request=dict(
                    state.get("canonical_review_request", {}) or {}
                ),
                normalized_requirement=asdict(cache_result.requirement),
                memory_mode=memory_mode,
            )
            structured_memory_hits_objects = list(retrieval.selected)
            structured_memory_hits = [item.to_dict() for item in retrieval.selected]
            rejected_memory_candidates = [
                item.to_dict() for item in retrieval.rejected_candidates
            ]
            memory_usage_notes = [
                str(item.get("usage_note", "") or "").strip()
                for item in structured_memory_hits
                if str(item.get("usage_note", "") or "").strip()
            ]
        except Exception:
            structured_memory_hits = []
            structured_memory_hits_objects = []
            rejected_memory_candidates = []
            memory_usage_notes = []
            memory_mode = "off"
    else:
        rejected_memory_candidates = []
    return {
        "normalized_requirement_obj": cache_result.requirement,
        "normalized_requirement": asdict(cache_result.requirement),
        "memory_hits": memory_hits,
        "memory_hits_objects": memory_hit_objects,
        "similar_reviews_referenced": similar_reviews_referenced,
        "structured_memory_hits": structured_memory_hits,
        "structured_memory_hits_objects": structured_memory_hits_objects,
        "memory_mode": memory_mode,
        "rejected_memory_candidates": rejected_memory_candidates,
        "memory_usage_notes": memory_usage_notes,
        "normalizer_cache_hit": cache_result.cache_hit,
        "rag_enabled": not isinstance(memory_store, NoopMemoryStore),
    }


def _apply_review_context(update: ReviewState, context: dict[str, Any]) -> ReviewState:
    memory_hits = [
        dict(item)
        for item in context.get("memory_hits", []) or []
        if isinstance(item, dict)
    ]
    similar_reviews_referenced = [
        str(item)
        for item in context.get("similar_reviews_referenced", []) or []
        if str(item).strip()
    ]
    structured_memory_hits = [
        dict(item)
        for item in context.get("structured_memory_hits", []) or []
        if isinstance(item, dict)
    ]
    rejected_memory_candidates = [
        dict(item)
        for item in context.get("rejected_memory_candidates", []) or []
        if isinstance(item, dict)
    ]
    memory_usage_notes = [
        str(item)
        for item in context.get("memory_usage_notes", []) or []
        if str(item).strip()
    ]
    memory_mode = (
        str(context.get("memory_mode", "off") or "off").strip().lower() or "off"
    )
    update["normalized_requirement"] = dict(
        context.get("normalized_requirement", {}) or {}
    )
    update["memory_hits"] = memory_hits
    update["similar_reviews_referenced"] = similar_reviews_referenced
    update["structured_memory_hits"] = structured_memory_hits
    update["memory_mode"] = memory_mode
    update["rejected_memory_candidates"] = rejected_memory_candidates
    retrieved_memory_cards = _memory_cards(structured_memory_hits)
    update["memory_usage_notes"] = memory_usage_notes
    update["memory_usage"] = {
        "memory_mode": memory_mode,
        "retrieved_count": len(structured_memory_hits),
        "retrieved_memory_ids": [
            str(item.get("memory_id", "") or "").strip()
            for item in structured_memory_hits
            if str(item.get("memory_id", "") or "").strip()
        ],
        "retrieved_memories": retrieved_memory_cards,
        "rejected_candidates": rejected_memory_candidates,
        "usage_notes": memory_usage_notes,
    }
    update["normalizer_cache_hit"] = bool(context.get("normalizer_cache_hit", False))
    update["rag_enabled"] = bool(context.get("rag_enabled", False))

    meta = dict(update.get("parallel_review_meta", {}) or {})
    meta["memory_hits"] = memory_hits
    meta["memory_hit_count"] = len(memory_hits)
    meta["similar_reviews_referenced"] = similar_reviews_referenced
    meta["memory_mode"] = memory_mode
    meta["retrieved_memories"] = structured_memory_hits
    meta["retrieved_memory_cards"] = retrieved_memory_cards
    meta["rejected_memory_candidates"] = rejected_memory_candidates
    meta["memory_usage_notes"] = memory_usage_notes
    meta["memory_influence"] = _build_memory_influence(update, structured_memory_hits)
    meta["normalizer_cache_hit"] = bool(context.get("normalizer_cache_hit", False))
    meta["rag_enabled"] = bool(context.get("rag_enabled", False))
    update["parallel_review_meta"] = meta

    parallel_review = dict(update.get("parallel_review", {}) or {})
    if parallel_review:
        parallel_review["memory_hits"] = memory_hits
        parallel_review["similar_reviews_referenced"] = similar_reviews_referenced
        parallel_review["memory_mode"] = memory_mode
        parallel_review["retrieved_memories"] = structured_memory_hits
        parallel_review["rejected_memory_candidates"] = rejected_memory_candidates
        parallel_review["memory_usage_notes"] = memory_usage_notes
        parallel_review["memory_usage"] = dict(update["memory_usage"])
        parallel_review["memory_influence"] = dict(meta.get("memory_influence", {}))
        parallel_review["normalizer_cache_hit"] = bool(
            context.get("normalizer_cache_hit", False)
        )
        parallel_review["rag_enabled"] = bool(context.get("rag_enabled", False))
        update["parallel_review"] = parallel_review
    trace = dict(update.get("trace", {}) or {})
    if trace:
        trace[_PARALLEL_REVIEW_META_KEY] = dict(meta)
        update["trace"] = trace
    return update


def _memory_cards(structured_memory_hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for item in structured_memory_hits:
        memory_id = str(item.get("memory_id", "") or "").strip()
        title = str(item.get("title", "") or "").strip()
        if not memory_id and not title:
            continue
        cards.append({"memory_id": memory_id, "title": title})
    return cards


def _build_memory_influence(
    update: ReviewState, structured_memory_hits: list[dict[str, Any]]
) -> dict[str, Any]:
    known_memory_ids = {
        str(item.get("memory_id", "") or "").strip()
        for item in structured_memory_hits
        if str(item.get("memory_id", "") or "").strip()
    }
    findings = []
    open_questions = []
    clarification_questions = []
    parallel_review = dict(update.get("parallel_review", {}) or {})
    for item in parallel_review.get("findings", []) or []:
        if not isinstance(item, dict):
            continue
        refs = _extract_memory_refs(item.get("evidence"), known_memory_ids)
        if not refs:
            continue
        findings.append(
            {
                "finding_id": str(item.get("finding_id", "") or "").strip(),
                "title": str(item.get("title", "") or "").strip(),
                "memory_refs": refs,
            }
        )
    refs_by_finding_id = {
        str(item.get("finding_id", "") or "").strip(): list(
            item.get("memory_refs", []) or []
        )
        for item in findings
        if str(item.get("finding_id", "") or "").strip()
    }
    for item in parallel_review.get("open_questions", []) or []:
        if not isinstance(item, dict):
            continue
        refs = [
            str(ref).strip()
            for ref in item.get("memory_refs", []) or []
            if str(ref).strip()
        ]
        if not refs:
            continue
        open_questions.append(
            {
                "question": str(item.get("question", "") or "").strip(),
                "memory_refs": refs,
            }
        )
    clarification = parallel_review.get("clarification")
    if isinstance(clarification, dict):
        for item in clarification.get("questions", []) or []:
            if not isinstance(item, dict):
                continue
            refs = [
                str(ref).strip()
                for ref in item.get("memory_refs", []) or []
                if str(ref).strip()
            ]
            for finding_id in item.get("finding_ids", []) or []:
                refs.extend(refs_by_finding_id.get(str(finding_id).strip(), []))
            refs = _unique_strings(refs)
            if not refs:
                continue
            clarification_questions.append(
                {
                    "id": str(item.get("id", "") or "").strip(),
                    "question": str(item.get("question", "") or "").strip(),
                    "memory_refs": refs,
                }
            )
    return {
        "findings": findings,
        "open_questions": open_questions,
        "clarification_questions": clarification_questions,
    }


def _extract_memory_refs(evidence: Any, known_memory_ids: set[str]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    entries = evidence if isinstance(evidence, list) else []
    for item in entries:
        if not isinstance(item, dict):
            continue
        if str(item.get("source", "") or "").strip().lower() != "review_memory":
            continue
        ref = str(item.get("ref", "") or "").strip()
        if not ref or ref in seen:
            continue
        if known_memory_ids and ref not in known_memory_ids:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def _unique_strings(values: list[str]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def _resolve_review_profile_context(
    state: ReviewState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = dict(state.get("review_profile", {}) or {})
    pack = dict(state.get("review_profile_pack", {}) or {})
    return profile, pack


def _attach_profile_meta(meta: dict[str, Any], state: ReviewState) -> dict[str, Any]:
    profile, pack = _resolve_review_profile_context(state)
    if profile:
        meta["review_profile"] = profile
        meta["selected_profile"] = str(profile.get("selected_profile", "") or "")
        meta["profile_routing_reason"] = str(profile.get("reason", "") or "")
        meta["profile_routing_confidence"] = float(
            profile.get("confidence", 0.0) or 0.0
        )
        meta["secondary_profiles"] = list(profile.get("secondary_profiles", []) or [])
    if pack:
        meta["review_profile_pack"] = {
            "profile": str(pack.get("profile", "") or ""),
            "pack_path": str(pack.get("pack_path", "") or ""),
            "checklist_path": str(pack.get("checklist_path", "") or ""),
            "rules_path": str(pack.get("rules_path", "") or ""),
        }
    return meta


async def _reviewer_node(state: ReviewState) -> ReviewState:
    requested_mode = _resolve_requested_mode(state)
    review_context = await _prepare_review_context(state)
    decision = decide_review_mode(
        str(state.get("requirement_doc", "") or ""),
        requested_mode=requested_mode,
        normalized_requirement=review_context["normalized_requirement_obj"],
    )
    log.info("review 模式: %s", decision.selected_mode, extra={"node": "reviewer"})

    if decision.selected_mode == "skip":
        update = _build_skip_reviewer_response(
            state,
            decision=decision,
            override=str(state.get("review_mode_override", "") or "").strip().lower(),
        )
        findings_count = len(list(update.get("review_results", []) or []))
        log.info(
            "review 完成, %s 条 findings", findings_count, extra={"node": "reviewer"}
        )
        return _apply_review_context(update, review_context)
    if decision.selected_mode == "full":
        update = await _run_parallel_reviewer(
            state,
            decision=decision,
            override=str(state.get("review_mode_override", "") or "").strip().lower(),
            review_context=review_context,
        )
        findings_count = len(
            list(update.get("parallel_review", {}).get("findings", []) or [])
        )
        log.info(
            "review 完成, %s 条 findings", findings_count, extra={"node": "reviewer"}
        )
        return _apply_review_context(update, review_context)
    update = await _run_single_reviewer(
        state,
        decision=decision,
        override=str(state.get("review_mode_override", "") or "").strip().lower(),
    )
    findings_count = len(list(update.get("review_results", []) or []))
    log.info("review 完成, %s 条 findings", findings_count, extra={"node": "reviewer"})
    return _apply_review_context(update, review_context)


async def _run_single_reviewer(
    state: ReviewState, *, decision: Any, override: str
) -> ReviewState:
    update = await reviewer_agent.run(state)
    trace: dict[str, Any] = dict(update.get("trace", state.get("trace", {})) or {})
    review_results = list(update.get("review_results", []) or [])
    review_open_questions = _derive_single_review_open_questions(review_results)
    review_risk_items = _derive_single_review_risk_items(review_results)

    reviewer_trace = (
        trace.get("reviewer") if isinstance(trace.get("reviewer"), dict) else {}
    )
    selected_mode = "quick"
    meta = {
        "requested_mode": decision.requested_mode,
        "default_mode": decision.selected_mode,
        "selected_mode": selected_mode,
        "review_mode_override": override,
        "parallel_triggered": False,
        "reviewer_strategy": "single_reviewer",
        "review_mode": selected_mode,
        "partial_review": False,
        "manual_review_required": False,
        "manual_review_message": "",
        "gating": asdict(decision),
        "gating_reasons": list(decision.reasons),
        "reviewer_count": 1,
        "reviewers_completed": ["single_reviewer"],
        "reviewers_failed": [],
        "reviewers_used": ["single_reviewer"],
        "reviewers_skipped": [],
        "finding_count": len(review_results),
        "open_questions_count": len(review_open_questions),
        "risk_items_count": len(review_risk_items),
        "input_token_estimate": _estimate_tokens(
            int(reviewer_trace.get("input_chars", 0) or 0)
        ),
        "output_token_estimate": _estimate_tokens(
            int(reviewer_trace.get("output_chars", 0) or 0)
        ),
        "duration_ms": int(reviewer_trace.get("duration_ms", 0) or 0),
        "tool_calls": [],
        "reviewer_insights": [
            {
                "reviewer": "single_reviewer",
                "status": "completed",
                "summary": "Single reviewer completed quick triage.",
                "status_detail": "Quick review path does not emit structured reviewer evidence.",
                "ambiguity_type": "",
                "clarification_question": "",
                "notes": [],
            }
        ],
    }
    meta = _attach_profile_meta(meta, state)
    trace[_PARALLEL_REVIEW_META_KEY] = meta
    update["trace"] = trace
    update["mode"] = selected_mode
    update["review_mode"] = selected_mode
    update["review_open_questions"] = review_open_questions
    update["review_risk_items"] = review_risk_items
    update["review_tool_calls"] = []
    update["reviewer_insights"] = list(meta.get("reviewer_insights", []))
    update["clarification"] = _not_needed_clarification()
    update["review_clarification"] = _not_needed_clarification()
    update["parallel_review_meta"] = meta
    return update


def _build_skip_reviewer_response(
    state: ReviewState, *, decision: Any, override: str
) -> ReviewState:
    trace: dict[str, Any] = dict(state.get("trace", {}))
    message = "Manual review required because the submitted requirement is too sparse for automated triage."
    reviewer_trace = trace_start(
        "reviewer",
        model="none",
        input_chars=len(str(state.get("requirement_doc", "") or "")),
    )
    reviewer_trace.set_attr("review_mode", "skip")
    trace["reviewer"] = reviewer_trace.end(status="ok", output_chars=len(message))
    meta = {
        "requested_mode": decision.requested_mode,
        "default_mode": decision.selected_mode,
        "selected_mode": "skip",
        "review_mode_override": override,
        "parallel_triggered": False,
        "reviewer_strategy": "skip",
        "review_mode": "skip",
        "partial_review": True,
        "manual_review_required": True,
        "manual_review_message": message,
        "gating": asdict(decision),
        "gating_reasons": list(decision.reasons),
        "reviewer_count": 0,
        "reviewers_completed": [],
        "reviewers_failed": [],
        "reviewers_used": [],
        "reviewers_skipped": [],
        "finding_count": 0,
        "open_questions_count": 0,
        "risk_items_count": 0,
        "input_token_estimate": _estimate_tokens(
            len(str(state.get("requirement_doc", "") or ""))
        ),
        "output_token_estimate": _estimate_tokens(len(message)),
        "duration_ms": 0,
        "tool_calls": [],
        "reviewer_insights": [
            {
                "reviewer": "manual",
                "status": "skipped",
                "summary": message,
                "status_detail": "Automated reviewer skipped because the requirement was too sparse.",
                "ambiguity_type": "insufficient_requirement_context",
                "clarification_question": "Can you provide scope, scenarios, acceptance criteria, and impacted systems?",
                "notes": [],
            }
        ],
    }
    meta = _attach_profile_meta(meta, state)
    trace[_PARALLEL_REVIEW_META_KEY] = meta
    return {
        "review_results": [],
        "plan_review": {"coverage": "", "milestones": "", "estimation": ""},
        "high_risk_ratio": 0.0,
        "trace": trace,
        "mode": "skip",
        "review_mode": "skip",
        "parallel_review": {
            "review_mode": "skip",
            "gating": asdict(decision),
            "summary": {"overall_risk": "unknown", "in_scope": [], "out_of_scope": []},
            "findings": [],
            "risk_items": [],
            "open_questions": [],
            "conflicts": [],
            "reviewer_summaries": [meta["reviewer_insights"][0]],
            "tool_calls": [],
            "reviewers_used": [],
            "reviewers_skipped": [],
            "manual_review_required": True,
            "manual_review_message": message,
            "partial_review": True,
        },
        "review_open_questions": [
            {
                "question": "Provide a fuller requirement with scope, scenarios, and acceptance criteria.",
                "reviewers": ["manual"],
            }
        ],
        "review_risk_items": [],
        "review_tool_calls": [],
        "reviewer_insights": list(meta.get("reviewer_insights", [])),
        "clarification": _not_needed_clarification(),
        "review_clarification": _not_needed_clarification(),
        "partial_review": True,
        "parallel_review_meta": meta,
    }


async def _invoke_parallel_review_manager(
    requirement_doc: str, run_dir: str, decision: Any, review_context: dict[str, Any]
):
    kwargs = {
        "gating_decision": decision,
        "normalized_requirement": review_context["normalized_requirement_obj"],
        "memory_hits": list(review_context.get("memory_hits_objects", []) or []),
        "retrieved_memories": list(
            review_context.get("structured_memory_hits_objects", []) or []
        ),
        "memory_mode": str(review_context.get("memory_mode", "off") or "off"),
        "normalizer_cache_hit": bool(review_context.get("normalizer_cache_hit", False)),
        "rag_enabled": bool(review_context.get("rag_enabled", False)),
    }
    supported = set(inspect.signature(run_parallel_review_async).parameters)
    filtered_kwargs = {key: value for key, value in kwargs.items() if key in supported}
    return await run_parallel_review_async(requirement_doc, run_dir, **filtered_kwargs)


async def _run_parallel_reviewer(
    state: ReviewState, *, decision: Any, override: str, review_context: dict[str, Any]
) -> ReviewState:
    requirement_doc = str(state.get("requirement_doc", "") or "")
    run_dir = str(state.get("run_dir", "") or "")
    trace: dict[str, Any] = dict(state.get("trace", {}))
    span = trace_start("reviewer", model="none", input_chars=len(requirement_doc))
    span.set_attr("review_mode", "full")
    span.set_attr("reviewer_strategy", "asyncio.gather")
    started = perf_counter()

    parallel_result = await _invoke_parallel_review_manager(
        requirement_doc, run_dir, decision, review_context
    )
    aggregated = parallel_result.aggregated
    aggregated_meta = dict(aggregated.get("meta", {}) or {})
    selected_mode = str(
        aggregated.get("review_mode", aggregated_meta.get("review_mode", "full"))
        or "full"
    )
    partial_review = bool(
        aggregated.get("partial_review", aggregated_meta.get("partial_review", False))
    )
    reviewers_completed = list(
        aggregated.get(
            "reviewers_completed", aggregated_meta.get("reviewers_completed", [])
        )
        or []
    )
    reviewers_failed = list(
        aggregated.get("reviewers_failed", aggregated_meta.get("reviewers_failed", []))
        or []
    )
    reviewers_used = list(
        aggregated.get("reviewers_used", aggregated_meta.get("reviewers_used", []))
        or []
    )
    reviewers_skipped = list(
        aggregated.get(
            "reviewers_skipped", aggregated_meta.get("reviewers_skipped", [])
        )
        or []
    )
    manual_review_required = bool(
        aggregated.get(
            "manual_review_required",
            aggregated_meta.get("manual_review_required", False),
        )
    )
    manual_review_message = str(
        aggregated.get(
            "manual_review_message", aggregated_meta.get("manual_review_message", "")
        )
        or ""
    ).strip()
    review_results = _build_parallel_review_results(state, aggregated)
    plan_review = _build_parallel_plan_review(aggregated)
    review_open_questions = list(aggregated.get("open_questions", []) or [])
    review_risk_items = list(aggregated.get("risk_items", []) or [])
    review_tool_calls = list(aggregated.get("tool_calls", []) or [])
    reviewer_insights = list(aggregated.get("reviewer_summaries", []) or [])
    clarification = dict(
        aggregated.get("clarification", {}) or _not_needed_clarification()
    )
    findings = list(aggregated.get("findings", []) or [])

    output_chars = len(json.dumps(aggregated, ensure_ascii=False))
    span.set_attr("partial_review", partial_review)
    if manual_review_required:
        span.set_attr("manual_review_required", True)
    trace["reviewer"] = span.end(status="ok", output_chars=output_chars)
    meta = {
        "requested_mode": decision.requested_mode,
        "default_mode": decision.selected_mode,
        "selected_mode": selected_mode,
        "review_mode_override": override,
        "parallel_triggered": True,
        "reviewer_strategy": "asyncio.gather",
        "review_mode": selected_mode,
        "partial_review": partial_review,
        "manual_review_required": manual_review_required,
        "manual_review_message": manual_review_message,
        "gating": dict(aggregated.get("gating", asdict(decision))),
        "gating_reasons": list(
            aggregated_meta.get(
                "gating_reasons", aggregated.get("gating", {}).get("reasons", [])
            )
            or []
        ),
        "reviewer_count": int(
            aggregated.get("reviewer_count", len(reviewers_completed))
            or len(reviewers_completed)
        ),
        "reviewers_completed": reviewers_completed,
        "reviewers_failed": reviewers_failed,
        "reviewers_used": reviewers_used,
        "reviewers_skipped": reviewers_skipped,
        "finding_count": len(findings),
        "open_questions_count": len(review_open_questions),
        "risk_items_count": len(review_risk_items),
        "input_token_estimate": _estimate_tokens(len(requirement_doc)),
        "output_token_estimate": _estimate_tokens(output_chars),
        "duration_ms": round((perf_counter() - started) * 1000),
        "artifact_paths": dict((aggregated.get("artifacts") or {})),
        "tool_calls": list(aggregated.get("tool_calls", [])),
        "reviewer_insights": list(aggregated.get("reviewer_summaries", [])),
    }
    meta = _attach_profile_meta(meta, state)
    trace[_PARALLEL_REVIEW_META_KEY] = meta

    aggregated["meta"] = _attach_profile_meta(
        dict(aggregated.get("meta", {}) or {}), state
    )
    aggregated["review_profile"] = dict(state.get("review_profile", {}) or {})
    aggregated["review_profile_pack"] = dict(state.get("review_profile_pack", {}) or {})

    return {
        "review_results": review_results,
        "plan_review": plan_review,
        "high_risk_ratio": _compute_parallel_high_risk_ratio(review_results),
        "trace": trace,
        "mode": selected_mode,
        "review_mode": selected_mode,
        "parallel_review": aggregated,
        "review_open_questions": review_open_questions,
        "review_risk_items": review_risk_items,
        "review_tool_calls": review_tool_calls,
        "reviewer_insights": reviewer_insights,
        "clarification": clarification,
        "review_clarification": clarification,
        "partial_review": partial_review,
        "parallel_review_meta": meta,
    }


def _build_parallel_review_results(
    state: ReviewState, aggregated: dict[str, Any]
) -> list[dict[str, Any]]:
    parsed_items = list(state.get("parsed_items", []) or [])
    findings = list(aggregated.get("findings", []) or [])
    open_questions = list(aggregated.get("open_questions", []) or [])
    risk_items = list(aggregated.get("risk_items", []) or [])

    has_scope_gap = any(
        str(item.get("category", "")).lower() in {"scope", "acceptance"}
        and str(item.get("severity", "")).lower() == "high"
        for item in findings
    )
    has_testability_gap = any(
        str(item.get("category", "")).lower() == "testability"
        and str(item.get("severity", "")).lower() == "high"
        for item in findings
    )
    issue_lines = [
        f"{item.get('title', 'Finding')}: {item.get('description', item.get('detail', ''))}".strip()
        for item in findings[:2]
        if str(item.get("title", "") or "").strip()
    ]
    question_lines = [
        str(item.get("question", "") or "").strip()
        for item in open_questions[:2]
        if str(item.get("question", "") or "").strip()
    ]
    suggestions = "; ".join(
        str(item.get("mitigation", "") or item.get("detail", "")).strip()
        for item in risk_items[:2]
        if str(item.get("mitigation", "") or item.get("detail", "")).strip()
    )

    review_results: list[dict[str, Any]] = []
    for item in parsed_items:
        acceptance_criteria = list(item.get("acceptance_criteria", []) or [])
        is_testable = bool(acceptance_criteria) and not has_testability_gap
        is_clear = not has_scope_gap
        is_ambiguous = has_scope_gap and not acceptance_criteria
        issues = issue_lines + (question_lines if is_ambiguous else [])
        review_results.append(
            {
                "id": item.get("id", "unknown"),
                "is_clear": is_clear,
                "is_testable": is_testable,
                "is_ambiguous": is_ambiguous,
                "issues": issues,
                "suggestions": suggestions,
            }
        )
    return review_results


def _build_parallel_plan_review(aggregated: dict[str, Any]) -> dict[str, str]:
    summaries = {
        str(item.get("reviewer", "") or "").strip().lower(): str(
            item.get("summary", "") or ""
        ).strip()
        for item in list(aggregated.get("reviewer_summaries", []) or [])
        if isinstance(item, dict)
    }
    return {
        "coverage": " ".join(
            part
            for part in (summaries.get("product", ""), summaries.get("security", ""))
            if part
        ),
        "milestones": summaries.get("engineering", ""),
        "estimation": summaries.get("qa", ""),
    }


def _compute_parallel_high_risk_ratio(review_results: list[dict[str, Any]]) -> float:
    total = len(review_results)
    if total == 0:
        return 0.0
    high_count = 0
    for item in review_results:
        flags = sum(
            [
                not item.get("is_clear", True),
                not item.get("is_testable", True),
                item.get("is_ambiguous", False),
            ]
        )
        if flags >= 2:
            high_count += 1
    return high_count / total


def _derive_single_review_open_questions(
    review_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for item in review_results:
        issues = [
            str(issue).strip()
            for issue in list(item.get("issues", []) or [])
            if str(issue).strip()
        ]
        if item.get("is_ambiguous") or issues:
            question = str(
                item.get("description")
                or item.get("id")
                or "Clarify requirement intent"
            ).strip()
            if not question:
                question = "Clarify requirement intent"
            questions.append(
                {
                    "question": question,
                    "reviewers": ["single_reviewer"],
                    "issues": issues,
                }
            )
    return questions


def _derive_single_review_risk_items(
    review_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in review_results:
        flags = sum(
            [
                not item.get("is_clear", True),
                not item.get("is_testable", True),
                item.get("is_ambiguous", False),
            ]
        )
        if flags == 0:
            continue
        severity = "high" if flags >= 2 else "medium"
        issues = [
            str(issue).strip()
            for issue in list(item.get("issues", []) or [])
            if str(issue).strip()
        ]
        items.append(
            {
                "title": str(item.get("id", "Requirement review risk")),
                "detail": "; ".join(issues)
                or str(
                    item.get("suggestions", "")
                    or "Clarify the requirement before implementation."
                ),
                "severity": severity,
                "category": "review_quality",
                "reviewers": ["single_reviewer"],
            }
        )
    return items


def _estimate_tokens(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return max(1, round(char_count / 4))


def build_review_graph(progress_hook: ProgressHook | None = None):
    """Build and compile the review graph with fan-out / fan-in parallelism."""

    workflow = StateGraph(ReviewState)

    workflow.add_node(
        "parser", _build_async_node("parser", parser_agent.run, progress_hook)
    )
    workflow.add_node(
        "clarify", _build_async_node("clarify", _clarify_node, progress_hook)
    )
    workflow.add_node(
        "parallel_start",
        _build_sync_node("parallel_start", _parallel_start_node, progress_hook),
    )
    workflow.add_node(
        "planner", _build_async_node("planner", planner_agent.run, progress_hook)
    )
    workflow.add_node(
        "risk",
        _build_async_node("risk", run_risk_analysis_from_review_state, progress_hook),
    )
    workflow.add_node(
        "review_join", _build_sync_node("review_join", _review_join_node, progress_hook)
    )
    workflow.add_node(
        "delivery_planning",
        _build_async_node(
            "delivery_planning", delivery_planning_agent.run, progress_hook
        ),
    )
    workflow.add_node(
        "reviewer", _build_async_node("reviewer", _reviewer_node, progress_hook)
    )
    workflow.add_node(
        "route_decider",
        _build_sync_node("route_decider", _route_decider_node, progress_hook),
    )
    workflow.add_node(
        "reporter", _build_async_node("reporter", reporter_agent.run, progress_hook)
    )

    workflow.set_entry_point("parser")
    workflow.add_edge("parser", "parallel_start")
    workflow.add_edge("clarify", "parallel_start")
    workflow.add_edge("parallel_start", "planner")
    workflow.add_edge("parallel_start", "risk")
    workflow.add_edge("planner", "review_join")
    workflow.add_edge("risk", "review_join")
    workflow.add_edge("review_join", "delivery_planning")
    workflow.add_edge("delivery_planning", "reviewer")
    workflow.add_edge("reviewer", "route_decider")
    workflow.add_conditional_edges(
        "route_decider", _route_next, {"clarify": "clarify", "reporter": "reporter"}
    )
    workflow.add_edge("reporter", END)

    return workflow.compile()
