"""Reporter agent that assembles workflow state into a Markdown report."""

from __future__ import annotations

from typing import Any

from ..metrics import compute_requirement_coverage, compute_runtime_metrics
from ..state import ReviewState, plan_from_state
from ..utils.logging import get_logger
from ..utils.trace import trace_start

_RISK_HIGH = "High"
_RISK_MEDIUM = "Medium"
_RISK_LOW = "Low"
_IMPACT_EMOJI = {"high": "[H]", "medium": "[M]", "low": "[L]"}


def _risk_level(result: dict) -> str:
    flags = sum(
        [
            not result.get("is_clear", True),
            not result.get("is_testable", True),
            result.get("is_ambiguous", False),
        ]
    )
    if flags >= 2:
        return _RISK_HIGH
    if flags == 1:
        return _RISK_MEDIUM
    return _RISK_LOW


_RISK_MARKER = {_RISK_HIGH: "[H]", _RISK_MEDIUM: "[M]", _RISK_LOW: "[L]"}


def _build_requirement_table(items: list[dict]) -> str:
    rows = [
        "| ID | Description | Acceptance Criteria |",
        "|----|-------------|---------------------|",
    ]
    for item in items:
        rid = item.get("id", "-")
        desc = item.get("description", "-")
        criteria = item.get("acceptance_criteria", [])
        criteria_str = "; ".join(criteria) if criteria else "-"
        rows.append(f"| {rid} | {desc} | {criteria_str} |")
    return "\n".join(rows)


def _build_detail_sections(items: list[dict], results_by_id: dict[str, dict]) -> str:
    sections: list[str] = []
    for item in items:
        rid = item.get("id", "-")
        desc = item.get("description", "-")
        result = results_by_id.get(rid, {})
        clear = "Yes" if result.get("is_clear", True) else "No"
        testable = "Yes" if result.get("is_testable", True) else "No"
        ambiguous = "Yes" if result.get("is_ambiguous", False) else "No"
        issues = result.get("issues", [])
        issues_str = "\n".join(f"  - {i}" for i in issues) if issues else "  None"
        suggestions = result.get("suggestions", "") or "None"
        risk = _risk_level(result)
        marker = _RISK_MARKER[risk]
        sections.append(
            f"### {rid} - {desc}\n\n"
            f"- **Clear:** {clear}\n"
            f"- **Testable:** {testable}\n"
            f"- **Ambiguous:** {ambiguous}\n"
            f"- **Issues:**\n{issues_str}\n"
            f"- **Suggestions:** {suggestions}\n"
            f"- **Risk Level:** {marker} {risk}"
        )
    return "\n\n".join(sections)


def _build_risk_summary(results: list[dict]) -> str:
    counts = {_RISK_HIGH: 0, _RISK_MEDIUM: 0, _RISK_LOW: 0}
    for r in results:
        counts[_risk_level(r)] += 1
    total = len(results) or 1
    rows = [
        "| Risk Level | Count | Percentage |",
        "|------------|-------|------------|",
    ]
    for level in (_RISK_HIGH, _RISK_MEDIUM, _RISK_LOW):
        pct = round(counts[level] / total * 100)
        rows.append(f"| {_RISK_MARKER[level]} {level} | {counts[level]} | {pct}% |")
    return "\n".join(rows)


def _build_task_table(tasks: list[dict]) -> str:
    rows = [
        "| ID | Title | Owner | Requirement IDs | Dependencies | Est. Days |",
        "|----|-------|-------|-----------------|--------------|-----------|",
    ]
    for t in tasks:
        tid = t.get("id", "-")
        title = t.get("title", "-")
        owner = t.get("owner", "-")
        requirement_ids = ", ".join(t.get("requirement_ids", [])) or "-"
        deps = ", ".join(t.get("depends_on", [])) or "-"
        days = t.get("estimate_days", "-")
        rows.append(
            f"| {tid} | {title} | {owner} | {requirement_ids} | {deps} | {days} |"
        )
    return "\n".join(rows)


def _build_milestone_table(milestones: list[dict]) -> str:
    rows = [
        "| ID | Title | Tasks | Target Days |",
        "|----|-------|-------|-------------|",
    ]
    for m in milestones:
        mid = m.get("id", "-")
        title = m.get("title", "-")
        includes = ", ".join(m.get("includes", [])) or "-"
        target = m.get("target_days", "-")
        rows.append(f"| {mid} | {title} | {includes} | {target} |")
    return "\n".join(rows)


def _build_estimation_summary(estimation: dict) -> str:
    total = estimation.get("total_days", "?")
    buffer = estimation.get("buffer_days", "?")
    grand_total = (
        total + buffer
        if isinstance(total, (int, float)) and isinstance(buffer, (int, float))
        else "?"
    )
    return (
        f"- **Total estimated days:** {total}\n"
        f"- **Buffer days:** {buffer}\n"
        f"- **Grand total:** {grand_total}"
    )


def _build_plan_review_section(plan_review: dict) -> str:
    if not plan_review:
        return "_No plan review comments available._"
    lines: list[str] = []
    for key in ("coverage", "milestones", "estimation"):
        comment = plan_review.get(key, "")
        if comment:
            label = key.replace("_", " ").title()
            lines.append(f"- **{label}:** {comment}")
    return "\n".join(lines) if lines else "_No plan review comments available._"


def _format_failed_reviewers(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"

    parts: list[str] = []
    for item in items:
        reviewer = str(item.get("reviewer", "") or "unknown")
        status = str(item.get("status", "failed") or "failed")
        reason = str(item.get("reason", "") or "").strip()
        if reason:
            parts.append(f"{reviewer} ({status}: {reason})")
        else:
            parts.append(f"{reviewer} ({status})")
    return ", ".join(parts)


def _md_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", "<br>")


def _build_risk_register(risks: list[dict]) -> str:
    rows = [
        "| ID | Description | Impact | Mitigation | Buffer Days | Evidence IDs | Evidence |",
        "|----|-------------|--------|------------|-------------|--------------|----------|",
    ]
    for r in risks:
        rid = r.get("id", "-")
        desc = r.get("description", "-")
        impact = r.get("impact", "-")
        marker = _IMPACT_EMOJI.get(impact, "[?]")
        mitigation = r.get("mitigation", "-")
        buf = r.get("buffer_days", 0)
        evidence_ids = ", ".join(r.get("evidence_ids", [])) or "-"
        evidence_snippets = " / ".join(r.get("evidence_snippets", [])) or "-"
        rows.append(
            f"| {_md_cell(rid)} | {_md_cell(desc)} | {_md_cell(f'{marker} {impact}')} "
            f"| {_md_cell(mitigation)} | {_md_cell(buf)} | {_md_cell(evidence_ids)} "
            f"| {_md_cell(evidence_snippets)} |"
        )
    return "\n".join(rows)


def _build_bullet_section(items: list[str], empty_message: str) -> str:
    if not items:
        return empty_message
    return "\n".join(f"- {item}" for item in items)


def _build_prompt_handoff_section(title: str, prompt_handoff: dict[str, Any]) -> str:
    agent_prompt = str(prompt_handoff.get("agent_prompt", "") or "").strip()
    recommended_execution_order = list(
        prompt_handoff.get("recommended_execution_order", []) or []
    )
    non_goals = list(prompt_handoff.get("non_goals", []) or [])
    validation_checklist = list(prompt_handoff.get("validation_checklist", []) or [])

    parts = [f"#### {title}\n"]
    if agent_prompt:
        parts.append("**Agent Prompt**")
        parts.append(agent_prompt)
    else:
        parts.append("_No agent prompt generated._")

    parts.append("\n**Recommended Execution Order**")
    parts.append(
        _build_bullet_section(
            recommended_execution_order, "_No execution order generated._"
        )
    )
    parts.append("\n**Non-Goals**")
    parts.append(_build_bullet_section(non_goals, "_No non-goals generated._"))
    parts.append("\n**Validation Checklist**")
    parts.append(
        _build_bullet_section(
            validation_checklist, "_No validation checklist generated._"
        )
    )
    return "\n".join(parts)


_AGENT = "reporter"
log = get_logger(_AGENT)


async def run(state: ReviewState) -> ReviewState:
    parsed_items: list[dict] = state.get("parsed_items", [])
    review_results: list[dict] = state.get("review_results", [])
    plan = plan_from_state(state)
    tasks: list[dict] = plan.get("tasks", [])
    milestones: list[dict] = plan.get("milestones", [])
    estimation: dict = plan.get("estimation", {})
    implementation_plan: dict = dict(state.get("implementation_plan", {}) or {})
    test_plan: dict = dict(state.get("test_plan", {}) or {})
    codex_prompt_handoff: dict = dict(state.get("codex_prompt_handoff", {}) or {})
    claude_code_prompt_handoff: dict = dict(
        state.get("claude_code_prompt_handoff", {}) or {}
    )
    risks: list[dict] = state.get("risks", [])
    plan_review: dict = state.get("plan_review", {})
    trace: dict[str, Any] = dict(state.get("trace", {}))
    parallel_review_meta: dict[str, Any] = dict(
        state.get("parallel_review_meta", {}) or {}
    )

    input_chars = sum(
        len(str(v))
        for v in (
            parsed_items,
            review_results,
            tasks,
            milestones,
            estimation,
            implementation_plan,
            test_plan,
            codex_prompt_handoff,
            claude_code_prompt_handoff,
            risks,
            plan_review,
            parallel_review_meta,
        )
    )
    span = trace_start(_AGENT, model="none", input_chars=input_chars)

    results_by_id = {r["id"]: r for r in review_results if "id" in r}
    metrics = compute_requirement_coverage(parsed_items, tasks)
    metrics.update(compute_runtime_metrics(trace))

    parts: list[str] = ["# Requirement Review Report\n"]
    ratio = metrics.get("coverage_ratio", 0.0)
    req_to_tasks = metrics.get("requirement_to_tasks", {})
    uncovered = metrics.get("uncovered_requirements", [])
    covered_count = sum(1 for task_ids in req_to_tasks.values() if task_ids)
    total_count = len(req_to_tasks)
    partial_review = bool(parallel_review_meta.get("partial_review", False))
    manual_review_message = str(
        parallel_review_meta.get("manual_review_message", "") or ""
    ).strip()
    reviewers_failed = list(parallel_review_meta.get("reviewers_failed", []) or [])
    parts.append(
        f"**Coverage:** {ratio:.2%} ({covered_count}/{total_count} requirements covered)"
    )
    if uncovered:
        parts.append(f"**Uncovered:** {', '.join(uncovered)}")
    if partial_review:
        parts.append("**Partial Review:** Yes")
        parts.append(
            f"**Reviewers Failed:** {_format_failed_reviewers(reviewers_failed)}"
        )
    if manual_review_message:
        parts.append(f"**Attention:** {manual_review_message}")
    parts.append("")

    parts.append("## 1. Requirement List\n")
    parts.append(
        _build_requirement_table(parsed_items)
        if parsed_items
        else "_No requirement items were parsed._"
    )

    parts.append("\n## 2. Review Details\n")
    parts.append(
        _build_detail_sections(parsed_items, results_by_id)
        if parsed_items and review_results
        else "_No review results available._"
    )

    parts.append("\n## 3. Risk Summary\n")
    parts.append(
        _build_risk_summary(review_results)
        if review_results
        else "_No review results to summarize._"
    )

    parts.append("\n## 4. Delivery Plan\n")
    parts.append("### 4.1 Task Breakdown\n")
    parts.append(_build_task_table(tasks) if tasks else "_No tasks generated._")

    parts.append("\n### 4.2 Milestones\n")
    parts.append(
        _build_milestone_table(milestones)
        if milestones
        else "_No milestones generated._"
    )

    parts.append("\n### 4.3 Estimation\n")
    parts.append(
        _build_estimation_summary(estimation)
        if estimation
        else "_No estimation available._"
    )

    parts.append("\n### 4.4 Implementation Planning\n")
    parts.append("**Implementation Steps**")
    parts.append(
        _build_bullet_section(
            implementation_plan.get("implementation_steps", []),
            "_No implementation steps generated._",
        )
    )
    parts.append("\n**Target Modules**")
    parts.append(
        _build_bullet_section(
            implementation_plan.get("target_modules", []),
            "_No target modules generated._",
        )
    )
    parts.append("\n**Constraints**")
    parts.append(
        _build_bullet_section(
            implementation_plan.get("constraints", []),
            "_No implementation constraints generated._",
        )
    )

    parts.append("\n### 4.5 Test Planning\n")
    parts.append("**Test Scope**")
    parts.append(
        _build_bullet_section(
            test_plan.get("test_scope", []), "_No test scope generated._"
        )
    )
    parts.append("\n**Edge Cases**")
    parts.append(
        _build_bullet_section(
            test_plan.get("edge_cases", []), "_No edge cases generated._"
        )
    )
    parts.append("\n**Regression Focus**")
    parts.append(
        _build_bullet_section(
            test_plan.get("regression_focus", []), "_No regression focus generated._"
        )
    )

    parts.append("\n### 4.6 Coding-Agent Handoff Prompts\n")
    parts.append(_build_prompt_handoff_section("Codex", codex_prompt_handoff))
    parts.append("")
    parts.append(
        _build_prompt_handoff_section("Claude Code", claude_code_prompt_handoff)
    )

    parts.append("\n## 5. Delivery Risk Register\n")
    parts.append(
        _build_risk_register(risks) if risks else "_No delivery risks identified._"
    )

    parts.append("\n## 6. Plan Review\n")
    parts.append(_build_plan_review_section(plan_review))

    final_report = "\n".join(parts) + "\n"
    trace[_AGENT] = span.end(status="ok", output_chars=len(final_report))
    log.info("报告生成完成", extra={"node": _AGENT})
    return {"final_report": final_report, "metrics": metrics, "trace": trace}
