from __future__ import annotations

import inspect
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

# Isolate tests from local operator .env / live model credentials. :-)
os.environ.setdefault("PM_PAL_API_AUTH_DISABLED", "true")
os.environ.setdefault("MARRDP_API_AUTH_DISABLED", "true")
os.environ.setdefault("PM_PAL_API_KEY", "")
os.environ.setdefault("PM_PAL_LLM", "openai:gpt-test")
os.environ.setdefault("SMART_LLM", "openai:gpt-test")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "")

from pm_pal.state import ParsedItemState, ReviewState, create_initial_state

_TMP_ROOT = Path(__file__).resolve().parents[1] / ".test-tmp"


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"pytest-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def sample_prd_text() -> str:
    return (
        "# Campus Recruitment PRD\n\n"
        "We need a recruiter-facing workflow that supports OAuth login, candidate shortlist review, "
        "and interview scheduling from a single dashboard. Recruiters should be able to search candidates, "
        "filter by campus and graduation year, and export a shortlist for hiring managers. The system must "
        "persist audit logs for login, shortlist changes, and interview status updates. Response time for the "
        "main dashboard should stay under two seconds for normal usage, and exported data must preserve the "
        "selected filters and ownership metadata."
    )


@pytest.fixture
def sample_parsed_items() -> list[ParsedItemState]:
    return [
        {
            "id": "REQ-001",
            "description": "Recruiters can log in with OAuth and retain session state.",
            "acceptance_criteria": [
                "OAuth callback succeeds",
                "Session persists after refresh",
            ],
        },
        {
            "id": "REQ-002",
            "description": "Recruiters can shortlist candidates by campus and graduation year.",
            "acceptance_criteria": [
                "Filters apply together",
                "Shortlist can be exported",
            ],
        },
        {
            "id": "REQ-003",
            "description": "The system records audit logs for sensitive workflow actions.",
            "acceptance_criteria": [
                "Login events are logged",
                "Interview status updates are logged",
            ],
        },
    ]


@pytest.fixture
def temp_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "20260326T000000Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


@pytest.fixture
def mock_llm_response() -> Callable[..., Any]:
    def _build(
        response: Any | None = None,
        *,
        side_effect: Any | None = None,
    ) -> Callable[..., Any]:
        async def _mock(*args: Any, **kwargs: Any) -> Any:
            if side_effect is not None:
                if isinstance(side_effect, Exception):
                    raise side_effect
                result = (
                    side_effect(*args, **kwargs)
                    if callable(side_effect)
                    else side_effect
                )
                if inspect.isawaitable(result):
                    return await result
                return result
            return deepcopy(response)

        return _mock

    return _build


@pytest.fixture
def sample_review_state(
    sample_prd_text: str, sample_parsed_items: list[ParsedItemState]
) -> ReviewState:
    state = create_initial_state(sample_prd_text)
    state["parsed_items"] = deepcopy(sample_parsed_items)
    state["review_results"] = [
        {
            "id": "REQ-001",
            "is_clear": True,
            "is_testable": True,
            "is_ambiguous": False,
            "issues": [],
            "suggestions": "",
        },
        {
            "id": "REQ-002",
            "is_clear": False,
            "is_testable": True,
            "is_ambiguous": True,
            "issues": ["Shortlist export owner is not identified."],
            "suggestions": "Assign an owner for shortlist export and define expected CSV fields.",
        },
        {
            "id": "REQ-003",
            "is_clear": True,
            "is_testable": True,
            "is_ambiguous": False,
            "issues": [],
            "suggestions": "",
        },
    ]
    state["final_report"] = (
        "# Requirement Review Report\n\nCore recruiter workflow reviewed."
    )
    state["tasks"] = [
        {
            "id": "TASK-001",
            "title": "Implement recruiter OAuth flow",
            "owner": "BE",
            "requirement_ids": ["REQ-001"],
            "depends_on": [],
            "estimate_days": 3.0,
        },
        {
            "id": "TASK-002",
            "title": "Add shortlist filters and export",
            "owner": "FE",
            "requirement_ids": ["REQ-002"],
            "depends_on": ["TASK-001"],
            "estimate_days": 2.0,
        },
    ]
    state["milestones"] = [
        {
            "id": "M-001",
            "title": "Recruiter MVP",
            "includes": ["TASK-001", "TASK-002"],
            "target_days": 5.0,
        }
    ]
    state["dependencies"] = [
        {"from": "TASK-002", "to": "TASK-001", "type": "blocked_by"}
    ]
    state["estimation"] = {"total_days": 5.0, "buffer_days": 1.0}
    state["implementation_plan"] = {
        "implementation_steps": [
            "Update auth flow",
            "Add shortlist filters",
            "Persist audit entries",
        ],
        "target_modules": ["backend.auth", "frontend.shortlist", "backend.audit"],
        "constraints": ["Preserve existing password login"],
    }
    state["test_plan"] = {
        "test_scope": ["OAuth callback", "Shortlist export", "Audit logging"],
        "edge_cases": ["Expired OAuth state", "Empty shortlist export"],
        "regression_focus": ["Existing recruiter login"],
    }
    state["codex_prompt_handoff"] = {
        "agent_prompt": "Implement the recruiter workflow changes with minimal auth regression risk.",
        "recommended_execution_order": [
            "Inspect auth flow",
            "Patch shortlist workflow",
            "Run focused tests",
        ],
        "non_goals": ["Do not redesign analytics dashboards"],
        "validation_checklist": ["Acceptance criteria mapped to tests"],
    }
    state["claude_code_prompt_handoff"] = {
        "agent_prompt": "Validate the recruiter workflow changes and regression coverage.",
        "recommended_execution_order": [
            "Inspect diff",
            "Run focused auth and shortlist tests",
        ],
        "non_goals": ["Do not broaden into unrelated UI refactors"],
        "validation_checklist": ["Audit logs verified"],
    }
    state["risks"] = [
        {
            "id": "RISK-001",
            "description": "OAuth changes may regress existing login.",
            "impact": "medium",
            "mitigation": "Run focused authentication regression coverage.",
            "buffer_days": 1.0,
            "evidence_ids": [],
            "evidence_snippets": [],
        }
    ]
    state["metrics"] = {"coverage_ratio": 0.9}
    state["high_risk_ratio"] = 1 / 3
    state["revision_round"] = 0
    state["trace"] = {}
    return state


@pytest.fixture
def sample_report_json(sample_review_state: ReviewState) -> dict[str, Any]:
    return {
        "final_report": sample_review_state["final_report"],
        "parsed_items": deepcopy(sample_review_state["parsed_items"]),
        "review_results": deepcopy(sample_review_state["review_results"]),
        "tasks": deepcopy(sample_review_state["tasks"]),
        "risks": deepcopy(sample_review_state["risks"]),
        "implementation_plan": deepcopy(sample_review_state["implementation_plan"]),
        "test_plan": deepcopy(sample_review_state["test_plan"]),
        "codex_prompt_handoff": deepcopy(sample_review_state["codex_prompt_handoff"]),
        "claude_code_prompt_handoff": deepcopy(
            sample_review_state["claude_code_prompt_handoff"]
        ),
        "metrics": deepcopy(sample_review_state["metrics"]),
        "high_risk_ratio": sample_review_state["high_risk_ratio"],
        "revision_round": sample_review_state["revision_round"],
        "trace": deepcopy(sample_review_state["trace"]),
    }


@pytest.fixture
def write_report_files(
    sample_report_json: dict[str, Any],
) -> Callable[..., dict[str, str]]:
    def _write(
        run_dir: Path, *, report_payload: dict[str, Any] | None = None
    ) -> dict[str, str]:
        payload = deepcopy(report_payload or sample_report_json)
        report_paths = {
            "report_md": str(run_dir / "report.md"),
            "report_json": str(run_dir / "report.json"),
            "run_trace": str(run_dir / "run_trace.json"),
        }
        (run_dir / "report.md").write_text(
            payload.get("final_report", "# Requirement Review Report\n"),
            encoding="utf-8",
        )
        (run_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "run_trace.json").write_text(
            json.dumps(payload.get("trace", {}), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report_paths

    return _write


@pytest.fixture
def write_delivery_workspace(
    sample_report_json: dict[str, Any],
    write_report_files: Callable[..., dict[str, str]],
) -> Callable[..., tuple[str, Path]]:
    def _write(
        outputs_root: Path,
        *,
        run_id: str = "20260308T020304Z",
        bundle_status: str = "approved",
        created_at: str = "2026-03-08T02:03:04+00:00",
        metadata: dict[str, Any] | None = None,
        report_payload: dict[str, Any] | None = None,
    ) -> tuple[str, Path]:
        run_dir = outputs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        implementation_pack = {
            "pack_type": "implementation_pack",
            "task_id": "TASK-001",
            "title": "Implement recruiter workflow",
            "summary": "Support recruiter login and shortlist handling safely.",
            "context": "Repository auth and shortlist flow context.",
            "target_modules": ["backend.auth"],
            "implementation_steps": [
                "Inspect auth flow",
                "Implement recruiter workflow change",
            ],
            "constraints": ["Do not break existing auth flow"],
            "acceptance_criteria": ["Recruiter login succeeds"],
            "recommended_skills": ["pytest"],
            "agent_handoff": {
                "primary_agent": "codex",
                "supporting_agents": ["claude_code"],
                "goals": ["Implement recruiter workflow"],
                "expected_output": "Small safe auth patch",
            },
        }
        test_pack = {
            "pack_type": "test_pack",
            "task_id": "TASK-001",
            "title": "Test recruiter workflow",
            "summary": "Validate recruiter login and shortlist flow.",
            "test_scope": ["Recruiter login API"],
            "edge_cases": ["Invalid credentials"],
            "acceptance_criteria": ["Regression covered"],
            "agent_handoff": {
                "primary_agent": "claude_code",
                "supporting_agents": ["codex"],
                "goals": ["Run auth checks"],
                "expected_output": "Validation summary",
            },
        }
        execution_pack = {
            "pack_type": "execution_pack",
            "pack_version": "1.0",
            "implementation_pack": implementation_pack,
            "test_pack": test_pack,
            "risk_pack": [
                {"id": "RISK-001", "summary": "Auth regression", "level": "low"}
            ],
            "handoff_strategy": "sequential",
        }

        for filename, payload in (
            ("implementation_pack.json", implementation_pack),
            ("test_pack.json", test_pack),
            ("execution_pack.json", execution_pack),
        ):
            (run_dir / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        report_paths = write_report_files(run_dir, report_payload=report_payload)
        bundle_id = f"bundle-{run_id}"
        bundle_payload = {
            "bundle_id": bundle_id,
            "bundle_version": "1.0",
            "created_at": created_at,
            "status": bundle_status,
            "source_run_id": run_id,
            "artifacts": {
                "prd_review_report": {
                    "artifact_type": "prd_review_report",
                    "path": str(run_dir / "prd_review_report.md"),
                },
                "open_questions": {
                    "artifact_type": "open_questions",
                    "path": str(run_dir / "open_questions.md"),
                },
                "scope_boundary": {
                    "artifact_type": "scope_boundary",
                    "path": str(run_dir / "scope_boundary.md"),
                },
                "tech_design_draft": {
                    "artifact_type": "tech_design_draft",
                    "path": str(run_dir / "tech_design_draft.md"),
                },
                "test_checklist": {
                    "artifact_type": "test_checklist",
                    "path": str(run_dir / "test_checklist.md"),
                },
                "implementation_pack": {
                    "artifact_type": "implementation_pack",
                    "path": str(run_dir / "implementation_pack.json"),
                },
                "test_pack": {
                    "artifact_type": "test_pack",
                    "path": str(run_dir / "test_pack.json"),
                },
                "execution_pack": {
                    "artifact_type": "execution_pack",
                    "path": str(run_dir / "execution_pack.json"),
                },
            },
            "approval_history": [],
            "metadata": metadata
            or {"source_report_paths": {"report_json": report_paths["report_json"]}},
        }
        (run_dir / "delivery_bundle.json").write_text(
            json.dumps(bundle_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return bundle_id, run_dir

    return _write
