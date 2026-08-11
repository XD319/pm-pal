import pytest
from pydantic import ValidationError

from pm_pal.packs import (
    ExecutionPack,
    ImplementationPack,
    TaskBundleV1,
    TestPack as SchemaTestPack,
    build_execution_pack,
)


def test_implementation_pack_validates_required_fields() -> None:
    pack = ImplementationPack.model_validate(
        {
            "pack_type": "implementation_pack",
            "task_id": "TASK-001",
            "title": "Add task pack schemas",
            "summary": "Define structured handoff payloads for coding agents.",
            "context": "Used by Codex and Claude Code handoff flows.",
            "target_modules": ["pm_pal/packs/schemas.py"],
            "implementation_steps": ["Define models", "Export builders"],
            "constraints": ["Do not break existing workflow"],
            "acceptance_criteria": ["Schemas validate nested execution packs"],
            "recommended_skills": ["pydantic", "pytest"],
            "agent_handoff": {
                "primary_agent": "codex",
                "goals": ["Implement schema package"],
                "expected_output": "Merged code with passing tests",
            },
        }
    )

    assert pack.task_id == "TASK-001"
    assert pack.agent_handoff.primary_agent == "codex"


def test_execution_pack_nests_implementation_and_test_packs() -> None:
    implementation_pack = {
        "pack_type": "implementation_pack",
        "task_id": "TASK-001",
        "title": "Implement feature",
        "summary": "Feature summary",
        "context": "Project context",
        "target_modules": ["a.py"],
        "implementation_steps": ["step 1"],
        "constraints": ["constraint 1"],
        "acceptance_criteria": ["criterion 1"],
        "recommended_skills": ["python"],
        "agent_handoff": {"primary_agent": "codex"},
    }
    test_pack = {
        "pack_type": "test_pack",
        "task_id": "TASK-001",
        "title": "Test feature",
        "summary": "Test summary",
        "test_scope": ["schema validation"],
        "edge_cases": ["missing required field"],
        "acceptance_criteria": ["tests pass"],
        "agent_handoff": {"primary_agent": "claude_code"},
    }

    pack = build_execution_pack(
        implementation_pack=implementation_pack,
        test_pack=test_pack,
        risk_pack=[
            {"id": "RISK-001", "summary": "Integration drift", "level": "medium"}
        ],
    )

    assert isinstance(pack, ExecutionPack)
    assert isinstance(pack.implementation_pack, ImplementationPack)
    assert isinstance(pack.test_pack, SchemaTestPack)
    assert pack.risk_pack[0].level == "medium"


def test_implementation_pack_requires_task_id() -> None:
    with pytest.raises(ValidationError):
        ImplementationPack.model_validate(
            {
                "pack_type": "implementation_pack",
                "title": "Missing task id",
                "summary": "Should fail",
                "context": "Context",
                "target_modules": [],
                "implementation_steps": [],
                "constraints": [],
                "acceptance_criteria": [],
                "recommended_skills": [],
                "agent_handoff": {},
            }
        )


def test_task_bundle_v1_schema_validates_role_partitions() -> None:
    bundle = TaskBundleV1.model_validate(
        {
            "run_id": "20260412T041500Z",
            "version": 1,
            "generated_at": "2026-04-12T04:15:00+00:00",
            "source_artifacts": ["report.json"],
            "tasks_by_role": {
                "backend": [
                    {
                        "task_id": "TB-BE-001",
                        "role": "backend",
                        "title": "Implement API",
                        "description": "Add backend support",
                        "priority": "high",
                        "prd_refs": ["REQ-001"],
                        "context": ["Derived from plan"],
                        "depends_on": [],
                        "source_type": "plan",
                    }
                ],
                "frontend": [],
                "qa": [],
                "security": [],
            },
        }
    )

    assert bundle.version == 1
    assert bundle.tasks_by_role.backend[0].role == "backend"
