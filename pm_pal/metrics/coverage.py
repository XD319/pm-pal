"""Deterministic requirement coverage metrics.

Coverage is computed from parsed requirement IDs and planner task references.
"""

from __future__ import annotations

from collections import OrderedDict


def _normalize_ids(raw_ids: object) -> list[str]:
    """Normalize IDs into a clean list of non-empty strings."""
    if isinstance(raw_ids, str):
        raw_list = [raw_ids]
    elif isinstance(raw_ids, list):
        raw_list = raw_ids
    else:
        return []

    clean_ids: list[str] = []
    for value in raw_list:
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                clean_ids.append(normalized)
    return clean_ids


def compute_requirement_coverage(
    parsed_items: list[dict],
    tasks: list[dict],
) -> dict:
    """Return deterministic requirement coverage metrics.

    Returns:
        {
          "coverage_ratio": float,  # 0..1
          "uncovered_requirements": list[str],
          "requirement_to_tasks": dict[str, list[str]],
        }
    """
    requirement_to_tasks: "OrderedDict[str, list[str]]" = OrderedDict()
    for item in parsed_items:
        rid = item.get("id")
        if isinstance(rid, str):
            normalized = rid.strip()
            if normalized and normalized not in requirement_to_tasks:
                requirement_to_tasks[normalized] = []

    for task in tasks:
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            continue

        normalized_task_id = task_id.strip()
        for rid in _normalize_ids(task.get("requirement_ids", [])):
            if rid not in requirement_to_tasks:
                continue
            if normalized_task_id not in requirement_to_tasks[rid]:
                requirement_to_tasks[rid].append(normalized_task_id)

    total = len(requirement_to_tasks)
    covered = sum(1 for task_ids in requirement_to_tasks.values() if task_ids)
    uncovered = [rid for rid, task_ids in requirement_to_tasks.items() if not task_ids]
    coverage_ratio = (covered / total) if total else 0.0

    return {
        "coverage_ratio": round(coverage_ratio, 4),
        "uncovered_requirements": uncovered,
        "requirement_to_tasks": dict(requirement_to_tasks),
    }
