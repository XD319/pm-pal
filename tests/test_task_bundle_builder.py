import json

from pm_pal.packs import TaskBundleBuilder, TaskBundleV1
from pm_pal.task_bundle_generator import (
    generate_task_bundle_v1_artifact,
    validate_task_bundle_v1_payload,
)


def test_task_bundle_builder_groups_tasks_by_role_and_source_type() -> None:
    bundle = TaskBundleBuilder().build(
        run_id="20260412T040000Z",
        generated_at="2026-04-12T04:00:00+00:00",
        source_artifacts=["report.json", "review_report.json"],
        requirements=[
            {
                "id": "REQ-001",
                "description": "Support recruiter shortlist decisions",
                "acceptance_criteria": ["Decision is persisted"],
            }
        ],
        tasks=[
            {
                "id": "TASK-001",
                "title": "Implement shortlist API",
                "owner": "BE",
                "requirement_ids": ["REQ-001"],
            },
            {
                "id": "TASK-002",
                "title": "Build shortlist UI",
                "owner": "FE",
                "requirement_ids": ["REQ-001"],
                "depends_on": ["TASK-001"],
            },
        ],
        risks=[
            {
                "id": "RISK-001",
                "description": "Authorization may regress",
                "impact": "high",
                "mitigation": "Review access control",
            }
        ],
        implementation_plan_output={
            "implementation_steps": ["Add API", "Patch UI"],
            "target_modules": ["backend/shortlist.py", "frontend/src/shortlist.tsx"],
            "constraints": ["Preserve existing recruiter flow"],
        },
        test_plan_output={
            "test_scope": ["Shortlist API", "Shortlist page"],
            "edge_cases": ["Duplicate submit"],
            "regression_focus": ["Recruiter login"],
        },
        codex_prompt_output={},
        claude_code_prompt_output={},
        review_findings=[
            {
                "title": "REQ-001",
                "detail": "Decision SLA is not measurable",
                "suggestion": "Define a measurable SLA.",
                "requirement_id": "REQ-001",
            }
        ],
        open_questions=[
            {
                "question": "Clarify empty state for shortlist page",
                "reviewers": ["product"],
            }
        ],
        risk_items=[
            {
                "title": "Audit coverage",
                "detail": "Audit detail may be incomplete",
                "severity": "medium",
                "mitigation": "Persist actor and outcome",
            }
        ],
    )

    assert isinstance(bundle, TaskBundleV1)
    assert bundle.version == 1
    assert bundle.tasks_by_role.backend
    assert bundle.tasks_by_role.frontend
    assert bundle.tasks_by_role.qa
    assert bundle.tasks_by_role.security
    assert bundle.tasks_by_role.backend[0].source_type == "plan"
    assert any(task.source_type == "finding" for task in bundle.tasks_by_role.backend)
    assert any(
        task.source_type == "open_question" for task in bundle.tasks_by_role.frontend
    )
    assert any(task.source_type == "risk" for task in bundle.tasks_by_role.security)


def test_generate_task_bundle_v1_artifact_writes_valid_json(tmp_path) -> None:
    run_dir = tmp_path / "20260412T040100Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "run_id": "20260412T040100Z",
        "parsed_items": [
            {
                "id": "REQ-001",
                "description": "Support shortlist flow",
                "acceptance_criteria": ["Persist decisions"],
            }
        ],
        "tasks": [
            {
                "id": "TASK-001",
                "title": "Implement shortlist API",
                "owner": "BE",
                "requirement_ids": ["REQ-001"],
            }
        ],
        "implementation_plan": {
            "implementation_steps": ["Implement API"],
            "target_modules": ["backend/shortlist.py"],
            "constraints": [],
        },
        "test_plan": {
            "test_scope": ["API"],
            "edge_cases": ["Duplicate submit"],
            "regression_focus": [],
        },
        "review_results": [],
        "risks": [],
        "trace": {},
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "open_questions.json").write_text(
        json.dumps(
            {"open_questions": [{"question": "Clarify retry behavior"}]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "risk_items.json").write_text(
        json.dumps(
            {
                "risk_items": [
                    {
                        "title": "Auth drift",
                        "detail": "Permission check could drift",
                        "severity": "high",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    artifact = generate_task_bundle_v1_artifact(
        {
            "run_id": "20260412T040100Z",
            "run_dir": str(run_dir),
            "report_paths": {"report_json": str(run_dir / "report.json")},
            "result": dict(report_payload),
        }
    )

    payload = json.loads((run_dir / "task_bundle_v1.json").read_text(encoding="utf-8"))
    validated = validate_task_bundle_v1_payload(payload)
    assert artifact.artifact_key == "task_bundle_v1"
    assert artifact.trace["output_paths"]["task_bundle_v1"].endswith(
        "task_bundle_v1.json"
    )
    assert validated.run_id == "20260412T040100Z"
    assert validated.tasks_by_role.backend
