from __future__ import annotations

import json

from pm_pal.service.roadmap_service import generate_roadmap_for_run


def _prepare_run(tmp_path, run_id: str, *, confirmed: bool) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "run_id": run_id,
        "trace": {"reporter": {"status": "ok"}},
        "requirement_doc": "# Original PRD\n\nbase",
        "tasks": [
            {"id": "T-1", "title": "Core setup", "depends_on": []},
            {"id": "T-2", "title": "Integrations", "depends_on": ["T-1"]},
            {"id": "T-3", "title": "Rollout", "depends_on": ["T-2"]},
        ],
        "dependencies": [{"from": "T-1", "to": "T-2"}, {"from": "T-2", "to": "T-3"}],
        "risk_items": [{"title": "coordination", "severity": "medium"}],
        "artifacts": {},
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if confirmed:
        (run_dir / "confirmed_prd.md").write_text(
            "# Confirmed PRD\n\nconfirmed", encoding="utf-8"
        )


def test_generate_roadmap_for_run_prefers_confirmed_revision(tmp_path):
    run_id = "20260414T131500Z"
    _prepare_run(tmp_path, run_id, confirmed=True)

    payload = generate_roadmap_for_run(run_id=run_id, outputs_root=tmp_path)

    assert payload["roadmap_generation"]["status"] == "generated"
    assert (
        payload["roadmap_generation"]["roadmap_source"]["selected_source"]
        == "confirmed_revision"
    )
    assert payload["artifact_paths"]["roadmap_md"].endswith("roadmap.md")
    assert payload["artifact_paths"]["roadmap_json"].endswith("roadmap.json")


def test_generate_roadmap_for_run_returns_not_recommended_for_small_scope(tmp_path):
    run_id = "20260414T131501Z"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "trace": {"reporter": {"status": "ok"}},
                "requirement_doc": "# Original",
                "tasks": [{"id": "T-1", "title": "single task"}],
                "artifacts": {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = generate_roadmap_for_run(run_id=run_id, outputs_root=tmp_path)
    assert payload["roadmap_generation"]["status"] == "not_recommended"
    assert "roadmap_not_recommended" in payload["roadmap_generation"]["reason"]
