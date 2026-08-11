"""Structured task pack schemas for coding-agent handoff."""

from enum import Enum

from pydantic import Field

from pm_pal.schemas.base import ID, AgentSchemaModel, RiskLevel


class AgentHandoff(AgentSchemaModel):
    """Instructions that help a downstream coding agent execute the task."""

    primary_agent: str = ""
    supporting_agents: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    expected_output: str = ""
    notes: list[str] = Field(default_factory=list)


class RiskSummaryItem(AgentSchemaModel):
    """Compact risk item attached to an execution pack."""

    id: ID
    summary: str = ""
    level: RiskLevel = RiskLevel.medium
    mitigation: str = ""
    owner: str = ""


class BaseTaskPack(AgentSchemaModel):
    """Common fields shared by task-oriented handoff packs."""

    pack_type: str
    pack_version: str = "1.0"
    task_id: ID
    title: str = ""
    summary: str = ""


class ImplementationPack(BaseTaskPack):
    """Implementation-focused instructions for a coding agent."""

    context: str = ""
    target_modules: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    recommended_skills: list[str] = Field(default_factory=list)
    agent_handoff: AgentHandoff = Field(default_factory=AgentHandoff)


class TestPack(BaseTaskPack):
    """Testing-focused instructions for a coding agent."""

    test_scope: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    agent_handoff: AgentHandoff = Field(default_factory=AgentHandoff)


class ExecutionPack(AgentSchemaModel):
    """Combined handoff pack used to execute and validate delivery."""

    pack_type: str = "execution_pack"
    pack_version: str = "1.0"
    implementation_pack: ImplementationPack
    test_pack: TestPack
    risk_pack: list[RiskSummaryItem] = Field(default_factory=list)
    handoff_strategy: str = "sequential"


class TaskBundleRole(str, Enum):
    backend = "backend"
    frontend = "frontend"
    qa = "qa"
    security = "security"


class TaskBundlePriority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class TaskBundleSourceType(str, Enum):
    finding = "finding"
    open_question = "open_question"
    risk = "risk"
    plan = "plan"


class TaskBundleTask(AgentSchemaModel):
    task_id: ID
    role: TaskBundleRole
    title: str = ""
    description: str = ""
    priority: TaskBundlePriority = TaskBundlePriority.medium
    prd_refs: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    source_type: TaskBundleSourceType = TaskBundleSourceType.plan


class TaskBundleTasksByRole(AgentSchemaModel):
    backend: list[TaskBundleTask] = Field(default_factory=list)
    frontend: list[TaskBundleTask] = Field(default_factory=list)
    qa: list[TaskBundleTask] = Field(default_factory=list)
    security: list[TaskBundleTask] = Field(default_factory=list)


class TaskBundleV1(AgentSchemaModel):
    run_id: str = ""
    version: int = 1
    generated_at: str = ""
    source_artifacts: list[str] = Field(default_factory=list)
    tasks_by_role: TaskBundleTasksByRole = Field(default_factory=TaskBundleTasksByRole)
