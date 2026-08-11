from __future__ import annotations

import json

from pm_pal.draft_generator import (
    GENERATOR_VERSION,
    build_draft_generator_input,
    generate_prd_v1_artifact,
    render_prd_v1_markdown,
)


def _base_run_output(
    tmp_path,
    *,
    requirement_doc: str = "# Recruiting PRD\n\nNeed shortlist review flow.",
) -> dict:
    run_dir = tmp_path / "20260412T010203Z"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "run_id": "20260412T010203Z",
        "requirement_doc": requirement_doc,
        "parsed_items": [
            {
                "id": "REQ-001",
                "description": "Support recruiter shortlist review flow",
                "acceptance_criteria": [
                    "Recruiters can submit shortlist decisions",
                    "Decision history is retained",
                ],
            }
        ],
        "review_results": [],
        "test_plan": {"edge_cases": ["Candidate already archived"]},
        "implementation_plan": {"target_modules": ["service.review_service"]},
        "trace": {},
    }
    (run_dir / "report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "run_id": "20260412T010203Z",
        "run_dir": str(run_dir),
        "report_paths": {"report_json": str(run_dir / "report.json")},
        "result": dict(report_payload),
    }


def test_render_prd_v1_markdown_handles_empty_findings(tmp_path):
    run_output = _base_run_output(tmp_path)

    draft_input = build_draft_generator_input(run_output)
    markdown = render_prd_v1_markdown(draft_input)

    assert "run_id: 20260412T010203Z" in markdown
    assert f"generator_version: {GENERATOR_VERSION}" in markdown
    assert "## Original Requirement Doc" in markdown
    assert "# Recruiting PRD" in markdown
    assert "## Acceptance Criteria" in markdown
    assert "REQ-001: Recruiters can submit shortlist decisions" in markdown
    assert "## Open Questions" in markdown
    assert "No unresolved review questions remain" in markdown


def test_render_prd_v1_markdown_expands_open_questions(tmp_path):
    run_output = _base_run_output(tmp_path)
    run_dir = tmp_path / "20260412T010203Z"
    (run_dir / "open_questions.json").write_text(
        json.dumps(
            {
                "open_questions": [
                    {
                        "question": f"Clarify scope boundary {index}",
                        "reviewers": ["product"],
                        "issues": ["scope unclear"],
                    }
                    for index in range(1, 8)
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    artifact = generate_prd_v1_artifact(run_output)
    markdown = (run_dir / "prd_v1.md").read_text(encoding="utf-8")

    assert artifact.artifact_key == "prd_v1"
    assert "source_artifacts: report.json, open_questions.json" in markdown
    assert (
        "Clarify scope boundary 1 [owners: product] [context: scope unclear]"
        in markdown
    )
    assert "Clarify unresolved boundaries that affect scope definition" in markdown


def test_render_prd_v1_markdown_expands_risk_items(tmp_path):
    run_output = _base_run_output(tmp_path)
    run_dir = tmp_path / "20260412T010203Z"
    (run_dir / "risk_items.json").write_text(
        json.dumps(
            {
                "risk_items": [
                    {
                        "title": f"Risk {index}",
                        "detail": f"Potential failure path {index}",
                        "severity": "high",
                        "mitigation": f"Mitigate {index}",
                    }
                    for index in range(1, 7)
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run_output["result"]["review_results"] = [
        {
            "id": "REQ-001",
            "description": "Support recruiter shortlist review flow",
            "is_clear": False,
            "is_testable": True,
            "is_ambiguous": True,
            "issues": ["Decision SLA is not measurable"],
            "suggestions": "Add a measurable reviewer SLA.",
        }
    ]

    artifact = generate_prd_v1_artifact(run_output)
    markdown = (run_dir / "prd_v1.md").read_text(encoding="utf-8")

    assert artifact.path.endswith("prd_v1.md")
    assert "[high] Risk 1: Potential failure path 1 Mitigation: Mitigate 1" in markdown
    assert (
        "Convert this into a measurable acceptance criterion: Add a measurable reviewer SLA."
        in markdown
    )
    assert "Validate high-severity edge case: Potential failure path 1" in markdown
