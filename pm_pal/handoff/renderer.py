"""Render execution packs into Markdown prompts for external coding agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from pm_pal.packs.schemas import ExecutionPack

from .templates import (
    CLAUDE_CODE_PROMPT_TEMPLATE,
    CODEX_PROMPT_TEMPLATE,
    OPENCLAW_PROMPT_TEMPLATE,
    PromptTemplate,
)


def _dedupe_items(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for group in groups:
        for raw_item in group:
            item = str(raw_item).strip()
            if item and item not in seen:
                seen.add(item)
                items.append(item)
    return items


def _as_execution_pack(
    execution_pack: ExecutionPack | BaseModel | dict[str, Any],
) -> ExecutionPack:
    if isinstance(execution_pack, ExecutionPack):
        return execution_pack
    if isinstance(execution_pack, BaseModel):
        return ExecutionPack.model_validate(execution_pack.model_dump(mode="python"))
    if isinstance(execution_pack, dict):
        return ExecutionPack.model_validate(execution_pack)
    raise TypeError(f"Unsupported execution pack type: {type(execution_pack)!r}")


def _render_list(items: list[str], empty_text: str) -> str:
    if not items:
        return empty_text
    return "\n".join(f"- {item}" for item in items)


def _render_section(title: str, body: str) -> str:
    return f"## {title}\n{body}".strip()


def _build_project_context(pack: ExecutionPack) -> str:
    implementation_pack = pack.implementation_pack
    risk_lines = [
        f"{risk.id} ({risk.level}): {risk.summary or 'Summary not provided.'}"
        + (f" Mitigation: {risk.mitigation}" if risk.mitigation else "")
        for risk in pack.risk_pack
    ]
    lines = [
        f"- Pack Type: `{pack.pack_type}` v{pack.pack_version}",
        f"- Handoff Strategy: `{pack.handoff_strategy}`",
        f"- Task ID: `{implementation_pack.task_id}`",
        f"- Title: {implementation_pack.title or 'Untitled task'}",
    ]
    if implementation_pack.context:
        lines.append("")
        lines.append(implementation_pack.context.strip())
    lines.append("")
    lines.append("Target modules:")
    if implementation_pack.target_modules:
        lines.extend(f"- `{module}`" for module in implementation_pack.target_modules)
    else:
        lines.append("- Target modules were not provided.")
    lines.append("")
    lines.append("Known delivery risks:")
    if risk_lines:
        lines.extend(f"- {line}" for line in risk_lines)
    else:
        lines.append("- No delivery risks were provided.")
    return "\n".join(lines)


def _build_constraints(
    implementation_constraints: list[str],
    handoff_notes: list[str],
    non_goal_label: str,
) -> str:
    lines = _dedupe_items(implementation_constraints, handoff_notes)
    if non_goal_label and non_goal_label not in lines:
        lines.append(non_goal_label)
    return _render_list(lines, "- No explicit constraints were provided.")


def _render_prompt(
    pack: ExecutionPack, template: PromptTemplate, prompt_source: str
) -> str:
    implementation_pack = pack.implementation_pack
    test_pack = pack.test_pack

    if prompt_source == "implementation":
        handoff = implementation_pack.agent_handoff
        required_changes = _dedupe_items(
            implementation_pack.implementation_steps, handoff.goals
        )
        output_expectations = _dedupe_items(
            [handoff.expected_output], [template.output_hint]
        )
        extra_constraints = [
            f"Primary agent handoff: {template.agent_name} is expected to drive implementation."
        ]
    else:
        handoff = test_pack.agent_handoff
        required_changes = _dedupe_items(
            test_pack.test_scope, test_pack.edge_cases, handoff.goals
        )
        output_expectations = _dedupe_items(
            [handoff.expected_output], [template.output_hint]
        )
        extra_constraints = [
            f"Primary agent handoff: {template.agent_name} is expected to validate delivery readiness."
        ]

    sections = {
        "Goal": "\n".join(
            [
                template.role_summary,
                "",
                _render_list(
                    _dedupe_items([implementation_pack.summary], handoff.goals),
                    "- No explicit goal was provided.",
                ),
            ]
        ).strip(),
        "Project Context": _build_project_context(pack),
        "Required Changes": _render_list(
            required_changes,
            "- No concrete change list was provided in the execution pack.",
        ),
        "Constraints": _build_constraints(
            implementation_pack.constraints,
            extra_constraints + list(handoff.notes),
            "Do not expand scope beyond the execution pack.",
        ),
        "Acceptance Criteria": _render_list(
            _dedupe_items(
                implementation_pack.acceptance_criteria, test_pack.acceptance_criteria
            ),
            "- Acceptance criteria were not provided.",
        ),
        "Testing Requirements": _render_list(
            _dedupe_items(test_pack.test_scope, test_pack.edge_cases),
            "- Testing requirements were not provided.",
        ),
        "Output Expectations": _render_list(
            output_expectations,
            "- Provide a concise execution summary.",
        ),
    }

    section_blocks = [
        _render_section(name, sections[name]) for name in template.section_order
    ]
    header = f"# {template.agent_name} Handoff Prompt"
    return "\n\n".join([header, *section_blocks]).strip() + "\n"


def render_codex_prompt(
    execution_pack: ExecutionPack | BaseModel | dict[str, Any],
) -> str:
    """Render a Markdown prompt tailored for Codex."""

    pack = _as_execution_pack(execution_pack)
    return _render_prompt(pack, CODEX_PROMPT_TEMPLATE, prompt_source="implementation")


def render_claude_code_prompt(
    execution_pack: ExecutionPack | BaseModel | dict[str, Any],
) -> str:
    """Render a Markdown prompt tailored for Claude Code."""

    pack = _as_execution_pack(execution_pack)
    return _render_prompt(pack, CLAUDE_CODE_PROMPT_TEMPLATE, prompt_source="validation")


def render_openclaw_prompt(
    execution_pack: ExecutionPack | BaseModel | dict[str, Any],
) -> str:
    """Render a Markdown prompt tailored for OpenClaw."""

    pack = _as_execution_pack(execution_pack)
    return _render_prompt(
        pack, OPENCLAW_PROMPT_TEMPLATE, prompt_source="implementation"
    )
