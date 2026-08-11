from __future__ import annotations

import json

from pm_pal.service.review_service import (
    confirm_revision_action,
    get_review_result_payload,
)


def _prepare_run(tmp_path, run_id: str):
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "trace": {"reporter": {"status": "ok"}},
                "clarification": {"triggered": False, "status": "not_needed"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "revised_prd.md").write_text(
        "# Revised\n\nDraft body.\n", encoding="utf-8"
    )
    return run_dir


def test_confirm_revision_action_writes_confirmed_prd(tmp_path):
    run_id = "20260414T101010Z"
    run_dir = _prepare_run(tmp_path, run_id)

    stage = confirm_revision_action(
        run_id=run_id, action="confirm_revision", outputs_root=tmp_path
    )

    assert stage["revision_confirmed"] is True
    assert stage["preferred_prd_source"] == "confirmed_revision"
    assert (run_dir / "confirmed_prd.md").exists()

    payload = get_review_result_payload(run_id=run_id, outputs_root=tmp_path)
    assert payload["revision_stage"]["revision_confirmed"] is True
    assert payload["artifact_paths"]["confirmed_prd"].endswith("confirmed_prd.md")


def test_continue_without_revision_marks_unconfirmed_basis(tmp_path):
    run_id = "20260414T111111Z"
    _prepare_run(tmp_path, run_id)

    stage = confirm_revision_action(
        run_id=run_id, action="continue_without_revision", outputs_root=tmp_path
    )

    assert stage["revision_confirmed"] is False
    assert stage["preferred_prd_source"] == "unconfirmed_revision_draft"
