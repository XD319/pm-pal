"""Selective rerun planner with artifact diff and cache reuse."""

from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from typing import Any

from pm_pal.service.impact_analysis_service import (
    WORKFLOW_NODES,
    analyze_affected_nodes_async,
)

_WORKFLOW_EDGES: dict[str, list[str]] = {
    "parser": ["planner", "risk"],
    "planner": ["delivery_planning"],
    "risk": ["reviewer"],
    "delivery_planning": ["reviewer"],
    "reviewer": ["reporter"],
    "reporter": [],
}


def _sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _extract_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "root"
    sections[current] = []
    for line in (markdown or "").splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def build_artifact_diff(prd_v1: str, prd_v2: str) -> dict[str, Any]:
    normalized_v1 = str(prd_v1 or "")
    normalized_v2 = str(prd_v2 or "")
    section_v1 = _extract_sections(normalized_v1)
    section_v2 = _extract_sections(normalized_v2)
    all_sections = sorted(set(section_v1) | set(section_v2))
    changed_sections = [
        name
        for name in all_sections
        if str(section_v1.get(name, "")) != str(section_v2.get(name, ""))
    ]
    unchanged_sections = [name for name in all_sections if name not in changed_sections]
    similarity = SequenceMatcher(None, normalized_v1, normalized_v2).ratio()
    return {
        "schema_version": "artifact_diff.v1",
        "baseline_checksum": _sha256(normalized_v1),
        "candidate_checksum": _sha256(normalized_v2),
        "change_ratio": round(1 - similarity, 6),
        "is_identical": normalized_v1 == normalized_v2,
        "changed_fields": changed_sections,
        "changed_sections": changed_sections,
        "section_stats": {
            "total": len(all_sections),
            "changed": len(changed_sections),
            "unchanged": len(unchanged_sections),
        },
        "sections": {
            "added": [name for name in all_sections if name not in section_v1],
            "removed": [name for name in all_sections if name not in section_v2],
            "modified": [
                name
                for name in changed_sections
                if name in section_v1 and name in section_v2
            ],
            "unchanged": unchanged_sections,
        },
    }


def _closure(nodes: list[str]) -> list[str]:
    visited: set[str] = set()
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(_WORKFLOW_EDGES.get(node, []))
    return [node for node in WORKFLOW_NODES if node in visited]


def _cache_source_for_node(cache: dict[str, Any], node: str) -> dict[str, Any]:
    if node == "parser":
        return {"parsed_items": cache.get("parsed_items", [])}
    if node == "planner":
        return {"plan": cache.get("plan", {}), "tasks": cache.get("tasks", [])}
    if node == "risk":
        return {"risks": cache.get("risks", []), "evidence": cache.get("evidence", {})}
    if node == "delivery_planning":
        return {
            "implementation_plan": cache.get("implementation_plan", {}),
            "test_plan": cache.get("test_plan", {}),
            "codex_prompt_handoff": cache.get("codex_prompt_handoff", {}),
            "claude_code_prompt_handoff": cache.get("claude_code_prompt_handoff", {}),
        }
    if node == "reviewer":
        return {
            "review_results": cache.get("review_results", []),
            "parallel_review": cache.get("parallel_review", {}),
            "parallel_review_meta": cache.get("parallel_review_meta", {}),
            "review_open_questions": cache.get("review_open_questions", []),
            "review_risk_items": cache.get("review_risk_items", []),
        }
    if node == "reporter":
        return {"final_report": cache.get("final_report", "")}
    return {}


async def build_rerun_plan_async(
    *,
    prd_v1: str,
    prd_v2: str,
    cached_node_outputs: dict[str, Any] | None = None,
    baseline_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cache = dict(cached_node_outputs or {})
    artifact_diff = build_artifact_diff(prd_v1, prd_v2)
    impact = await analyze_affected_nodes_async(
        artifact_diff=artifact_diff, baseline_snapshot=baseline_snapshot or cache
    )
    directly_affected = [
        node for node in WORKFLOW_NODES if node in set(impact.get("affected_nodes", []))
    ]
    rerun_nodes = _closure(directly_affected)
    steps: list[dict[str, Any]] = []
    cache_hits = 0
    for node in WORKFLOW_NODES:
        should_rerun = node in rerun_nodes
        cache_payload = _cache_source_for_node(cache, node=node)
        has_cache = any(bool(value) for value in cache_payload.values())
        if (not should_rerun) and has_cache:
            cache_hits += 1
        steps.append(
            {
                "node": node,
                "action": "rerun"
                if should_rerun
                else ("reuse_cache" if has_cache else "skip"),
                "reason": (
                    "impact_or_dependency"
                    if should_rerun
                    else (
                        "cache_hit_for_unaffected_node"
                        if has_cache
                        else "unaffected_without_cache"
                    )
                ),
                "cache_hit": bool((not should_rerun) and has_cache),
                "cache_source": cache_payload
                if ((not should_rerun) and has_cache)
                else {},
            }
        )
    return {
        "schema_version": "rerun_plan.v1",
        "artifact_diff": artifact_diff,
        "impact_analysis": impact,
        "rerun_nodes": rerun_nodes,
        "skip_nodes": [step["node"] for step in steps if step["action"] != "rerun"],
        "cache_stats": {
            "eligible_nodes": len(WORKFLOW_NODES) - len(rerun_nodes),
            "cache_hit_nodes": cache_hits,
            "cache_miss_nodes": max(
                0, len(WORKFLOW_NODES) - len(rerun_nodes) - cache_hits
            ),
        },
        "steps": steps,
        "execution_mode": "plan_only",
        "should_execute": False,
        "plan_digest": _sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True)),
    }
