import json
from pathlib import Path

from pm_pal.handoff import (
    render_claude_code_prompt,
    render_codex_prompt,
    render_openclaw_prompt,
)
from pm_pal.packs import ExecutionPackBuilder
from pm_pal.service.review_service import build_handoff_prompts

SAMPLE_REQUIREMENTS = [
    {
        "id": "REQ-101",
        "description": "Add recruiter SSO login support",
        "acceptance_criteria": [
            "SSO callback succeeds",
            "Existing login remains available",
        ],
    }
]

SAMPLE_TASKS = [
    {
        "id": "TASK-101",
        "title": "Implement recruiter SSO login",
        "owner": "BE",
        "requirement_ids": ["REQ-101"],
    }
]

SAMPLE_RISKS = [
    {
        "id": "RISK-101",
        "description": "Legacy auth session may regress",
        "impact": "high",
        "mitigation": "Run targeted auth regression coverage",
        "owner": "qa",
    }
]

IMPLEMENTATION_PLAN = {
    "implementation_steps": [
        "Inspect auth flow",
        "Add SSO callback handler",
        "Preserve legacy login flow",
    ],
    "target_modules": ["pm_pal/server/app.py", "frontend/src/auth.ts"],
    "constraints": ["Do not break existing password login"],
}

TEST_PLAN = {
    "test_scope": ["SSO callback API", "Recruiter login UI"],
    "edge_cases": ["Expired state token"],
    "regression_focus": ["Password login regression"],
}

CODEX_PROMPT = {
    "agent_prompt": "Implement the smallest safe SSO change set and run focused checks.",
    "recommended_execution_order": [
        "Review auth modules",
        "Implement backend changes",
        "Verify SSO login",
    ],
    "non_goals": ["Do not redesign user profile flows"],
    "validation_checklist": ["SSO flow mapped to tests"],
}

CLAUDE_PROMPT = {
    "agent_prompt": "Validate edge cases and regression coverage before handoff is complete.",
    "recommended_execution_order": [
        "Inspect changed files",
        "Add edge-case coverage",
        "Run auth regression checks",
    ],
    "non_goals": ["Do not expand beyond auth validation"],
    "validation_checklist": ["Regression checks stay green"],
}


def _build_execution_pack(*, implementation_plan=None, risks=None):
    return ExecutionPackBuilder().build(
        requirements=SAMPLE_REQUIREMENTS,
        tasks=SAMPLE_TASKS,
        risks=SAMPLE_RISKS if risks is None else risks,
        implementation_plan_output=IMPLEMENTATION_PLAN
        if implementation_plan is None
        else implementation_plan,
        test_plan_output=TEST_PLAN,
        codex_prompt_output=CODEX_PROMPT,
        claude_code_prompt_output=CLAUDE_PROMPT,
    )


def test_render_codex_prompt_contains_required_sections() -> None:
    prompt = render_codex_prompt(_build_execution_pack())

    assert prompt.startswith("# Codex Handoff Prompt")
    for section in (
        "## Goal",
        "## Required Changes",
        "## Constraints",
        "## Acceptance Criteria",
        "## Testing Requirements",
    ):
        assert section in prompt
    assert "Add SSO callback handler" in prompt
    assert "`pm_pal/server/app.py`" in prompt
    assert "Legacy auth session may regress" in prompt


def test_render_claude_code_prompt_contains_validation_handoff_instructions() -> None:
    prompt = render_claude_code_prompt(
        _build_execution_pack().model_dump(mode="python")
    )

    assert prompt.startswith("# Claude Code Handoff Prompt")
    assert "repository analysis instruction" in prompt
    assert "implementation summary" in prompt
    assert "changed files" in prompt
    assert "test results" in prompt
    assert "Expired state token" in prompt
    assert "Regression checks stay green" in prompt


def test_render_openclaw_prompt_contains_local_agent_guidance() -> None:
    prompt = render_openclaw_prompt(_build_execution_pack())

    assert prompt.startswith("# OpenClaw Handoff Prompt")
    assert "verification scope" in prompt
    assert "Add SSO callback handler" in prompt
    assert "Expired state token" in prompt


def test_renderers_tolerate_missing_target_modules_and_risk_summary() -> None:
    pack = _build_execution_pack(
        implementation_plan={
            "implementation_steps": ["Inspect auth flow"],
            "constraints": ["Do not break existing password login"],
        },
        risks=[
            {
                "id": "RISK-404",
                "impact": "medium",
                "mitigation": "Run smoke tests",
            }
        ],
    )

    codex_prompt = render_codex_prompt(pack)
    claude_prompt = render_claude_code_prompt(pack)

    assert "Target modules:" in codex_prompt
    assert "Target modules were not provided." in codex_prompt
    assert "Summary not provided." in codex_prompt
    assert "Target modules were not provided." in claude_prompt
    assert "Summary not provided." in claude_prompt


def test_build_handoff_prompts_writes_expected_markdown_files(tmp_path: Path) -> None:
    execution_pack_path = tmp_path / "execution_pack.json"
    execution_pack_path.write_text(
        json.dumps(
            _build_execution_pack().model_dump(mode="python"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    trace: dict[str, object] = {}
    prompt_paths = build_handoff_prompts(execution_pack_path, trace=trace)

    assert prompt_paths == {
        "codex_prompt": str(tmp_path / "codex_prompt.md"),
        "claude_code_prompt": str(tmp_path / "claude_code_prompt.md"),
        "openclaw_prompt": str(tmp_path / "openclaw_prompt.md"),
    }
    for path_str in prompt_paths.values():
        path = Path(path_str)
        assert path.exists()
        assert path.suffix == ".md"
        assert path.read_text(encoding="utf-8").startswith("# ")

    renderer_trace = trace["handoff_renderer"]
    assert renderer_trace["template_version"] == "handoff_markdown_v1"
    assert (
        renderer_trace["templates"]["codex_prompt"]["template_id"]
        == "adapter.codex.handoff_markdown"
    )
    assert (
        renderer_trace["templates"]["claude_code_prompt"]["template_id"]
        == "adapter.claude_code.handoff_markdown"
    )
    assert (
        renderer_trace["templates"]["openclaw_prompt"]["template_id"]
        == "adapter.openclaw.handoff_markdown"
    )
