from __future__ import annotations

from pathlib import Path

from pm_pal.packs.artifact_splitter import ArtifactSplitter


def test_artifact_splitter_generates_expected_markdown_files(
    tmp_path: Path, sample_report_json: dict
):
    refs = ArtifactSplitter().split(sample_report_json, tmp_path)

    assert set(refs) == {
        "prd_review_report",
        "open_questions",
        "scope_boundary",
        "tech_design_draft",
        "test_checklist",
    }
    for ref in refs.values():
        path = Path(ref.path)
        assert path.exists()
        assert path.parent == tmp_path
        assert ref.content_hash

    assert "# PRD Review Report" in (tmp_path / "prd_review_report.md").read_text(
        encoding="utf-8"
    )
    assert "# Open Questions" in (tmp_path / "open_questions.md").read_text(
        encoding="utf-8"
    )
    assert "Shortlist export owner is not identified." in (
        tmp_path / "open_questions.md"
    ).read_text(encoding="utf-8")
    assert "# Scope Boundary" in (tmp_path / "scope_boundary.md").read_text(
        encoding="utf-8"
    )
    assert "# Technical Design Draft" in (tmp_path / "tech_design_draft.md").read_text(
        encoding="utf-8"
    )
    assert "# Test Checklist" in (tmp_path / "test_checklist.md").read_text(
        encoding="utf-8"
    )


def test_artifact_splitter_degrades_gracefully_on_empty_input(tmp_path: Path):
    refs = ArtifactSplitter().split({}, tmp_path)

    assert len(refs) == 5
    assert "No open questions were detected." in (
        tmp_path / "open_questions.md"
    ).read_text(encoding="utf-8")
    assert "No implementation steps were generated." in (
        tmp_path / "tech_design_draft.md"
    ).read_text(encoding="utf-8")


def test_artifact_splitter_returns_correct_paths(
    tmp_path: Path, sample_report_json: dict
):
    refs = ArtifactSplitter().split(sample_report_json, tmp_path)

    assert refs["prd_review_report"].path == str(tmp_path / "prd_review_report.md")
    assert refs["test_checklist"].path == str(tmp_path / "test_checklist.md")
