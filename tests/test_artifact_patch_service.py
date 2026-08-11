from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

from pm_pal.service import (
    apply_artifact_patch_async,
    build_clarification_to_patch_prompt,
)
from pm_pal.workspace import (
    ArtifactPatch,
    ArtifactRepository,
    ArtifactVersion,
    ArtifactVersionStatus,
    WorkspaceRepository,
    WorkspaceState,
    WorkspaceStateStatus,
)


def _write_structured_artifact(path: Path) -> None:
    payload = {
        "artifact_id": "prd_doc",
        "version": 1,
        "title": "Checkout PRD",
        "metadata": {"source": "seed"},
        "blocks": [
            {
                "block_id": "functional.payment_timeout",
                "type": "requirement",
                "title": "支付超时时间",
                "content": "支付超时时间为30分钟。",
                "meta": {"priority": "P2"},
            },
            {
                "block_id": "functional.payment_result",
                "type": "requirement",
                "title": "支付结果提示",
                "content": "支付结果应在页面内提示用户。",
                "meta": {},
            },
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _build_source_version(content_path: Path) -> ArtifactVersion:
    return ArtifactVersion(
        version_id="artifact-v1",
        workspace_id="ws-1",
        artifact_key="prd_doc",
        artifact_type="structured_prd",
        status=ArtifactVersionStatus.active,
        version_number=1,
        title="Checkout PRD",
        source_run_id="seed-run-1",
        created_at="2026-04-13T08:00:00+00:00",
        updated_at="2026-04-13T08:00:00+00:00",
        content_path=str(content_path),
    )


def _build_patch() -> ArtifactPatch:
    return ArtifactPatch.model_validate(
        {
            "schema_version": "1.0",
            "patch_id": "patch-001",
            "artifact_id": "prd_doc",
            "base_version": 1,
            "clarification_id": "clr-001",
            "author": {"type": "llm", "model": "test-model"},
            "summary": "缩短超时并补充恢复机制",
            "ops": [
                {
                    "op_id": "op-1",
                    "action": "replace_text",
                    "target": {
                        "block_id": "functional.payment_timeout",
                        "field": "content",
                    },
                    "old_value": "支付超时时间为30分钟。",
                    "new_value": "支付超时时间为15分钟。",
                    "rationale": "澄清确认库存紧张，需要缩短锁单时间",
                },
                {
                    "op_id": "op-2",
                    "action": "set_field",
                    "target": {
                        "block_id": "functional.payment_timeout",
                        "field": "meta.priority",
                    },
                    "old_value": "P2",
                    "new_value": "P1",
                    "rationale": "该变更影响核心交易链路",
                },
                {
                    "op_id": "op-3",
                    "action": "insert_block_after",
                    "target": {"block_id": "functional.payment_timeout"},
                    "new_block": {
                        "block_id": "edge.payment_timeout_recovery",
                        "type": "requirement",
                        "title": "支付超时后的恢复机制",
                        "content": "订单超时后应自动释放库存，并提示用户重新下单。",
                        "meta": {},
                    },
                    "rationale": "澄清补充了超时后的库存释放规则",
                },
            ],
        }
    )


@pytest.mark.asyncio
async def test_apply_artifact_patch_async_creates_next_version_and_diff(
    tmp_path,
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    content_path = tmp_path / "artifact.v1.json"
    _write_structured_artifact(content_path)

    artifact_repository = ArtifactRepository(db_path)
    workspace_repository = WorkspaceRepository(db_path)
    await artifact_repository.initialize()
    await workspace_repository.initialize()

    source_version = _build_source_version(content_path)
    await artifact_repository.upsert_version(source_version)
    await workspace_repository.upsert_workspace(
        WorkspaceState(
            workspace_id="ws-1",
            name="Workspace 1",
            status=WorkspaceStateStatus.active,
            source_run_id="seed-run-1",
            current_run_id="seed-run-1",
            created_at="2026-04-13T08:00:00+00:00",
            updated_at="2026-04-13T08:00:00+00:00",
            versions=[source_version],
            current_version_ids={"prd_doc": "artifact-v1"},
        )
    )

    result = await apply_artifact_patch_async(
        "artifact-v1",
        _build_patch(),
        {
            "workspace_db_path": str(db_path),
            "artifact_output_root": str(tmp_path / "outputs"),
        },
    )

    assert result.status == "applied"
    assert result.next_version_number == 2
    assert Path(result.content_path).exists()
    assert Path(result.patch_path).exists()
    assert Path(result.diff_path).exists()

    updated_payload = json.loads(Path(result.content_path).read_text(encoding="utf-8"))
    assert updated_payload["version"] == 2
    assert updated_payload["blocks"][0]["content"] == "支付超时时间为15分钟。"
    assert updated_payload["blocks"][0]["meta"]["priority"] == "P1"
    assert updated_payload["blocks"][1]["block_id"] == "edge.payment_timeout_recovery"

    diff_payload = json.loads(Path(result.diff_path).read_text(encoding="utf-8"))
    assert diff_payload["changed_blocks"] == [
        "edge.payment_timeout_recovery",
        "functional.payment_timeout",
    ]

    loaded_version = await artifact_repository.get_version(result.next_version_id)
    assert loaded_version.ok is True
    assert loaded_version.value is not None
    assert loaded_version.value.parent_version_id == "artifact-v1"
    assert loaded_version.value.patch_from_parent_path == result.patch_path

    workspace_result = await workspace_repository.get_workspace("ws-1")
    assert workspace_result.ok is True
    assert workspace_result.value is not None
    assert (
        workspace_result.value.current_version_ids["prd_doc"] == result.next_version_id
    )


@pytest.mark.asyncio
async def test_apply_artifact_patch_async_downgrades_to_review_on_old_value_mismatch(
    tmp_path,
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    content_path = tmp_path / "artifact.v1.json"
    _write_structured_artifact(content_path)

    artifact_repository = ArtifactRepository(db_path)
    await artifact_repository.initialize()
    await artifact_repository.upsert_version(_build_source_version(content_path))

    patch = _build_patch().model_copy(deep=True)
    patch.ops[0].old_value = "支付超时时间为60分钟。"

    result = await apply_artifact_patch_async(
        "artifact-v1",
        patch,
        {
            "workspace_db_path": str(db_path),
            "artifact_output_root": str(tmp_path / "outputs"),
            "failure_mode": "needs_review",
        },
    )

    assert result.status == "needs_review"
    assert result.next_version_id == ""
    assert result.issues[0].code == "old_value_mismatch"


@pytest.mark.asyncio
async def test_apply_artifact_patch_async_rejects_base_version_mismatch(
    tmp_path,
) -> None:
    db_path = tmp_path / "workspace.sqlite3"
    content_path = tmp_path / "artifact.v1.json"
    _write_structured_artifact(content_path)

    artifact_repository = ArtifactRepository(db_path)
    await artifact_repository.initialize()
    await artifact_repository.upsert_version(_build_source_version(content_path))

    patch = _build_patch().model_copy(deep=True)
    patch.base_version = 2

    result = await apply_artifact_patch_async(
        "artifact-v1",
        patch,
        {
            "workspace_db_path": str(db_path),
            "failure_mode": "reject",
        },
    )

    assert result.status == "rejected"
    assert result.issues[0].code == "base_version_mismatch"


def test_build_clarification_to_patch_prompt_is_stable_and_restrictive() -> None:
    prompt = build_clarification_to_patch_prompt(
        artifact_id="prd_doc",
        base_version=3,
        blocks=[
            {
                "block_id": "functional.payment_timeout",
                "type": "requirement",
                "title": "支付超时时间",
                "content": "支付超时时间为30分钟。",
                "meta": {"priority": "P2"},
            }
        ],
        clarification_question="支付超时应该多久？",
        clarification_answer="改成15分钟，并在超时后释放库存。",
    )

    assert "你不能重写整篇 PRD" in prompt
    assert "如果找不到准确 block_id，不要编造，返回空 ops" in prompt
    assert "只输出 patch JSON" in prompt
    assert "functional.payment_timeout" in prompt
