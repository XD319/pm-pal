from __future__ import annotations

from pathlib import Path


def test_local_prd_review_skill_hardens_storage_and_source_usage() -> None:
    skill_path = Path("skills/prd-review-agent/SKILL.md")
    yaml_path = Path("skills/prd-review-agent/agents/openai.yaml")

    skill = skill_path.read_text(encoding="utf-8")
    ui_yaml = yaml_path.read_text(encoding="utf-8")

    assert "[TODO" not in skill
    assert "outputs/_skill/current_prd.md" in skill
    assert "Do not use connector-backed `--source`" in skill
    assert "Do not paste the full PRD or the full report JSON" in skill
    assert "clarification.triggered" in skill
    assert "answer_review_clarification" in skill
    assert "POST /api/projects/<project_id>/reviews/<run_id>/clarification" in skill
    assert (
        "Do not continue to `prepare-handoff` while clarification is still pending."
        in skill
    )
    assert "$prd-review-agent" in ui_yaml


def test_remote_prd_review_service_skill_targets_http_flow() -> None:
    skill_path = Path("skills/prd-review-service/SKILL.md")
    yaml_path = Path("skills/prd-review-service/agents/openai.yaml")

    skill = skill_path.read_text(encoding="utf-8")
    ui_yaml = yaml_path.read_text(encoding="utf-8")

    assert "[TODO" not in skill
    assert "GET <base-url>/health" in skill
    assert "POST <base-url>/api/projects/<project_id>/reviews" in skill
    assert "GET <base-url>/api/projects/<project_id>/reviews/<run_id>/result" in skill
    assert "Never reveal API keys" in skill
    assert "source_id" in skill
    assert "does **not** accept inline `prd_text`" in skill
    assert "$prd-review-service" in ui_yaml
