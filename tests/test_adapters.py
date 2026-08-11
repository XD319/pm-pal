from __future__ import annotations

import json
from pathlib import Path

from pm_pal.adapters import (
    BaseAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    OpenClawAdapter,
)
from pm_pal.execution import ExecutionMode, ExecutionTask, ExecutionTaskStatus
from pm_pal.packs import build_execution_pack
from pm_pal.packs.delivery_bundle import (
    ArtifactRef,
    BundleStatus,
    DeliveryArtifacts,
    DeliveryBundle,
)
from pm_pal.packs.schemas import ExecutionPack


class DummyAdapter(BaseAdapter):
    adapter_name = "dummy"
    display_name = "Dummy"
    request_filename = "dummy_request.json"
    request_type = "dummy.run_pack"

    def _render_prompt(self, execution_pack: ExecutionPack) -> str:
        return f"# Dummy Prompt\n\nTask: {execution_pack.implementation_pack.task_id}\n"

    def _build_adapter_payload(self, **kwargs) -> dict[str, object]:
        return {
            "input": {
                "prompt": kwargs["prompt"],
                "context_file": str(kwargs["context_path"]),
            }
        }


def _write_text(path: Path, content: str = "") -> None:
    path.write_text(content, encoding="utf-8")


def _make_execution_pack() -> ExecutionPack:
    return build_execution_pack(
        implementation_pack={
            "pack_type": "implementation_pack",
            "task_id": "TASK-ADAPTER-001",
            "title": "Add adapter abstraction",
            "summary": "Generate adapter request payloads from the delivery bundle.",
            "context": "Repository context for downstream coding agents.",
            "target_modules": [
                "pm_pal/adapters/base.py",
                "pm_pal/service/execution_service.py",
            ],
            "implementation_steps": [
                "Read execution pack",
                "Build adapter request payload",
                "Persist execution context",
            ],
            "constraints": ["Do not execute external commands"],
            "acceptance_criteria": [
                "Request payload is persisted",
                "Execution context includes prompt",
            ],
            "recommended_skills": ["pytest"],
            "agent_handoff": {
                "primary_agent": "codex",
                "supporting_agents": ["claude_code"],
                "goals": ["Generate adapter-specific requests"],
                "expected_output": "Saved request payload and execution context files",
                "notes": ["Keep payloads stable for later callback integration"],
            },
        },
        test_pack={
            "pack_type": "test_pack",
            "task_id": "TASK-ADAPTER-001",
            "title": "Validate adapter abstraction",
            "summary": "Ensure prompt and payload generation are deterministic.",
            "test_scope": ["codex request builder", "claude_code request builder"],
            "edge_cases": ["Missing execution pack file", "Executor type mismatch"],
            "acceptance_criteria": [
                "Pytest coverage exists",
                "Prompt rendering includes validation details",
            ],
            "agent_handoff": {
                "primary_agent": "claude_code",
                "supporting_agents": ["codex"],
                "goals": ["Validate request payload shape"],
                "expected_output": "Passing adapter tests",
            },
        },
        risk_pack=[
            {
                "id": "RISK-ADAPTER-001",
                "summary": "Adapter payload may drift from bundle artifacts",
                "level": "medium",
                "mitigation": "Cover request payload fields with tests",
            }
        ],
    )


def _make_bundle(tmp_path: Path) -> DeliveryBundle:
    execution_pack = _make_execution_pack()

    implementation_pack_path = tmp_path / "implementation_pack.json"
    test_pack_path = tmp_path / "test_pack.json"
    execution_pack_path = tmp_path / "execution_pack.json"
    prd_review_report = tmp_path / "prd_review_report.md"
    open_questions = tmp_path / "open_questions.md"
    scope_boundary = tmp_path / "scope_boundary.md"
    tech_design_draft = tmp_path / "tech_design_draft.md"
    test_checklist = tmp_path / "test_checklist.md"

    implementation_pack_path.write_text(
        json.dumps(
            execution_pack.implementation_pack.model_dump(mode="python"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    test_pack_path.write_text(
        json.dumps(
            execution_pack.test_pack.model_dump(mode="python"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    execution_pack_path.write_text(
        json.dumps(
            execution_pack.model_dump(mode="python"), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    for path in (
        prd_review_report,
        open_questions,
        scope_boundary,
        tech_design_draft,
        test_checklist,
    ):
        _write_text(path, f"{path.stem}\n")

    return DeliveryBundle(
        bundle_id="bundle-adapter-001",
        created_at="2026-03-08T08:00:00+00:00",
        status=BundleStatus.approved,
        source_run_id="20260308T080000Z",
        artifacts=DeliveryArtifacts(
            prd_review_report=ArtifactRef(
                artifact_type="prd_review_report", path=str(prd_review_report)
            ),
            open_questions=ArtifactRef(
                artifact_type="open_questions", path=str(open_questions)
            ),
            scope_boundary=ArtifactRef(
                artifact_type="scope_boundary", path=str(scope_boundary)
            ),
            tech_design_draft=ArtifactRef(
                artifact_type="tech_design_draft", path=str(tech_design_draft)
            ),
            test_checklist=ArtifactRef(
                artifact_type="test_checklist", path=str(test_checklist)
            ),
            implementation_pack=ArtifactRef(
                artifact_type="implementation_pack", path=str(implementation_pack_path)
            ),
            test_pack=ArtifactRef(artifact_type="test_pack", path=str(test_pack_path)),
            execution_pack=ArtifactRef(
                artifact_type="execution_pack", path=str(execution_pack_path)
            ),
        ),
        metadata={
            "workspace_root": "D:/workspace/project",
            "owner": "delivery-planning",
        },
    )


def _make_task(executor_type: str, source_pack_type: str) -> ExecutionTask:
    return ExecutionTask(
        task_id=f"bundle-adapter-001:{source_pack_type}",
        bundle_id="bundle-adapter-001",
        source_pack_type=source_pack_type,
        executor_type=executor_type,
        execution_mode=ExecutionMode.agent_assisted,
        status=ExecutionTaskStatus.pending,
        created_at="2026-03-08T08:00:00+00:00",
        updated_at="2026-03-08T08:00:00+00:00",
        metadata={"priority": "p1"},
    )


def test_base_adapter_subclass_build_pack_writes_request_and_context(
    tmp_path: Path,
) -> None:
    bundle = _make_bundle(tmp_path)
    task = _make_task("dummy", "implementation_pack")

    result = DummyAdapter().build_pack({"task": task, "bundle": bundle})

    request_path = tmp_path / "dummy_request.json"
    context_path = tmp_path / "execution_context.md"

    assert result["request_path"] == str(request_path)
    assert request_path.exists()
    assert context_path.exists()

    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["adapter"] == "dummy"
    assert payload["request_type"] == "dummy.run_pack"
    assert payload["input"]["context_file"] == str(context_path)

    context = context_path.read_text(encoding="utf-8")
    assert context.startswith("# Dummy Execution Context")
    assert "## Prompt" in context
    assert "# Dummy Prompt" in context


def test_codex_adapter_request_payload_contains_expected_fields(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    task = _make_task("codex", "implementation_pack")

    request = CodexAdapter().create_execution_request(task, bundle)

    assert request["adapter"] == "codex"
    assert request["request_type"] == "codex.run_pack"
    assert request["task"]["task_id"] == "bundle-adapter-001:implementation_pack"
    assert request["input"]["workspace_root"] == "D:/workspace/project"
    assert request["input"]["target_modules"] == [
        "pm_pal/adapters/base.py",
        "pm_pal/service/execution_service.py",
    ]
    assert request["input"]["implementation_steps"] == [
        "Read execution pack",
        "Build adapter request payload",
        "Persist execution context",
    ]
    assert request["callback"]["mode"] == "deferred"
    assert request["context_path"].endswith("execution_context.md")
    assert request["prompt"].startswith("# Codex Handoff Prompt")


def test_claude_code_adapter_build_prompt_and_context_generation(
    tmp_path: Path,
) -> None:
    bundle = _make_bundle(tmp_path)
    task = _make_task("claude_code", "test_pack")
    adapter = ClaudeCodeAdapter()

    prompt = adapter.build_prompt(str(tmp_path))
    result = adapter.build_pack({"task": task, "bundle": bundle})

    request_path = tmp_path / "claude_code_request.json"
    context_path = tmp_path / "execution_context.md"

    assert prompt.startswith("# Claude Code Handoff Prompt")
    assert "Validate request payload shape" in prompt
    assert request_path.exists()
    assert context_path.exists()
    assert result["request_path"] == str(request_path)

    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["input"]["test_scope"] == [
        "codex request builder",
        "claude_code request builder",
    ]
    assert payload["input"]["edge_cases"] == [
        "Missing execution pack file",
        "Executor type mismatch",
    ]
    assert payload["input"]["handoff_strategy"] == "sequential"

    context = context_path.read_text(encoding="utf-8")
    assert context.startswith("# Claude Code Execution Context")
    assert "`bundle-adapter-001:test_pack`" in context
    assert "`bundle-adapter-001`" in context
    assert "Prompt rendering includes validation details" in context
    assert "# Claude Code Handoff Prompt" in context


def test_openclaw_adapter_request_payload_contains_verification_scope(
    tmp_path: Path,
) -> None:
    bundle = _make_bundle(tmp_path)
    task = _make_task("openclaw", "implementation_pack")

    request = OpenClawAdapter().create_execution_request(task, bundle)

    assert request["adapter"] == "openclaw"
    assert request["request_type"] == "openclaw.run_pack"
    assert request["input"]["verification_scope"] == [
        "codex request builder",
        "claude_code request builder",
    ]
    assert request["input"]["edge_cases"] == [
        "Missing execution pack file",
        "Executor type mismatch",
    ]
    assert request["input"]["handoff_strategy"] == "sequential"
    assert request["prompt"].startswith("# OpenClaw Handoff Prompt")
