"""Central template registry for prompts, artifacts, and adapter handoffs."""

from __future__ import annotations

from typing import Any, cast

from .models import (
    BASE_SECTION_ORDER,
    AdapterPromptTemplate,
    DeliveryArtifactTemplate,
    ReviewPromptTemplate,
    TemplateDefinition,
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _md_bullets(items: list[str], empty_message: str) -> list[str]:
    if not items:
        return [f"- {empty_message}"]
    return [f"- {item}" for item in items]


def _render_prd_review_report(review_result: dict[str, Any]) -> str:
    final_report = str(review_result.get("final_report", "") or "").strip()
    lines = [
        "# PRD Review Report",
        "",
        "Source: `result.final_report` from the review workflow.",
        "",
    ]
    if final_report:
        lines.append(final_report)
    else:
        lines.append(
            "_No final report was generated. Use `report.md` as the fallback source._"
        )
    return "\n".join(lines).strip() + "\n"


def _render_open_questions(review_result: dict[str, Any]) -> str:
    review_results = _as_list(review_result.get("review_results"))
    open_items: list[str] = []
    for item in review_results:
        if not isinstance(item, dict):
            continue
        issues = [
            str(issue) for issue in _as_list(item.get("issues")) if str(issue).strip()
        ]
        if item.get("is_ambiguous") or issues:
            question = str(
                item.get("description")
                or item.get("summary")
                or item.get("id")
                or "Unknown item"
            )
            open_items.append(f"`{item.get('id', 'unknown')}` {question}")
            open_items.extend(
                [f"  - {issue}" for issue in issues]
                or ["  - Clarify requirement intent and acceptance criteria."]
            )

    lines = [
        "# Open Questions",
        "",
        "Source: `result.review_results` items marked ambiguous or carrying issues.",
        "",
        "## Questions",
        "",
        *_md_bullets(open_items, "No open questions were detected."),
    ]
    return "\n".join(lines).strip() + "\n"


def _render_scope_boundary(review_result: dict[str, Any]) -> str:
    parsed_items = _as_list(review_result.get("parsed_items"))
    tasks = _as_list(review_result.get("tasks"))
    in_scope = [
        f"`{item.get('id', 'unknown')}` {item.get('description', '')}".strip()
        for item in parsed_items
        if isinstance(item, dict)
    ]
    task_titles = [
        f"`{task.get('id', 'unknown')}` {task.get('title', '')}".strip()
        for task in tasks
        if isinstance(task, dict)
    ]

    lines = [
        "# Scope Boundary",
        "",
        "Source: `result.parsed_items` and `result.tasks`.",
        "",
        "## In Scope Requirements",
        "",
        *_md_bullets(in_scope, "No parsed requirement items were available."),
        "",
        "## Planned Delivery Tasks",
        "",
        *_md_bullets(task_titles, "No delivery tasks were generated."),
        "",
        "## Out of Scope / Pending Clarification",
        "",
        "- Any work not mapped to the reviewed requirements should be treated as out of scope until approved.",
    ]
    return "\n".join(lines).strip() + "\n"


def _render_tech_design_draft(review_result: dict[str, Any]) -> str:
    implementation_plan = _as_dict(review_result.get("implementation_plan"))
    tasks = _as_list(review_result.get("tasks"))
    lines = [
        "# Technical Design Draft",
        "",
        "Source: `result.implementation_plan` and `result.tasks`.",
        "",
        "## Target Modules",
        "",
        *_md_bullets(
            [str(item) for item in _as_list(implementation_plan.get("target_modules"))],
            "No target modules were generated.",
        ),
        "",
        "## Proposed Implementation Steps",
        "",
        *_md_bullets(
            [
                str(item)
                for item in _as_list(implementation_plan.get("implementation_steps"))
            ],
            "No implementation steps were generated.",
        ),
        "",
        "## Constraints",
        "",
        *_md_bullets(
            [str(item) for item in _as_list(implementation_plan.get("constraints"))],
            "No explicit implementation constraints were generated.",
        ),
        "",
        "## Delivery Tasks",
        "",
        *_md_bullets(
            [
                f"`{task.get('id', 'unknown')}` {task.get('title', '')}".strip()
                for task in tasks
                if isinstance(task, dict)
            ],
            "No delivery tasks were generated.",
        ),
    ]
    return "\n".join(lines).strip() + "\n"


def _render_test_checklist(review_result: dict[str, Any]) -> str:
    test_plan = _as_dict(review_result.get("test_plan"))
    review_results = _as_list(review_result.get("review_results"))
    issue_checks = [
        f"Resolve review issue for `{item.get('id', 'unknown')}`: {issue}"
        for item in review_results
        if isinstance(item, dict)
        for issue in [
            str(candidate)
            for candidate in _as_list(item.get("issues"))
            if str(candidate).strip()
        ]
    ]
    lines = [
        "# Test Checklist",
        "",
        "Source: `result.test_plan` and unresolved review issues.",
        "",
        "## Test Scope",
        "",
        *_md_bullets(
            [str(item) for item in _as_list(test_plan.get("test_scope"))],
            "No test scope was generated.",
        ),
        "",
        "## Edge Cases",
        "",
        *_md_bullets(
            [str(item) for item in _as_list(test_plan.get("edge_cases"))],
            "No edge cases were generated.",
        ),
        "",
        "## Regression Focus",
        "",
        *_md_bullets(
            [str(item) for item in _as_list(test_plan.get("regression_focus"))],
            "No regression focus was generated.",
        ),
        "",
        "## Review-Driven Checks",
        "",
        *_md_bullets(issue_checks, "No extra review-driven checks were required."),
    ]
    return "\n".join(lines).strip() + "\n"


PARSER_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.parser",
    template_type="review_prompt",
    version="v1.1",
    description="Default parser prompt for decomposing a requirement document into atomic items.",
    system_prompt="""\
You are a senior requirements analyst. Your task is to decompose a requirement \
document into discrete, atomic requirement items.

For each item, extract:
- id: a sequential identifier such as REQ-001, REQ-002, ...
- description: one-sentence summary of the requirement
- acceptance_criteria: a list of measurable conditions that must be satisfied

Respond with valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "parsed_items": [
    {
      "id": "REQ-001",
      "description": "...",
      "acceptance_criteria": ["..."]
    }
  ]
}
""",
    user_prompt="""\
Please parse the following requirement document into structured items.

---
{requirement_doc}
---
""",
)

CLARIFY_PARSER_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.parser.clarify",
    template_type="review_prompt",
    version="v1.1-clarify",
    description="Clarification-focused parser prompt used when ambiguity remains too high.",
    system_prompt="""\
You are a senior requirements analyst focused on clarification and disambiguation.
Your task is to decompose the requirement document into discrete, atomic requirement items
with strict, testable acceptance criteria.

For each item, extract:
- id: a sequential identifier such as REQ-001, REQ-002, ...
- description: one-sentence summary of the requirement with ambiguous wording removed
- acceptance_criteria: a list of measurable, testable conditions with explicit thresholds

Rules:
- Split vague requirements into finer-grained items when needed.
- Replace imprecise terms (for example: "fast", "reliable", "timely", "good") with concrete checks.
- Keep every criterion independently verifiable by QA.

Respond with valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "parsed_items": [
    {
      "id": "REQ-001",
      "description": "...",
      "acceptance_criteria": ["..."]
    }
  ]
}
""",
    user_prompt="""\
Please re-parse and clarify the requirement document below.
The previous review found too many high-risk requirement items.
Return a finer-grained, more testable parsed_items list.

---
{requirement_doc}
---
""",
)

REVIEWER_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.reviewer",
    template_type="review_prompt",
    version="v1.1",
    description="Reviewer prompt for requirement clarity, testability, and plan coverage checks.",
    system_prompt="""\
You are a senior QA / requirements reviewer. You receive two inputs:

1. Parsed requirement items, the atomic requirements extracted from a PRD.
2. Delivery plan (tasks, milestones, estimation), produced by the planner.

For every requirement item evaluate:

- Clarity: Is the description unambiguous and easy to understand?
- Testability: Can the acceptance criteria be verified by a concrete test?
- Ambiguity: Does the wording contain vague terms that different stakeholders could interpret differently?
- Plan coverage: Does at least one task in the delivery plan map to this requirement? If not, flag it as an issue.

Additionally, produce a top-level plan_review object that assesses:

- coverage: Are there requirements with no corresponding task?
- milestones: Do the milestones cover all critical tasks?
- estimation: Does the total estimate including buffer look reasonable?

Respond with valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "review_results": [
    {
      "id": "REQ-001",
      "is_clear": true,
      "is_testable": true,
      "is_ambiguous": false,
      "issues": ["list of concrete issues, empty if none"],
      "suggestions": "actionable improvement suggestion, empty string if none"
    }
  ],
  "plan_review": {
    "coverage": "comment on requirement-to-task mapping gaps",
    "milestones": "comment on milestone completeness",
    "estimation": "comment on whether total_days + buffer_days is reasonable"
  }
}
""",
    user_prompt="""\
Please review every requirement item below, cross-referencing with the \
delivery plan that follows.

### Requirement Items
---
{items_json}
---

### Delivery Plan
---
{plan_json}
---
""",
)

PLANNER_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.planner",
    template_type="review_prompt",
    version="v1.1",
    description="Planner prompt for turning parsed requirements into a delivery plan.",
    system_prompt="""\
You are a senior technical project manager. Given a list of parsed requirement \
items you must produce a concrete delivery plan covering four sections:

1. Tasks: atomic work items, each assigned to an owner role \
(FE / BE / QA / DevOps), with dependency links, a requirement mapping list, \
and an effort estimate in days.
2. Milestones: logical delivery checkpoints that group related tasks, \
each with a cumulative target in days from project start.
3. Dependencies: explicit edges between tasks (type is always "blocked_by").
4. Estimation: an overall summary with total_days and a buffer_days value \
(recommend 15-20% of total).

Respond with valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "tasks": [
    {
      "id": "T-1",
      "title": "...",
      "owner": "FE",
      "requirement_ids": ["REQ-001"],
      "depends_on": [],
      "estimate_days": 2
    }
  ],
  "milestones": [
    {
      "id": "M-1",
      "title": "...",
      "includes": ["T-1", "T-2"],
      "target_days": 7
    }
  ],
  "dependencies": [
    {
      "from": "T-2",
      "to": "T-5",
      "type": "blocked_by"
    }
  ],
  "estimation": {
    "total_days": 12,
    "buffer_days": 2
  }
}

Hard requirement for every task:
- `requirement_ids` MUST be present.
- `requirement_ids` MUST be a non-empty list of valid requirement IDs from the input.
""",
    user_prompt="""\
Based on the parsed requirement items below, produce a delivery plan.

---
{items_json}
---
""",
)

RISK_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.risk",
    template_type="review_prompt",
    version="v1.1",
    description="Risk analysis prompt for delivery-quality and schedule risks.",
    system_prompt="""\
You are a senior delivery risk analyst. Given structured requirements and an \
optional delivery plan (tasks, milestones, dependencies, and estimation) you \
must identify concrete risks that could threaten delivery quality or schedule.

Consider at least:
- Requirement ambiguity - vague wording, missing constraints, or hidden assumptions.
- Integration uncertainty - requirements that imply cross-team coordination, migration, or sequencing risk.
- Dependency chains - long chains or single points of failure.
- Resource bottlenecks - too many tasks assigned to one owner role.
- Tight buffers - buffer_days < 15% of total_days.
- Uncovered milestones - milestones that include tasks with unresolved dependencies.
- Estimation optimism - individual task estimates that look too low for their scope.

For every risk provide an impact level (high / medium / low), a mitigation \
strategy, and an optional extra buffer_days recommendation.

You are also given evidence candidates from a local risk catalog retrieval tool.
- Use the most relevant evidence items for each risk.
- Keep evidence references short and specific.
- If no evidence applies, return empty arrays.

Respond with valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "risks": [
    {
      "id": "R-1",
      "description": "...",
      "impact": "high",
      "mitigation": "...",
      "buffer_days": 1,
      "evidence_ids": ["RC-001"],
      "evidence_snippets": ["Too many integration tasks are assigned to one backend engineer."]
    }
  ]
}
""",
    user_prompt="""\
Analyse the structured requirements and optional draft plan below and identify all delivery risks.

### Structured Requirements
---
{requirements_json}
---

### Draft Plan Context
---
{plan_json}
---

### Retrieved Evidence Candidates
---
{evidence_json}
---
""",
)

IMPLEMENTATION_PLAN_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.delivery_planning.implementation",
    template_type="review_prompt",
    version="v1",
    description="Prompt for deriving implementation guidance from reviewed requirements, tasks, and risks.",
    system_prompt="""\
You are a senior engineering lead. Given structured requirements, the current
task plan, and known delivery risks, produce concise implementation guidance
for execution planning.

Return valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "implementation_steps": ["ordered engineering steps"],
  "target_modules": ["likely code modules or subsystems to change"],
  "constraints": ["technical or delivery constraints to keep explicit"]
}

Rules:
- Keep steps concrete and implementation-facing.
- Mention modules at subsystem level when file names are unknown.
- Include constraints implied by requirements, dependencies, and risks.
""",
    user_prompt="""\
Based on the inputs below, produce implementation planning guidance.

### Structured Requirements
---
{requirements_json}
---

### Tasks
---
{tasks_json}
---

### Risks
---
{risks_json}
---
""",
)

TEST_PLAN_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.delivery_planning.test_plan",
    template_type="review_prompt",
    version="v1",
    description="Prompt for generating focused execution-time test planning guidance.",
    system_prompt="""\
You are a senior QA lead. Given structured requirements, the current task plan,
and known delivery risks, produce focused test planning guidance for execution.

Return valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "test_scope": ["core areas that must be covered"],
  "edge_cases": ["boundary or failure scenarios"],
  "regression_focus": ["existing flows that are most likely to regress"]
}

Rules:
- Keep scope tied to requirements and planned work.
- Edge cases should reflect risk, dependency, and integration concerns.
- Regression focus should name flows or subsystems, not generic advice.
""",
    user_prompt="""\
Based on the inputs below, produce a test planning guide.

### Structured Requirements
---
{requirements_json}
---

### Tasks
---
{tasks_json}
---

### Risks
---
{risks_json}
---
""",
)

CODEX_PROMPT_GENERATION_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.delivery_planning.codex_prompt",
    template_type="review_prompt",
    version="v1",
    description="Prompt for generating the Codex execution handoff payload.",
    system_prompt="""\
You are preparing a structured implementation handoff for Codex, an external
coding agent that will edit code directly. Produce a high-signal execution
prompt that is concrete, repository-aware, and constrained.

Return valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "agent_prompt": "single handoff prompt string for Codex",
  "recommended_execution_order": ["ordered execution phases"],
  "non_goals": ["explicit out-of-scope items"],
  "validation_checklist": ["checks the agent must complete before handoff is done"]
}

Rules:
- The agent_prompt must state the implementation goal, relevant constraints, and acceptance criteria.
- Refer to concrete modules, flows, risks, and test scope from the input. Do not produce generic advice.
- Include instructions to preserve existing behavior unless the inputs explicitly require changes.
- recommended_execution_order must be actionable phases, not vague principles.
- non_goals must actively prevent scope creep.
- validation_checklist must be testable and aligned to acceptance criteria.
""",
    user_prompt="""\
Generate a Codex handoff prompt from the validated planning inputs below.

### Implementation Plan
---
{implementation_plan_json}
---

### Test Plan
---
{test_plan_json}
---

### Constraints
---
{constraints_json}
---

### Acceptance Criteria
---
{acceptance_criteria_json}
---
""",
)

CLAUDE_CODE_PROMPT_GENERATION_REVIEW_PROMPT = ReviewPromptTemplate(
    template_id="review.delivery_planning.claude_code_prompt",
    template_type="review_prompt",
    version="v1",
    description="Prompt for generating the Claude Code execution handoff payload.",
    system_prompt="""\
You are preparing a structured implementation handoff for Claude Code, an
external coding agent that will inspect, edit, and validate code. Produce a
precise execution prompt that minimizes ambiguity and overbuild.

Return valid JSON only, no markdown fences and no commentary.
The JSON schema you MUST follow:

{
  "agent_prompt": "single handoff prompt string for Claude Code",
  "recommended_execution_order": ["ordered execution phases"],
  "non_goals": ["explicit out-of-scope items"],
  "validation_checklist": ["checks the agent must complete before handoff is done"]
}

Rules:
- The agent_prompt must tell Claude Code what to change, what not to change, and how to prove completion.
- Use the implementation plan and test plan to anchor the prompt in specific engineering work.
- Make constraints and acceptance criteria explicit inside the prompt; do not imply them.
- recommended_execution_order should bias toward inspection before editing, then verification.
- non_goals should name plausible but excluded work.
- validation_checklist should be executable by an engineering agent and avoid generic statements.
""",
    user_prompt="""\
Generate a Claude Code handoff prompt from the validated planning inputs below.

### Implementation Plan
---
{implementation_plan_json}
---

### Test Plan
---
{test_plan_json}
---

### Constraints
---
{constraints_json}
---

### Acceptance Criteria
---
{acceptance_criteria_json}
---
""",
)

CODEX_ADAPTER_PROMPT_TEMPLATE = AdapterPromptTemplate(
    template_id="adapter.codex.handoff_markdown",
    template_type="adapter_prompt",
    version="handoff_markdown_v1",
    description="Markdown handoff template tailored for Codex execution requests.",
    agent_name="Codex",
    role_summary="Use the execution pack as the source of truth and implement the smallest complete change set.",
    output_hint="Return a concise implementation summary, the files changed, and the tests or checks you ran.",
    section_order=BASE_SECTION_ORDER,
)

CLAUDE_CODE_ADAPTER_PROMPT_TEMPLATE = AdapterPromptTemplate(
    template_id="adapter.claude_code.handoff_markdown",
    template_type="adapter_prompt",
    version="handoff_markdown_v1",
    description="Markdown handoff template tailored for Claude Code validation requests.",
    agent_name="Claude Code",
    role_summary="Start with a repository analysis instruction, then use the execution pack as the source of truth to focus on delivery validation, safety checks, and test completeness.",
    output_hint="Return a concise implementation summary, the changed files, and the test results or checks you ran.",
    section_order=BASE_SECTION_ORDER,
)

OPENCLAW_ADAPTER_PROMPT_TEMPLATE = AdapterPromptTemplate(
    template_id="adapter.openclaw.handoff_markdown",
    template_type="adapter_prompt",
    version="handoff_markdown_v1",
    description="Markdown handoff template tailored for OpenClaw execution requests.",
    agent_name="OpenClaw",
    role_summary="Use the execution pack as the source of truth, implement the intended change set, and keep verification scope explicit for local-agent execution.",
    output_hint="Return a concise implementation summary, the changed files, and the validation scope you covered.",
    section_order=BASE_SECTION_ORDER,
)

PRD_REVIEW_REPORT_TEMPLATE = DeliveryArtifactTemplate(
    template_id="delivery_artifact.prd_review_report",
    template_type="delivery_artifact_template",
    version="v1",
    description="Markdown artifact template for the top-level PRD review report handoff file.",
    artifact_type="prd_review_report",
    file_name="prd_review_report.md",
    renderer=_render_prd_review_report,
)

OPEN_QUESTIONS_TEMPLATE = DeliveryArtifactTemplate(
    template_id="delivery_artifact.open_questions",
    template_type="delivery_artifact_template",
    version="v1",
    description="Markdown artifact template for open requirement questions and ambiguities.",
    artifact_type="open_questions",
    file_name="open_questions.md",
    renderer=_render_open_questions,
)

SCOPE_BOUNDARY_TEMPLATE = DeliveryArtifactTemplate(
    template_id="delivery_artifact.scope_boundary",
    template_type="delivery_artifact_template",
    version="v1",
    description="Markdown artifact template that records in-scope and out-of-scope delivery boundaries.",
    artifact_type="scope_boundary",
    file_name="scope_boundary.md",
    renderer=_render_scope_boundary,
)

TECH_DESIGN_DRAFT_TEMPLATE = DeliveryArtifactTemplate(
    template_id="delivery_artifact.tech_design_draft",
    template_type="delivery_artifact_template",
    version="v1",
    description="Markdown artifact template for the generated technical design draft.",
    artifact_type="tech_design_draft",
    file_name="tech_design_draft.md",
    renderer=_render_tech_design_draft,
)

TEST_CHECKLIST_TEMPLATE = DeliveryArtifactTemplate(
    template_id="delivery_artifact.test_checklist",
    template_type="delivery_artifact_template",
    version="v1",
    description="Markdown artifact template for execution-time test and review checks.",
    artifact_type="test_checklist",
    file_name="test_checklist.md",
    renderer=_render_test_checklist,
)


_REGISTERED_TEMPLATES: dict[str, dict[str, TemplateDefinition]] = {}
_DEFAULT_TEMPLATE_VERSION: dict[str, str] = {}
_TEMPLATE_IDS_BY_TYPE: dict[str, set[str]] = {}


class TemplateRegistryError(LookupError):
    """Base class for controlled template-registry lookup failures."""


class TemplateNotFoundError(TemplateRegistryError):
    """Raised when a template_id is not registered."""


class TemplateTypeNotFoundError(TemplateRegistryError):
    """Raised when a template_type has no registered templates."""


class TemplateVersionNotFoundError(TemplateRegistryError):
    """Raised when a requested template version cannot be resolved."""


def _sorted_templates(
    templates: list[TemplateDefinition],
) -> tuple[TemplateDefinition, ...]:
    return tuple(
        sorted(
            templates,
            key=lambda template: (
                template.template_type,
                template.template_id,
                template.version,
            ),
        )
    )


def _template_status(template: TemplateDefinition) -> str:
    return (
        "active"
        if is_default_template(template.template_id, template.version)
        else "registered"
    )


def _lookup_versions(template_id: str) -> dict[str, TemplateDefinition]:
    versions = _REGISTERED_TEMPLATES.get(template_id)
    if not versions:
        raise TemplateNotFoundError(f"unknown template_id: {template_id}")
    return versions


def _lookup_template_ids_by_type(template_type: str) -> tuple[str, ...]:
    ids = _TEMPLATE_IDS_BY_TYPE.get(template_type)
    if not ids:
        raise TemplateTypeNotFoundError(f"unknown template_type: {template_type}")
    return tuple(sorted(ids))


def register_template(
    template: TemplateDefinition, *, is_default: bool | None = None
) -> TemplateDefinition:
    versions = _REGISTERED_TEMPLATES.setdefault(template.template_id, {})
    if template.version in versions:
        raise ValueError(
            f"template already registered: {template.template_id}@{template.version}"
        )
    versions[template.version] = template
    _TEMPLATE_IDS_BY_TYPE.setdefault(template.template_type, set()).add(
        template.template_id
    )
    if is_default is not False:
        _DEFAULT_TEMPLATE_VERSION[template.template_id] = template.version
    elif template.template_id not in _DEFAULT_TEMPLATE_VERSION:
        _DEFAULT_TEMPLATE_VERSION[template.template_id] = template.version
    return template


def resolve_default_template_version(template_id: str) -> str:
    _lookup_versions(template_id)
    try:
        return _DEFAULT_TEMPLATE_VERSION[template_id]
    except KeyError as exc:
        raise TemplateVersionNotFoundError(
            f"no default version registered for template_id: {template_id}"
        ) from exc


def resolve_template_version(template_id: str, version: str | None = None) -> str:
    if version is None:
        return resolve_default_template_version(template_id)
    versions = _lookup_versions(template_id)
    normalized_version = str(version or "").strip()
    if normalized_version in versions:
        return normalized_version
    raise TemplateVersionNotFoundError(
        f"unknown template version: {template_id}@{normalized_version}"
    )


def get_default_template(template_id: str) -> TemplateDefinition:
    return get_template(template_id, resolve_default_template_version(template_id))


def get_template_by_version(template_id: str, version: str) -> TemplateDefinition:
    return get_template(template_id, version)


def get_template(template_id: str, version: str | None = None) -> TemplateDefinition:
    versions = _lookup_versions(template_id)
    resolved_version = resolve_template_version(template_id, version)
    return versions[resolved_version]


def is_default_template(template_id: str, version: str) -> bool:
    try:
        return (
            resolve_default_template_version(template_id) == str(version or "").strip()
        )
    except TemplateRegistryError:
        return False


def list_templates(
    *,
    template_type: str | None = None,
    version: str | None = None,
) -> tuple[TemplateDefinition, ...]:
    if template_type is not None:
        return get_templates_by_type(template_type, version=version)

    templates = [
        template
        for versions in _REGISTERED_TEMPLATES.values()
        for template in versions.values()
    ]
    if version is not None:
        normalized_version = str(version or "").strip()
        templates = [
            template for template in templates if template.version == normalized_version
        ]
        if not templates:
            raise TemplateVersionNotFoundError(
                f"unknown template version: {normalized_version}"
            )
    return _sorted_templates(templates)


def get_templates_by_type(
    template_type: str, *, version: str | None = None
) -> tuple[TemplateDefinition, ...]:
    template_ids = _lookup_template_ids_by_type(str(template_type or "").strip())
    if version is None:
        templates = [
            template
            for template_id in template_ids
            for template in _lookup_versions(template_id).values()
        ]
        return _sorted_templates(templates)

    normalized_version = str(version or "").strip()
    templates = [
        versions[normalized_version]
        for template_id in template_ids
        for versions in [_lookup_versions(template_id)]
        if normalized_version in versions
    ]
    if not templates:
        raise TemplateVersionNotFoundError(
            f"unknown template version for template_type {template_type}: {normalized_version}"
        )
    return _sorted_templates(templates)


def find_templates_by_version(
    version: str, *, template_type: str | None = None
) -> tuple[TemplateDefinition, ...]:
    return list_templates(template_type=template_type, version=version)


def get_template_record(template_id: str, version: str | None = None) -> dict[str, Any]:
    template = get_template(template_id, version)
    return template.registry_metadata(
        is_default=is_default_template(template.template_id, template.version),
        status=_template_status(template),
    )


def list_template_records(
    *,
    template_type: str | None = None,
    version: str | None = None,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        get_template_record(template.template_id, template.version)
        for template in list_templates(template_type=template_type, version=version)
    )


def get_review_prompt_template(
    template_id: str, version: str | None = None
) -> ReviewPromptTemplate:
    return cast(ReviewPromptTemplate, get_template(template_id, version))


def get_adapter_prompt_template(
    template_id: str, version: str | None = None
) -> AdapterPromptTemplate:
    return cast(AdapterPromptTemplate, get_template(template_id, version))


def get_delivery_artifact_template(
    template_id: str, version: str | None = None
) -> DeliveryArtifactTemplate:
    return cast(DeliveryArtifactTemplate, get_template(template_id, version))


for _template in (
    PARSER_REVIEW_PROMPT,
    CLARIFY_PARSER_REVIEW_PROMPT,
    REVIEWER_REVIEW_PROMPT,
    PLANNER_REVIEW_PROMPT,
    RISK_REVIEW_PROMPT,
    IMPLEMENTATION_PLAN_REVIEW_PROMPT,
    TEST_PLAN_REVIEW_PROMPT,
    CODEX_PROMPT_GENERATION_REVIEW_PROMPT,
    CLAUDE_CODE_PROMPT_GENERATION_REVIEW_PROMPT,
    CODEX_ADAPTER_PROMPT_TEMPLATE,
    CLAUDE_CODE_ADAPTER_PROMPT_TEMPLATE,
    OPENCLAW_ADAPTER_PROMPT_TEMPLATE,
    PRD_REVIEW_REPORT_TEMPLATE,
    OPEN_QUESTIONS_TEMPLATE,
    SCOPE_BOUNDARY_TEMPLATE,
    TECH_DESIGN_DRAFT_TEMPLATE,
    TEST_CHECKLIST_TEMPLATE,
):
    register_template(_template)
