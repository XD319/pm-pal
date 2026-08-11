from __future__ import annotations
from collections.abc import Awaitable, Callable
import os
from typing import Any
from pydantic import BaseModel, Field
from pm_pal.utils.llm_structured_call import StructuredCallError, llm_structured_call
from .skills import Skill, SkillKind, default_skill_registry
class AgentRuntimeError(RuntimeError): pass
class PlannedStep(BaseModel):
    skill_id: str
    purpose: str = Field(min_length=1, max_length=500)
    inputs: dict[str, Any] = Field(default_factory=dict)
class RuntimePlan(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    steps: list[PlannedStep] = Field(min_length=1, max_length=6)
Planner = Callable[[str, type[RuntimePlan]], Awaitable[dict[str, Any]]]
EventSink = Callable[[dict[str, Any]], Awaitable[None]]
class AgentRuntime:
    """Plan with the configured model; execute only registered capabilities."""
    def __init__(self, *, skills: dict[str, Skill] | None = None, planner: Planner | None = None) -> None:
        self.skills = skills or default_skill_registry(); self.planner = planner or self._model_plan
    async def run(self, *, request: str, product: dict[str, Any], evidence: list[dict[str, Any]], context: dict[str, Any], emit: EventSink) -> dict[str, Any]:
        await emit({"type":"progress", "phase":"planning", "message":"Planning task with configured model."})
        degraded_reason = ""
        try:
            plan = await self._create_plan(request, product, evidence, context)
        except AgentRuntimeError as exc:
            if "unregistered skills" in str(exc) or not self._offline_fallback_enabled():
                raise
            degraded_reason = str(exc)
            plan = self._offline_plan(request)
            await emit({"type": "warning", "phase": "planning", "message": "Configured model is unavailable; completed this task with the local fallback plan."})
        await emit({"type":"plan", "summary":plan.summary, "steps":[step.model_dump() for step in plan.steps]})
        artifacts, proposals = [], []
        for index, step in enumerate(plan.steps, 1):
            skill = self.skills[step.skill_id]
            await emit({"type":"progress", "phase":"running", "step":index, "skill":skill.id, "message":step.purpose})
            (proposals if skill.may_write_external else artifacts).append(self._execute_skill(skill, request, step.purpose, evidence, step.inputs))
        await emit({"type":"progress", "phase":"complete", "message":"Task completed."})
        return {"summary":plan.summary, "plan":[step.model_dump() for step in plan.steps], "artifacts":artifacts, "proposals":proposals, "evidence_count":len(evidence), "execution_mode":"offline_fallback" if degraded_reason else "model", "model_error":degraded_reason}

    @staticmethod
    def _offline_fallback_enabled() -> bool:
        return os.getenv("PM_PAL_OFFLINE_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}

    def _offline_plan(self, request: str) -> RuntimePlan:
        """Produce a safe, local-only plan when the configured model cannot be reached."""
        normalized = request.casefold()
        if "prd" in normalized or "需求" in normalized or "规格" in normalized:
            skill_id, purpose = "draft_prd", "Create a traceable PRD draft from the supplied request and authorised evidence."
        elif any(keyword in normalized for keyword in ("反馈", "分析", "总结", "汇总", "insight", "feedback")):
            skill_id, purpose = "synthesize_evidence", "Synthesize the authorised evidence and identify the key findings."
        else:
            skill_id, purpose = "plan_work", "Create an ordered local work plan for the requested product task."
        return RuntimePlan(summary="Local fallback plan (configured model unavailable).", steps=[PlannedStep(skill_id=skill_id, purpose=purpose)])
    async def _create_plan(self, request: str, product: dict[str, Any], evidence: list[dict[str, Any]], context: dict[str, Any]) -> RuntimePlan:
        catalogue = [{"id":s.id,"kind":s.kind,"description":s.description,"may_write_external":s.may_write_external} for s in self.skills.values()]
        prompt = f"You are a PM agent planner. Produce a minimal plan using ONLY the skill ids below. Do not invent tools. Use external-write only when explicitly asked to write or sync externally.\nTask: {request}\nProduct: {product}\nAuthorised evidence count: {len(evidence)}\nSkills: {catalogue}"
        try: plan = RuntimePlan.model_validate(await self.planner(prompt, RuntimePlan))
        except (StructuredCallError, ValueError, TypeError) as exc: raise AgentRuntimeError(f"Model planning failed: {exc}") from exc
        invalid = [step.skill_id for step in plan.steps if step.skill_id not in self.skills]
        if invalid: raise AgentRuntimeError(f"Model selected unregistered skills: {', '.join(invalid)}")
        return plan
    async def _model_plan(self, prompt: str, schema: type[RuntimePlan]) -> dict[str, Any]:
        return await llm_structured_call(prompt=prompt, schema=schema, metadata={"agent_name":"pm_agent_runtime", "run_id":""})
    @staticmethod
    def _execute_skill(skill: Skill, request: str, purpose: str, evidence: list[dict[str, Any]], inputs: dict[str, Any]) -> dict[str, Any]:
        refs = [str(item["id"]) for item in evidence if item.get("id")]
        if skill.may_write_external: return {"resource_type":"deliveries", "target_system":str(inputs.get("target_system") or "notion"), "title":purpose, "content":str(inputs.get("content") or request), "evidence_ids":refs}
        kind = {SkillKind.insight:"insight", SkillKind.authoring:"document", SkillKind.execution:"task_plan"}[skill.kind]
        return {"type":kind, "skill_id":skill.id, "title":purpose, "content":str(inputs.get("content") or request), "evidence_ids":refs}
