from __future__ import annotations

import json

import pytest

from pm_pal.run_review import run_review
from pm_pal.review.parallel_review_manager import ParallelReviewResult


def _trace_span(status: str = "ok") -> dict[str, object]:
    return {
        "start": "2026-03-08T00:00:00+00:00",
        "end": "2026-03-08T00:00:01+00:00",
        "duration_ms": 1000,
        "model": "none",
        "status": status,
        "input_chars": 10,
        "output_chars": 10,
        "raw_output_path": "",
        "error_message": "",
    }


@pytest.mark.asyncio
async def test_run_review_can_skip_when_prd_is_too_sparse(monkeypatch, tmp_path):
    async def fake_parser(state):
        trace = dict(state.get("trace", {}))
        trace["parser"] = _trace_span()
        return {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Login",
                    "acceptance_criteria": ["Works"],
                }
            ],
            "trace": trace,
        }

    async def fake_planner(state):
        trace = dict(state.get("trace", {}))
        trace["planner"] = _trace_span()
        return {
            "plan": {
                "tasks": [],
                "milestones": [],
                "dependencies": [],
                "estimation": {},
            },
            "trace": trace,
        }

    async def fake_risk(state):
        trace = dict(state.get("trace", {}))
        trace["risk"] = _trace_span()
        return {"evidence": {}, "risks": [], "trace": trace}

    async def fake_delivery(state):
        return {
            "implementation_plan": {
                "implementation_steps": [],
                "target_modules": [],
                "constraints": [],
            },
            "test_plan": {"test_scope": [], "edge_cases": [], "regression_focus": []},
            "codex_prompt_handoff": {
                "agent_prompt": "",
                "recommended_execution_order": [],
                "non_goals": [],
                "validation_checklist": [],
            },
            "claude_code_prompt_handoff": {
                "agent_prompt": "",
                "recommended_execution_order": [],
                "non_goals": [],
                "validation_checklist": [],
            },
        }

    async def fake_reviewer(state):
        trace = dict(state.get("trace", {}))
        trace["reviewer"] = {**_trace_span(), "input_chars": 40, "output_chars": 20}
        return {
            "review_results": [
                {
                    "id": "REQ-001",
                    "is_clear": True,
                    "is_testable": True,
                    "is_ambiguous": False,
                    "issues": [],
                    "suggestions": "",
                }
            ],
            "plan_review": {},
            "high_risk_ratio": 0.0,
            "trace": trace,
        }

    async def fake_reporter(state):
        trace = dict(state.get("trace", {}))
        trace["reporter"] = _trace_span()
        return {
            "final_report": "# Requirement Review Report\n\nSingle mode.",
            "metrics": {},
            "trace": trace,
        }

    monkeypatch.setattr("pm_pal.agents.parser_agent.run", fake_parser)
    monkeypatch.setattr("pm_pal.agents.planner_agent.run", fake_planner)
    monkeypatch.setattr(
        "pm_pal.workflow.run_risk_analysis_from_review_state", fake_risk
    )
    monkeypatch.setattr("pm_pal.agents.delivery_planning_agent.run", fake_delivery)
    monkeypatch.setattr("pm_pal.agents.reviewer_agent.run", fake_reviewer)
    monkeypatch.setattr("pm_pal.agents.reporter_agent.run", fake_reporter)

    run_output = await run_review("Simple login PRD", outputs_root=tmp_path)

    result = run_output["result"]
    assert result["review_mode"] == "skip"
    assert result["parallel-review_meta"]["selected_mode"] == "skip"
    assert result["parallel-review_meta"]["review_mode"] == "skip"
    assert result["parallel-review_meta"]["reviewers_completed"] == []
    assert result["parallel-review_meta"]["manual_review_required"] is True
    report_payload = json.loads(
        (tmp_path / run_output["run_id"] / "report.json").read_text(encoding="utf-8")
    )
    trace_payload = json.loads(
        (tmp_path / run_output["run_id"] / "run_trace.json").read_text(encoding="utf-8")
    )
    assert report_payload["parallel-review_meta"]["selected_mode"] == "skip"
    assert trace_payload["parallel-review_meta"]["selected_mode"] == "skip"


@pytest.mark.asyncio
async def test_run_review_forced_parallel_mode_uses_parallel_manager(
    monkeypatch, tmp_path
):
    async def fake_parser(state):
        trace = dict(state.get("trace", {}))
        trace["parser"] = _trace_span()
        return {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Export data",
                    "acceptance_criteria": ["Export works"],
                }
            ],
            "trace": trace,
        }

    async def fake_planner(state):
        trace = dict(state.get("trace", {}))
        trace["planner"] = _trace_span()
        return {
            "plan": {
                "tasks": [],
                "milestones": [],
                "dependencies": [],
                "estimation": {},
            },
            "trace": trace,
        }

    async def fake_risk(state):
        trace = dict(state.get("trace", {}))
        trace["risk"] = _trace_span()
        return {"evidence": {}, "risks": [], "trace": trace}

    async def fake_delivery(state):
        return {
            "implementation_plan": {
                "implementation_steps": [],
                "target_modules": [],
                "constraints": [],
            },
            "test_plan": {"test_scope": [], "edge_cases": [], "regression_focus": []},
            "codex_prompt_handoff": {
                "agent_prompt": "",
                "recommended_execution_order": [],
                "non_goals": [],
                "validation_checklist": [],
            },
            "claude_code_prompt_handoff": {
                "agent_prompt": "",
                "recommended_execution_order": [],
                "non_goals": [],
                "validation_checklist": [],
            },
        }

    async def fake_parallel_review(
        _prd_text, output_dir, reviewer_config=None, gating_decision=None
    ):
        return ParallelReviewResult(
            normalized_requirement={"summary": "Parallel export review"},
            reviewer_inputs={
                "product": "p",
                "engineering": "e",
                "qa": "q",
                "security": "s",
            },
            reviewer_results=(
                {
                    "reviewer": "product",
                    "findings": [],
                    "open_questions": [],
                    "risk_items": [],
                    "summary": "product",
                    "status": "completed",
                    "error_message": "",
                },
                {
                    "reviewer": "engineering",
                    "findings": [],
                    "open_questions": [],
                    "risk_items": [],
                    "summary": "engineering",
                    "status": "completed",
                    "error_message": "",
                },
                {
                    "reviewer": "qa",
                    "findings": [],
                    "open_questions": [],
                    "risk_items": [],
                    "summary": "qa",
                    "status": "completed",
                    "error_message": "",
                },
                {
                    "reviewer": "security",
                    "findings": [],
                    "open_questions": [],
                    "risk_items": [],
                    "summary": "security",
                    "status": "completed",
                    "error_message": "",
                },
            ),
            aggregated={
                "findings": [
                    {
                        "finding_id": "finding-123456789abc",
                        "title": "Security review gate required",
                        "detail": "Sensitive export",
                        "description": "Sensitive export",
                        "severity": "high",
                        "category": "security",
                        "source_reviewer": "security",
                        "suggested_action": "Approve release",
                        "assignee": "security",
                        "reviewers": ["security"],
                    }
                ],
                "risk_items": [
                    {
                        "title": "Security review gate required",
                        "detail": "Sensitive export",
                        "severity": "high",
                        "category": "security",
                        "mitigation": "Approve release",
                        "reviewers": ["security"],
                    }
                ],
                "open_questions": [
                    {"question": "Who approves the export?", "reviewers": ["product"]}
                ],
                "conflicts": [],
                "reviewer_summaries": [{"reviewer": "product", "summary": "product"}],
                "reviewer_count": 4,
                "meta": {
                    "review_mode": "full",
                    "gating": {
                        "selected_mode": "full",
                        "reasons": ["mode=full explicitly requested"],
                        "skipped": False,
                    },
                    "reviewers_used": ["product", "engineering", "qa", "security"],
                    "reviewers_skipped": [],
                    "reviewers_completed": ["product", "engineering", "qa", "security"],
                    "reviewers_failed": [],
                },
                "review_mode": "full",
                "reviewers_used": ["product", "engineering", "qa", "security"],
                "reviewers_skipped": [],
                "gating": {
                    "selected_mode": "full",
                    "reasons": ["mode=full explicitly requested"],
                    "skipped": False,
                },
                "summary": {
                    "overall_risk": "high",
                    "in_scope": ["Export data"],
                    "out_of_scope": [],
                },
                "artifacts": {
                    "review_result_json": str(output_dir) + "\\review_result.json",
                    "review_report_md": str(output_dir) + "\\review_report.md",
                    "review_report_json": str(output_dir) + "\\review_report.json",
                    "risk_items_json": str(output_dir) + "\\risk_items.json",
                    "open_questions_json": str(output_dir) + "\\open_questions.json",
                    "review_summary_md": str(output_dir) + "\\review_summary.md",
                },
            },
        )

    async def fail_reviewer(_state):
        raise AssertionError(
            "single reviewer should not run when parallel mode is forced"
        )

    async def fake_reporter(state):
        trace = dict(state.get("trace", {}))
        trace["reporter"] = _trace_span()
        return {
            "final_report": "# Requirement Review Report\n\nParallel mode.",
            "metrics": {},
            "trace": trace,
        }

    monkeypatch.setattr("pm_pal.agents.parser_agent.run", fake_parser)
    monkeypatch.setattr("pm_pal.agents.planner_agent.run", fake_planner)
    monkeypatch.setattr(
        "pm_pal.workflow.run_risk_analysis_from_review_state", fake_risk
    )
    monkeypatch.setattr("pm_pal.agents.delivery_planning_agent.run", fake_delivery)
    monkeypatch.setattr("pm_pal.agents.reviewer_agent.run", fail_reviewer)
    monkeypatch.setattr(
        "pm_pal.workflow.run_parallel_review_async", fake_parallel_review
    )
    monkeypatch.setattr("pm_pal.agents.reporter_agent.run", fake_reporter)

    run_output = await run_review(
        "Complex export PRD",
        outputs_root=tmp_path,
        review_mode_override="parallel_review",
    )

    result = run_output["result"]
    assert result["review_mode"] == "full"
    assert result["parallel-review_meta"]["selected_mode"] == "full"
    assert result["parallel-review_meta"]["review_mode"] == "full"
    assert result["parallel-review_meta"]["reviewer_count"] == 4
    assert result["parallel-review_meta"]["reviewers_completed"] == [
        "product",
        "engineering",
        "qa",
        "security",
    ]
    assert result["parallel-review_meta"]["reviewers_failed"] == []
    assert result["parallel-review_meta"]["artifact_paths"][
        "review_result_json"
    ].endswith("review_result.json")
    assert len(result["review_open_questions"]) == 1
    assert len(result["review_risk_items"]) == 1
    report_payload = json.loads(
        (tmp_path / run_output["run_id"] / "report.json").read_text(encoding="utf-8")
    )
    trace_payload = json.loads(
        (tmp_path / run_output["run_id"] / "run_trace.json").read_text(encoding="utf-8")
    )
    assert report_payload["parallel-review_meta"]["selected_mode"] == "full"
    assert trace_payload["parallel-review_meta"]["selected_mode"] == "full"


@pytest.mark.asyncio
async def test_run_review_carries_review_profile_into_parallel_meta(
    monkeypatch, tmp_path
):
    async def fake_parser(state):
        trace = dict(state.get("trace", {}))
        trace["parser"] = _trace_span()
        return {
            "parsed_items": [
                {
                    "id": "REQ-001",
                    "description": "Export data",
                    "acceptance_criteria": ["Export works"],
                }
            ],
            "trace": trace,
        }

    async def fake_planner(state):
        trace = dict(state.get("trace", {}))
        trace["planner"] = _trace_span()
        return {
            "plan": {
                "tasks": [],
                "milestones": [],
                "dependencies": [],
                "estimation": {},
            },
            "trace": trace,
        }

    async def fake_risk(state):
        trace = dict(state.get("trace", {}))
        trace["risk"] = _trace_span()
        return {"evidence": {}, "risks": [], "trace": trace}

    async def fake_delivery(_state):
        return {
            "implementation_plan": {
                "implementation_steps": [],
                "target_modules": [],
                "constraints": [],
            },
            "test_plan": {"test_scope": [], "edge_cases": [], "regression_focus": []},
            "codex_prompt_handoff": {
                "agent_prompt": "",
                "recommended_execution_order": [],
                "non_goals": [],
                "validation_checklist": [],
            },
            "claude_code_prompt_handoff": {
                "agent_prompt": "",
                "recommended_execution_order": [],
                "non_goals": [],
                "validation_checklist": [],
            },
        }

    async def fake_parallel_review(
        _prd_text, output_dir, reviewer_config=None, gating_decision=None
    ):
        return ParallelReviewResult(
            normalized_requirement={"summary": "Parallel export review"},
            reviewer_inputs={"product": "p"},
            reviewer_results=(
                {
                    "reviewer": "product",
                    "findings": [],
                    "open_questions": [],
                    "risk_items": [],
                    "summary": "product",
                    "status": "completed",
                    "error_message": "",
                },
            ),
            aggregated={
                "findings": [],
                "risk_items": [],
                "open_questions": [],
                "conflicts": [],
                "reviewer_summaries": [{"reviewer": "product", "summary": "product"}],
                "reviewer_count": 1,
                "meta": {
                    "review_mode": "full",
                    "reviewers_used": ["product"],
                    "reviewers_skipped": [],
                    "reviewers_completed": ["product"],
                    "reviewers_failed": [],
                },
                "review_mode": "full",
                "reviewers_used": ["product"],
                "reviewers_skipped": [],
                "gating": {
                    "selected_mode": "full",
                    "reasons": ["mode=full explicitly requested"],
                    "skipped": False,
                },
                "summary": {
                    "overall_risk": "low",
                    "in_scope": ["Export data"],
                    "out_of_scope": [],
                },
                "artifacts": {
                    "review_result_json": str(output_dir) + "\\review_result.json"
                },
            },
        )

    async def fake_reporter(state):
        trace = dict(state.get("trace", {}))
        trace["reporter"] = _trace_span()
        return {
            "final_report": "# Requirement Review Report\n\nProfile meta.",
            "metrics": {},
            "trace": trace,
        }

    monkeypatch.setattr("pm_pal.agents.parser_agent.run", fake_parser)
    monkeypatch.setattr("pm_pal.agents.planner_agent.run", fake_planner)
    monkeypatch.setattr(
        "pm_pal.workflow.run_risk_analysis_from_review_state", fake_risk
    )
    monkeypatch.setattr("pm_pal.agents.delivery_planning_agent.run", fake_delivery)
    monkeypatch.setattr(
        "pm_pal.workflow.run_parallel_review_async", fake_parallel_review
    )
    monkeypatch.setattr("pm_pal.agents.reporter_agent.run", fake_reporter)

    run_output = await run_review(
        "Complex export PRD",
        outputs_root=tmp_path,
        review_mode_override="parallel_review",
        review_profile={
            "selected_profile": "approval_workflow",
            "confidence": 0.8,
            "reason": "approval signals detected",
            "secondary_profiles": ["data_sensitive"],
        },
        review_profile_pack={
            "profile": "approval_workflow",
            "pack_path": str(tmp_path / "profile_pack"),
            "checklist_path": str(tmp_path / "profile_pack" / "checklist.md"),
            "rules_path": str(tmp_path / "profile_pack" / "rules.md"),
        },
    )

    meta = run_output["result"]["parallel-review_meta"]
    assert meta["selected_profile"] == "approval_workflow"
    assert meta["review_profile"]["confidence"] == 0.8
    assert meta["review_profile_pack"]["profile"] == "approval_workflow"
