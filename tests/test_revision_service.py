from __future__ import annotations

import json

import pytest

from pm_pal.service import revision_service


def _prepare_revision_run_dir(tmp_path, run_id: str):
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    report_payload = {
        "run_id": run_id,
        "requirement_doc": "# Original PRD\n\nKeep original body.",
        "summary": {"headline": "review summary"},
        "findings": [{"id": "F-1", "suggestion": "add SLO"}],
        "risk_items": [{"id": "R-1", "description": "missing rollback"}],
        "gating": {"selected_mode": "full"},
        "reviewers_used": ["product", "engineering"],
        "trace": {"reporter": {"status": "ok"}},
        "clarification": {
            "triggered": True,
            "status": "resolved",
            "stable_conclusions": ["Use phased rollout"],
            "resolved_answers": [{"question_id": "q1", "answer": "phase by tenant"}],
        },
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "revision_stage.json").write_text(
        json.dumps(
            {
                "status": "inputs_recorded",
                "decision": "generate_from_review",
                "entered_revision": True,
                "available": True,
                "decision_required": False,
                "allow_continue_without_revision": True,
                "updated_at": "2026-04-14T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "revision_request.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "selected_review_basis": "all_review_suggestions",
                "extra_instructions": "Must keep beta timeline",
                "meeting_notes_text": "Leader asked to shift launch by one month.",
                "meeting_notes_file_ref": {"name": "meeting.md"},
                "source_context_snapshot": {"review_status": "completed"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


@pytest.mark.asyncio
async def test_generate_revision_for_run_async_writes_revision_artifacts(
    tmp_path, monkeypatch
):
    run_id = "20260414T010203Z"
    run_dir = _prepare_revision_run_dir(tmp_path, run_id)

    async def fake_llm_structured_call(*, prompt, schema, metadata):
        return {
            "revised_prd_markdown": "# Revised PRD\n\nUpdated scope and rollout.",
            "sources_used": [
                "review",
                "clarification",
                "meeting_notes",
                "user_instructions",
            ],
            "major_changes": ["Added rollout strategy section."],
            "rationale": "Align with stable review feedback while preserving unresolved conflicts.",
            "unadopted_review_suggestions": [
                "Do full rewrite of auth module (too broad)."
            ],
            "pending_questions": [
                "Meeting note conflicts with PRD launch date; need confirmation."
            ],
            "user_direct_requirements_applied": [
                "Kept beta timeline constraint in revised milestones."
            ],
        }

    monkeypatch.setattr(
        revision_service, "llm_structured_call", fake_llm_structured_call
    )

    payload = await revision_service.generate_revision_for_run_async(
        run_id=run_id,
        outputs_root=tmp_path,
    )

    assert payload["revision_status"] == "completed"
    assert (run_dir / "revised_prd.md").exists()
    assert (run_dir / "revision_summary.md").exists()
    summary_json = json.loads(
        (run_dir / "revision_summary.json").read_text(encoding="utf-8")
    )
    assert summary_json["status"] == "completed"
    assert "meeting_notes" in summary_json["sources_used"]
    assert summary_json["user_direct_requirements_applied"]


@pytest.mark.asyncio
async def test_generate_revision_for_run_async_returns_failed_status_with_reason(
    tmp_path, monkeypatch
):
    run_id = "20260414T010204Z"
    run_dir = _prepare_revision_run_dir(tmp_path, run_id)

    async def fake_llm_structured_call(*, prompt, schema, metadata):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(
        revision_service, "llm_structured_call", fake_llm_structured_call
    )

    payload = await revision_service.generate_revision_for_run_async(
        run_id=run_id,
        outputs_root=tmp_path,
    )

    assert payload["revision_status"] == "failed"
    assert "llm unavailable" in payload["error_reason"]
    stage_payload = json.loads(
        (run_dir / "revision_stage.json").read_text(encoding="utf-8")
    )
    assert stage_payload["status"] == "failed"
    assert "llm unavailable" in stage_payload["error_reason"]
