"""Helpers for constructing structured task packs."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from pm_pal.schemas.planning_skill_schema import (
    CodingAgentPromptOutput,
    ImplementationPlanOutput,
    QaPlanningOutput,
)

from .schemas import (
    AgentHandoff,
    ExecutionPack,
    ImplementationPack,
    RiskSummaryItem,
    TaskBundlePriority,
    TaskBundleRole,
    TaskBundleSourceType,
    TaskBundleTask,
    TaskBundleTasksByRole,
    TaskBundleV1,
    TestPack,
)


JsonDict = dict[str, Any]


def _as_dict(data: Any) -> JsonDict:
    if data is None:
        return {}
    if isinstance(data, BaseModel):
        return data.model_dump(mode="python")
    if isinstance(data, dict):
        return dict(data)
    raise TypeError(f"Unsupported input type: {type(data)!r}")


def _as_dict_list(items: Any) -> list[JsonDict]:
    if items is None:
        return []
    if isinstance(items, list):
        return [_as_dict(item) for item in items]
    raise TypeError(f"Expected list input, got: {type(items)!r}")


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _collect_acceptance_criteria(requirements: list[JsonDict]) -> list[str]:
    criteria: list[str] = []
    for requirement in requirements:
        for item in requirement.get("acceptance_criteria", []) or []:
            text = str(item).strip()
            if text and text not in criteria:
                criteria.append(text)
    return criteria


def _json_block(title: str, payload: Any) -> str:
    return f"{title}:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def _unique_strings(items: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in items:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    normalized = str(text or "").strip().lower()
    return any(needle in normalized for needle in needles)


def _classify_role(*parts: str) -> TaskBundleRole:
    combined = " ".join(str(part or "") for part in parts).lower()
    if _contains_any(
        combined,
        (
            "security",
            "auth",
            "permission",
            "compliance",
            "audit",
            "vulnerability",
            "encryption",
        ),
    ):
        return TaskBundleRole.security
    if _contains_any(combined, ("qa", "test", "regression", "edge case", "validation")):
        return TaskBundleRole.qa
    if _contains_any(
        combined,
        (
            "frontend",
            "front-end",
            "fe",
            "ui",
            "ux",
            "page",
            "screen",
            "client",
            "browser",
            "react",
            "vue",
        ),
    ):
        return TaskBundleRole.frontend
    return TaskBundleRole.backend


def _infer_priority(*parts: str) -> TaskBundlePriority:
    combined = " ".join(str(part or "") for part in parts).lower()
    if _contains_any(
        combined,
        (
            "high",
            "critical",
            "blocker",
            "security",
            "risk",
            "auth",
            "permission",
            "timeout",
            "ambiguous",
        ),
    ):
        return TaskBundlePriority.high
    if _contains_any(combined, ("low", "minor", "nice to have")):
        return TaskBundlePriority.low
    return TaskBundlePriority.medium


def _make_task_id(role: TaskBundleRole, index: int) -> str:
    return f"TB-{role.value[:2].upper()}-{index:03d}"


class TaskBundleBuilder:
    """Assemble a lightweight role-based task bundle for iterative follow-up work."""

    pack_type = "task_bundle_v1"

    def build(
        self,
        *,
        run_id: str,
        source_artifacts: list[str] | None,
        requirements: list[dict[str, Any]] | list[BaseModel] | None,
        tasks: list[dict[str, Any]] | list[BaseModel] | None,
        risks: list[dict[str, Any]] | list[BaseModel] | None,
        implementation_plan_output: ImplementationPlanOutput | JsonDict | None,
        test_plan_output: QaPlanningOutput | JsonDict | None,
        codex_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        claude_code_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        review_findings: list[dict[str, Any]] | list[BaseModel] | None = None,
        open_questions: list[dict[str, Any]] | list[BaseModel] | None = None,
        risk_items: list[dict[str, Any]] | list[BaseModel] | None = None,
        generated_at: str = "",
    ) -> TaskBundleV1:
        requirements_list = _as_dict_list(requirements)
        tasks_list = _as_dict_list(tasks)
        risks_list = _as_dict_list(risks)
        implementation_plan = _as_dict(implementation_plan_output)
        test_plan = _as_dict(test_plan_output)
        codex_prompt = _as_dict(codex_prompt_output)
        claude_code_prompt = _as_dict(claude_code_prompt_output)
        data = {
            "requirements": requirements_list,
            "tasks": tasks_list,
            "risks": risks_list,
            "implementation_plan": implementation_plan,
            "test_plan": test_plan,
            "codex_prompt": codex_prompt,
            "claude_code_prompt": claude_code_prompt,
        }
        findings_list = _as_dict_list(review_findings)
        question_list = _as_dict_list(open_questions)
        risk_item_list = _as_dict_list(risk_items)

        tasks_by_role: dict[str, list[dict[str, Any]]] = {
            TaskBundleRole.backend.value: [],
            TaskBundleRole.frontend.value: [],
            TaskBundleRole.qa.value: [],
            TaskBundleRole.security.value: [],
        }
        counters: dict[TaskBundleRole, int] = {
            TaskBundleRole.backend: 1,
            TaskBundleRole.frontend: 1,
            TaskBundleRole.qa: 1,
            TaskBundleRole.security: 1,
        }

        def append_task(
            *,
            role: TaskBundleRole,
            title: str,
            description: str,
            priority: TaskBundlePriority,
            prd_refs: list[str],
            context: list[str],
            depends_on: list[str],
            source_type: TaskBundleSourceType,
        ) -> str:
            task_id = _make_task_id(role, counters[role])
            counters[role] += 1
            task = TaskBundleTask.model_validate(
                {
                    "task_id": task_id,
                    "role": role,
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "prd_refs": _unique_strings(prd_refs),
                    "context": _unique_strings(context),
                    "depends_on": _unique_strings(depends_on),
                    "source_type": source_type,
                }
            )
            tasks_by_role[role.value].append(task.model_dump(mode="python"))
            return task_id

        backend_frontend_ids: list[str] = []
        role_plan_ids: dict[TaskBundleRole, list[str]] = {role: [] for role in counters}

        for task in data["tasks"]:
            role = _classify_role(
                str(task.get("owner", "") or ""),
                str(task.get("title", "") or ""),
                str(task.get("description", "") or ""),
            )
            title = str(
                task.get("title", "") or task.get("id", "") or "Plan task"
            ).strip()
            description = str(task.get("description", "") or title).strip()
            priority = _infer_priority(
                title, description, str(task.get("owner", "") or "")
            )
            prd_refs = [
                str(item).strip()
                for item in list(task.get("requirement_ids", []) or [])
                if str(item).strip()
            ]
            context = (
                [f"Plan owner: {str(task.get('owner', '') or '').strip()}"]
                if str(task.get("owner", "") or "").strip()
                else []
            )
            task_id = append_task(
                role=role,
                title=title,
                description=description,
                priority=priority,
                prd_refs=prd_refs or ["Implementation Plan"],
                context=context,
                depends_on=[
                    str(item).strip()
                    for item in list(task.get("depends_on", []) or [])
                    if str(item).strip()
                ],
                source_type=TaskBundleSourceType.plan,
            )
            role_plan_ids[role].append(task_id)
            if role in {TaskBundleRole.backend, TaskBundleRole.frontend}:
                backend_frontend_ids.append(task_id)

        target_modules = [
            str(item).strip()
            for item in list(
                data["implementation_plan"].get("target_modules", []) or []
            )
            if str(item).strip()
        ]
        impl_steps = [
            str(item).strip()
            for item in list(
                data["implementation_plan"].get("implementation_steps", []) or []
            )
            if str(item).strip()
        ]
        impl_constraints = [
            str(item).strip()
            for item in list(data["implementation_plan"].get("constraints", []) or [])
            if str(item).strip()
        ]

        if target_modules:
            frontend_modules = [
                item
                for item in target_modules
                if _classify_role(item) == TaskBundleRole.frontend
            ]
            backend_modules = [
                item
                for item in target_modules
                if _classify_role(item) == TaskBundleRole.backend
            ]
            if backend_modules and not role_plan_ids[TaskBundleRole.backend]:
                task_id = append_task(
                    role=TaskBundleRole.backend,
                    title="Apply backend implementation plan updates",
                    description="Execute backend-oriented implementation steps derived from the review plan.",
                    priority=_infer_priority(" ".join(impl_steps + impl_constraints)),
                    prd_refs=["Implementation Plan", "Dependencies"],
                    context=backend_modules + impl_steps[:3] + impl_constraints[:2],
                    depends_on=[],
                    source_type=TaskBundleSourceType.plan,
                )
                role_plan_ids[TaskBundleRole.backend].append(task_id)
                backend_frontend_ids.append(task_id)
            if frontend_modules and not role_plan_ids[TaskBundleRole.frontend]:
                task_id = append_task(
                    role=TaskBundleRole.frontend,
                    title="Apply frontend implementation plan updates",
                    description="Execute frontend-oriented implementation steps derived from the review plan.",
                    priority=_infer_priority(" ".join(frontend_modules + impl_steps)),
                    prd_refs=["Implementation Plan", "Scope"],
                    context=frontend_modules + impl_steps[:3] + impl_constraints[:2],
                    depends_on=role_plan_ids[TaskBundleRole.backend][:1],
                    source_type=TaskBundleSourceType.plan,
                )
                role_plan_ids[TaskBundleRole.frontend].append(task_id)
                backend_frontend_ids.append(task_id)

        test_scope = [
            str(item).strip()
            for item in list(data["test_plan"].get("test_scope", []) or [])
            if str(item).strip()
        ]
        edge_cases = [
            str(item).strip()
            for item in list(data["test_plan"].get("edge_cases", []) or [])
            if str(item).strip()
        ]
        regression_focus = [
            str(item).strip()
            for item in list(data["test_plan"].get("regression_focus", []) or [])
            if str(item).strip()
        ]
        if test_scope or edge_cases or regression_focus:
            role_plan_ids[TaskBundleRole.qa].append(
                append_task(
                    role=TaskBundleRole.qa,
                    title="Validate test scope and regression coverage",
                    description="Execute the planned QA scope, edge cases, and regression checks for this review run.",
                    priority=_infer_priority(" ".join(edge_cases + regression_focus)),
                    prd_refs=["Test Plan", "Edge Cases", "Acceptance Criteria"],
                    context=test_scope[:4] + edge_cases[:4] + regression_focus[:3],
                    depends_on=backend_frontend_ids,
                    source_type=TaskBundleSourceType.plan,
                )
            )

        for finding in findings_list[:8]:
            detail = str(
                finding.get("detail", "") or finding.get("suggestion", "") or ""
            ).strip()
            title = str(
                finding.get("title", "")
                or finding.get("requirement_id", "")
                or "Review finding"
            ).strip()
            if not detail and not title:
                continue
            role = _classify_role(
                title, detail, str(finding.get("description", "") or "")
            )
            role_plan_ids[role].append(
                append_task(
                    role=role,
                    title=f"Address finding: {title}",
                    description=detail
                    or "Convert this finding into an explicit implementation update.",
                    priority=_infer_priority(title, detail),
                    prd_refs=_unique_strings(
                        [
                            str(finding.get("requirement_id", "") or "").strip(),
                            "Acceptance Criteria",
                            "Open Questions",
                        ]
                    ),
                    context=_unique_strings(
                        [
                            str(finding.get("description", "") or "").strip(),
                            str(finding.get("suggestion", "") or "").strip(),
                        ]
                    ),
                    depends_on=role_plan_ids[role][:1],
                    source_type=TaskBundleSourceType.finding,
                )
            )

        for question in question_list[:8]:
            question_text = str(
                question.get("question", "") or question.get("detail", "") or ""
            ).strip()
            if not question_text:
                continue
            role = _classify_role(
                question_text,
                " ".join(str(item) for item in list(question.get("issues", []) or [])),
            )
            role_plan_ids[role].append(
                append_task(
                    role=role,
                    title=f"Resolve open question: {question_text[:80]}",
                    description=question_text,
                    priority=_infer_priority(
                        question_text,
                        " ".join(
                            str(item) for item in list(question.get("issues", []) or [])
                        ),
                    ),
                    prd_refs=["Open Questions", "Scope"],
                    context=_unique_strings(
                        [
                            str(item).strip()
                            for item in list(question.get("issues", []) or [])
                            if str(item).strip()
                        ]
                        + [
                            f"Reviewers: {', '.join(str(item).strip() for item in list(question.get('reviewers', []) or []) if str(item).strip())}"
                        ]
                    ),
                    depends_on=role_plan_ids[role][:1],
                    source_type=TaskBundleSourceType.open_question,
                )
            )

        for risk in risk_item_list[:8]:
            detail = str(
                risk.get("detail", "") or risk.get("description", "") or ""
            ).strip()
            title = str(
                risk.get("title", "") or risk.get("id", "") or "Risk item"
            ).strip()
            role = _classify_role(title, detail, str(risk.get("category", "") or ""))
            depends_on = (
                backend_frontend_ids
                if role == TaskBundleRole.qa
                else role_plan_ids[TaskBundleRole.backend][:1]
            )
            role_plan_ids[role].append(
                append_task(
                    role=role,
                    title=f"Mitigate risk: {title}",
                    description=detail
                    or "Reduce the risk signaled by the review output.",
                    priority=_infer_priority(
                        title, detail, str(risk.get("severity", "") or "")
                    ),
                    prd_refs=["Risks", "Dependencies"],
                    context=_unique_strings(
                        [
                            str(risk.get("mitigation", "") or "").strip(),
                            str(risk.get("category", "") or "").strip(),
                            str(
                                risk.get("severity", "") or risk.get("impact", "") or ""
                            ).strip(),
                        ]
                    ),
                    depends_on=depends_on,
                    source_type=TaskBundleSourceType.risk,
                )
            )

        return TaskBundleV1.model_validate(
            {
                "run_id": run_id,
                "version": 1,
                "generated_at": generated_at,
                "source_artifacts": _unique_strings(source_artifacts or []),
                "tasks_by_role": TaskBundleTasksByRole.model_validate(tasks_by_role),
            }
        )


class _BasePackBuilder:
    pack_type: str

    def _normalize_inputs(
        self,
        *,
        requirements: list[dict[str, Any]] | list[BaseModel] | None,
        tasks: list[dict[str, Any]] | list[BaseModel] | None,
        risks: list[dict[str, Any]] | list[BaseModel] | None,
        implementation_plan_output: ImplementationPlanOutput | JsonDict | None,
        test_plan_output: QaPlanningOutput | JsonDict | None,
        codex_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        claude_code_prompt_output: CodingAgentPromptOutput | JsonDict | None,
    ) -> JsonDict:
        requirements_list = _as_dict_list(requirements)
        tasks_list = _as_dict_list(tasks)
        risks_list = _as_dict_list(risks)
        implementation_plan = _as_dict(implementation_plan_output)
        test_plan = _as_dict(test_plan_output)
        codex_prompt = _as_dict(codex_prompt_output)
        claude_code_prompt = _as_dict(claude_code_prompt_output)
        primary_task = tasks_list[0] if tasks_list else {}
        primary_requirement = requirements_list[0] if requirements_list else {}

        return {
            "requirements": requirements_list,
            "tasks": tasks_list,
            "risks": risks_list,
            "implementation_plan": implementation_plan,
            "test_plan": test_plan,
            "codex_prompt": codex_prompt,
            "claude_code_prompt": claude_code_prompt,
            "primary_task": primary_task,
            "primary_requirement": primary_requirement,
            "task_id": _first_non_empty(
                str(primary_task.get("id", "")).strip(),
                str(primary_requirement.get("id", "")).strip(),
                "handoff-task",
            ),
            "title": _first_non_empty(
                str(primary_task.get("title", "")).strip(),
                str(primary_requirement.get("description", "")).strip(),
                f"{self.pack_type.replace('_', ' ').title()}",
            ),
            "summary": _first_non_empty(
                str(primary_requirement.get("description", "")).strip(),
                str(primary_task.get("title", "")).strip(),
                "Multi-skill handoff pack",
            ),
            "acceptance_criteria": _collect_acceptance_criteria(requirements_list),
        }


class ImplementationPackBuilder(_BasePackBuilder):
    """Assemble an implementation-focused handoff pack from multiple skill outputs."""

    pack_type = "implementation_pack"

    def build(
        self,
        *,
        requirements: list[dict[str, Any]] | list[BaseModel] | None,
        tasks: list[dict[str, Any]] | list[BaseModel] | None,
        risks: list[dict[str, Any]] | list[BaseModel] | None,
        implementation_plan_output: ImplementationPlanOutput | JsonDict | None,
        test_plan_output: QaPlanningOutput | JsonDict | None,
        codex_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        claude_code_prompt_output: CodingAgentPromptOutput | JsonDict | None,
    ) -> ImplementationPack:
        data = self._normalize_inputs(
            requirements=requirements,
            tasks=tasks,
            risks=risks,
            implementation_plan_output=implementation_plan_output,
            test_plan_output=test_plan_output,
            codex_prompt_output=codex_prompt_output,
            claude_code_prompt_output=claude_code_prompt_output,
        )
        context_sections = [
            _json_block("Requirements", data["requirements"]),
            _json_block("Tasks", data["tasks"]),
            _json_block("Risks", data["risks"]),
        ]
        handoff_notes = [
            data["codex_prompt"].get("agent_prompt", ""),
            data["claude_code_prompt"].get("agent_prompt", ""),
        ]
        handoff_notes.extend(data["codex_prompt"].get("non_goals", []) or [])
        handoff_notes.extend(data["claude_code_prompt"].get("non_goals", []) or [])

        return ImplementationPack.model_validate(
            {
                "pack_type": self.pack_type,
                "task_id": data["task_id"],
                "title": data["title"],
                "summary": data["summary"],
                "context": "\n\n".join(
                    section for section in context_sections if section
                ),
                "target_modules": data["implementation_plan"].get("target_modules", [])
                or [],
                "implementation_steps": data["implementation_plan"].get(
                    "implementation_steps", []
                )
                or [],
                "constraints": data["implementation_plan"].get("constraints", []) or [],
                "acceptance_criteria": data["acceptance_criteria"],
                "recommended_skills": [
                    "implementation.plan",
                    "codex.prompt.generate",
                    "claude_code.prompt.generate",
                ],
                "agent_handoff": AgentHandoff.model_validate(
                    {
                        "primary_agent": "codex",
                        "supporting_agents": ["claude_code"],
                        "goals": data["codex_prompt"].get(
                            "recommended_execution_order", []
                        )
                        or [],
                        "expected_output": data["codex_prompt"].get("agent_prompt", ""),
                        "notes": [note for note in handoff_notes if note],
                    }
                ),
            }
        )


class TestPackBuilder(_BasePackBuilder):
    """Assemble a test-focused handoff pack from planning and prompt outputs."""

    pack_type = "test_pack"

    def build(
        self,
        *,
        requirements: list[dict[str, Any]] | list[BaseModel] | None,
        tasks: list[dict[str, Any]] | list[BaseModel] | None,
        risks: list[dict[str, Any]] | list[BaseModel] | None,
        implementation_plan_output: ImplementationPlanOutput | JsonDict | None,
        test_plan_output: QaPlanningOutput | JsonDict | None,
        codex_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        claude_code_prompt_output: CodingAgentPromptOutput | JsonDict | None,
    ) -> TestPack:
        data = self._normalize_inputs(
            requirements=requirements,
            tasks=tasks,
            risks=risks,
            implementation_plan_output=implementation_plan_output,
            test_plan_output=test_plan_output,
            codex_prompt_output=codex_prompt_output,
            claude_code_prompt_output=claude_code_prompt_output,
        )
        acceptance_criteria = list(data["acceptance_criteria"])
        for item in data["codex_prompt"].get("validation_checklist", []) or []:
            if item not in acceptance_criteria:
                acceptance_criteria.append(item)
        for item in data["claude_code_prompt"].get("validation_checklist", []) or []:
            if item not in acceptance_criteria:
                acceptance_criteria.append(item)

        return TestPack.model_validate(
            {
                "pack_type": self.pack_type,
                "task_id": data["task_id"],
                "title": data["title"],
                "summary": data["summary"],
                "test_scope": data["test_plan"].get("test_scope", []) or [],
                "edge_cases": (data["test_plan"].get("edge_cases", []) or [])
                + (data["test_plan"].get("regression_focus", []) or []),
                "acceptance_criteria": acceptance_criteria,
                "agent_handoff": AgentHandoff.model_validate(
                    {
                        "primary_agent": "claude_code",
                        "supporting_agents": ["codex"],
                        "goals": data["claude_code_prompt"].get(
                            "recommended_execution_order", []
                        )
                        or [],
                        "expected_output": data["claude_code_prompt"].get(
                            "agent_prompt", ""
                        ),
                        "notes": [
                            note
                            for note in [
                                data["codex_prompt"].get("agent_prompt", ""),
                                data["claude_code_prompt"].get("agent_prompt", ""),
                            ]
                            if note
                        ],
                    }
                ),
            }
        )


class ExecutionPackBuilder(_BasePackBuilder):
    """Combine implementation, test and risk handoff details into one execution pack."""

    pack_type = "execution_pack"

    def __init__(
        self,
        implementation_builder: ImplementationPackBuilder | None = None,
        test_builder: TestPackBuilder | None = None,
    ) -> None:
        self.implementation_builder = (
            implementation_builder or ImplementationPackBuilder()
        )
        self.test_builder = test_builder or TestPackBuilder()

    def build(
        self,
        *,
        requirements: list[dict[str, Any]] | list[BaseModel] | None,
        tasks: list[dict[str, Any]] | list[BaseModel] | None,
        risks: list[dict[str, Any]] | list[BaseModel] | None,
        implementation_plan_output: ImplementationPlanOutput | JsonDict | None,
        test_plan_output: QaPlanningOutput | JsonDict | None,
        codex_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        claude_code_prompt_output: CodingAgentPromptOutput | JsonDict | None,
        handoff_strategy: str = "sequential",
    ) -> ExecutionPack:
        data = self._normalize_inputs(
            requirements=requirements,
            tasks=tasks,
            risks=risks,
            implementation_plan_output=implementation_plan_output,
            test_plan_output=test_plan_output,
            codex_prompt_output=codex_prompt_output,
            claude_code_prompt_output=claude_code_prompt_output,
        )
        implementation_pack = self.implementation_builder.build(
            requirements=data["requirements"],
            tasks=data["tasks"],
            risks=data["risks"],
            implementation_plan_output=data["implementation_plan"],
            test_plan_output=data["test_plan"],
            codex_prompt_output=data["codex_prompt"],
            claude_code_prompt_output=data["claude_code_prompt"],
        )
        test_pack = self.test_builder.build(
            requirements=data["requirements"],
            tasks=data["tasks"],
            risks=data["risks"],
            implementation_plan_output=data["implementation_plan"],
            test_plan_output=data["test_plan"],
            codex_prompt_output=data["codex_prompt"],
            claude_code_prompt_output=data["claude_code_prompt"],
        )
        risk_pack = [
            RiskSummaryItem.model_validate(
                {
                    "id": risk.get("id", f"risk-{index + 1}"),
                    "summary": risk.get("summary", "") or risk.get("description", ""),
                    "level": risk.get("level", "") or risk.get("impact", "medium"),
                    "mitigation": risk.get("mitigation", ""),
                    "owner": risk.get("owner", ""),
                }
            )
            for index, risk in enumerate(data["risks"])
        ]

        return ExecutionPack.model_validate(
            {
                "pack_type": self.pack_type,
                "pack_version": "1.0",
                "implementation_pack": implementation_pack,
                "test_pack": test_pack,
                "risk_pack": risk_pack,
                "handoff_strategy": handoff_strategy,
            }
        )


def build_implementation_pack(**data: object) -> ImplementationPack:
    """Validate and create an implementation pack."""

    return ImplementationPack.model_validate(data)


def build_test_pack(**data: object) -> TestPack:
    """Validate and create a test pack."""

    return TestPack.model_validate(data)


def build_execution_pack(
    implementation_pack: ImplementationPack | dict[str, object],
    test_pack: TestPack | dict[str, object],
    risk_pack: list[RiskSummaryItem | dict[str, object]] | None = None,
    handoff_strategy: str = "sequential",
    pack_type: str = "execution_pack",
    pack_version: str = "1.0",
) -> ExecutionPack:
    """Validate and create a full execution pack."""

    risk_items = risk_pack or []
    return ExecutionPack.model_validate(
        {
            "pack_type": pack_type,
            "pack_version": pack_version,
            "implementation_pack": implementation_pack,
            "test_pack": test_pack,
            "risk_pack": risk_items,
            "handoff_strategy": handoff_strategy,
        }
    )
