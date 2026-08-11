"""Clarification-to-patch prompt generation and patch application service."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pm_pal.workspace import (
    ArtifactRepository,
    ArtifactVersion,
    ArtifactVersionStatus,
    RepositoryResult,
    TraceLink,
    WorkspaceRepository,
    WorkspaceState,
    WorkspaceStateStatus,
)
from pm_pal.workspace.artifact_patch_models import (
    ArtifactBlock,
    ArtifactPatch,
    ArtifactPatchAction,
    ArtifactPatchApplyResult,
    ArtifactPatchOp,
    PatchApplyIssue,
    PatchApplyOpResult,
    PatchApplyStatus,
    PatchFailureCode,
    PatchFailureMode,
    StructuredArtifactDocument,
)

_DEFAULT_WORKSPACE_DB_PATH = Path("data") / "workspace.sqlite3"


class ArtifactPatchError(RuntimeError):
    """Base error raised by the structured patch service."""


class ArtifactPatchPersistenceError(ArtifactPatchError):
    """Raised when repository persistence fails after a patch is applied."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _resolve_workspace_db_path(options: dict[str, Any] | None) -> Path:
    raw_path = str((options or {}).get("workspace_db_path") or "").strip()
    return Path(raw_path) if raw_path else _DEFAULT_WORKSPACE_DB_PATH


def _resolve_output_root(options: dict[str, Any] | None) -> Path:
    raw_path = str((options or {}).get("artifact_output_root") or "").strip()
    return Path(raw_path) if raw_path else Path("outputs") / "artifact_patches"


def _resolve_failure_mode(options: dict[str, Any] | None) -> PatchFailureMode:
    raw_value = (
        str((options or {}).get("failure_mode") or PatchFailureMode.reject).strip()
        or "reject"
    )
    return PatchFailureMode(raw_value)


def _require_repository_value(result: RepositoryResult[Any], action: str) -> Any:
    if result.ok and result.value is not None:
        return result.value
    if result.error is not None:
        raise ArtifactPatchPersistenceError(
            f"{action} failed: {result.error.message} ({result.error.code})"
        )
    raise ArtifactPatchPersistenceError(f"{action} failed unexpectedly")


def _load_structured_document(version: ArtifactVersion) -> StructuredArtifactDocument:
    content_path = Path(str(version.content_path or "").strip())
    if not content_path.exists() or not content_path.is_file():
        raise FileNotFoundError(
            f"artifact content not found for version_id={version.version_id}: {content_path}"
        )
    payload = json.loads(content_path.read_text(encoding="utf-8"))
    return StructuredArtifactDocument.model_validate(payload)


def _serialize_document(document: StructuredArtifactDocument) -> str:
    return (
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n"
    )


def _build_diff_payload(
    before: StructuredArtifactDocument,
    after: StructuredArtifactDocument,
    patch: ArtifactPatch,
) -> dict[str, Any]:
    before_blocks = {block.block_id: block for block in before.blocks}
    after_blocks = {block.block_id: block for block in after.blocks}
    changed_blocks = sorted(
        block_id
        for block_id in set(before_blocks) | set(after_blocks)
        if before_blocks.get(block_id) != after_blocks.get(block_id)
    )
    return {
        "patch_id": patch.patch_id,
        "artifact_id": patch.artifact_id,
        "base_version": patch.base_version,
        "next_version": after.version,
        "changed_blocks": changed_blocks,
        "ops": [op.model_dump(mode="json") for op in patch.ops],
    }


def _get_block_map(document: StructuredArtifactDocument) -> dict[str, dict[str, Any]]:
    return {
        block.block_id: block.model_dump(mode="python") for block in document.blocks
    }


def _get_field_value(block_payload: dict[str, Any], field: str) -> Any:
    normalized_field = str(field or "").strip()
    if normalized_field == "title":
        return block_payload["title"]
    if normalized_field == "content":
        return block_payload["content"]
    if normalized_field.startswith("meta."):
        meta_key = normalized_field.split(".", 1)[1]
        return block_payload["meta"].get(meta_key)
    raise KeyError(normalized_field)


def _set_field_value(block_payload: dict[str, Any], field: str, value: Any) -> None:
    normalized_field = str(field or "").strip()
    if normalized_field == "title":
        block_payload["title"] = str(value)
        return
    if normalized_field == "content":
        block_payload["content"] = str(value)
        return
    if normalized_field.startswith("meta."):
        meta_key = normalized_field.split(".", 1)[1]
        block_payload["meta"][meta_key] = value
        return
    raise KeyError(normalized_field)


def _append_field_value(block_payload: dict[str, Any], field: str, value: Any) -> None:
    current = _get_field_value(block_payload, field)
    _set_field_value(block_payload, field, f"{current}{value}")


def _match_delete_snapshot(block_payload: dict[str, Any], old_value: Any) -> bool:
    if isinstance(old_value, dict):
        return (
            str(old_value.get("title", "")) == block_payload["title"]
            and str(old_value.get("content", "")) == block_payload["content"]
        )
    return str(old_value) == json.dumps(
        block_payload, ensure_ascii=False, sort_keys=True
    )


def _build_issue(
    code: PatchFailureCode,
    message: str,
    *,
    op: ArtifactPatchOp | None = None,
    details: dict[str, Any] | None = None,
) -> PatchApplyIssue:
    return PatchApplyIssue(
        code=code,
        message=message,
        op_id=op.op_id if op is not None else "",
        target_block_id=op.target.block_id if op is not None else "",
        details=dict(details or {}),
    )


def _failure_status_for_mode(mode: PatchFailureMode) -> PatchApplyStatus:
    if mode == PatchFailureMode.needs_review:
        return PatchApplyStatus.needs_review
    if mode == PatchFailureMode.proposed_not_applied:
        return PatchApplyStatus.proposed_not_applied
    if mode == PatchFailureMode.clarification_required:
        return PatchApplyStatus.clarification_required
    return PatchApplyStatus.rejected


def _result_from_issues(
    patch: ArtifactPatch,
    issues: list[PatchApplyIssue],
    *,
    failure_mode: PatchFailureMode,
    applied_ops: list[PatchApplyOpResult] | None = None,
) -> ArtifactPatchApplyResult:
    message = (
        "; ".join(issue.message for issue in issues) or "Patch application failed."
    )
    return ArtifactPatchApplyResult(
        patch_id=patch.patch_id,
        artifact_id=patch.artifact_id,
        base_version=patch.base_version,
        status=_failure_status_for_mode(failure_mode),
        failure_mode=failure_mode,
        issues=issues,
        applied_ops=list(applied_ops or []),
        message=message,
    )


def _apply_patch_to_document(
    document: StructuredArtifactDocument,
    patch: ArtifactPatch,
) -> tuple[StructuredArtifactDocument, list[PatchApplyOpResult]]:
    if not patch.ops:
        raise ValueError("patch.ops must not be empty")

    working = StructuredArtifactDocument.model_validate(
        document.model_dump(mode="python")
    )
    block_order = [block.block_id for block in working.blocks]
    block_map = _get_block_map(working)
    op_results: list[PatchApplyOpResult] = []

    for op in patch.ops:
        target_payload = block_map.get(op.target.block_id)
        if target_payload is None:
            issue = _build_issue(
                PatchFailureCode.target_not_found,
                f"target block_id not found: {op.target.block_id}",
                op=op,
            )
            raise LookupError(
                json.dumps(issue.model_dump(mode="json"), ensure_ascii=False)
            )

        try:
            if op.action in {
                ArtifactPatchAction.set_field,
                ArtifactPatchAction.replace_text,
            }:
                current_value = _get_field_value(target_payload, op.target.field)
                if current_value != op.old_value:
                    issue = _build_issue(
                        PatchFailureCode.old_value_mismatch,
                        f"old_value mismatch for {op.target.block_id}.{op.target.field}",
                        op=op,
                        details={"expected": op.old_value, "actual": current_value},
                    )
                    raise ValueError(
                        json.dumps(issue.model_dump(mode="json"), ensure_ascii=False)
                    )
                _set_field_value(target_payload, op.target.field, op.new_value)
            elif op.action == ArtifactPatchAction.append_text:
                _append_field_value(target_payload, op.target.field, str(op.new_value))
            elif op.action in {
                ArtifactPatchAction.insert_block_after,
                ArtifactPatchAction.insert_block_before,
            }:
                assert op.new_block is not None
                if op.new_block.block_id in block_map:
                    issue = _build_issue(
                        PatchFailureCode.duplicate_block_id,
                        f"new block_id already exists: {op.new_block.block_id}",
                        op=op,
                    )
                    raise ValueError(
                        json.dumps(issue.model_dump(mode="json"), ensure_ascii=False)
                    )
                anchor_index = block_order.index(op.target.block_id)
                insert_index = (
                    anchor_index + 1
                    if op.action == ArtifactPatchAction.insert_block_after
                    else anchor_index
                )
                block_order.insert(insert_index, op.new_block.block_id)
                block_map[op.new_block.block_id] = op.new_block.model_dump(
                    mode="python"
                )
            elif op.action == ArtifactPatchAction.delete_block:
                if not _match_delete_snapshot(target_payload, op.old_value):
                    issue = _build_issue(
                        PatchFailureCode.old_value_mismatch,
                        f"delete_block snapshot mismatch for {op.target.block_id}",
                        op=op,
                    )
                    raise ValueError(
                        json.dumps(issue.model_dump(mode="json"), ensure_ascii=False)
                    )
                block_order = [
                    block_id
                    for block_id in block_order
                    if block_id != op.target.block_id
                ]
                del block_map[op.target.block_id]

            op_results.append(
                PatchApplyOpResult(
                    op_id=op.op_id,
                    action=op.action,
                    status="applied",
                    target_block_id=op.target.block_id,
                    field=op.target.field,
                    message=op.rationale,
                )
            )
        except KeyError as exc:
            issue = _build_issue(
                PatchFailureCode.unsupported_field,
                f"unsupported field target: {exc.args[0]}",
                op=op,
            )
            raise ValueError(
                json.dumps(issue.model_dump(mode="json"), ensure_ascii=False)
            ) from exc

    working.blocks = [
        ArtifactBlock.model_validate(block_map[block_id]) for block_id in block_order
    ]
    working.version = document.version + 1
    return working, op_results


def _build_workspace_state(
    *,
    source_version: ArtifactVersion,
    next_version: ArtifactVersion,
    existing_workspace: WorkspaceState | None,
) -> WorkspaceState:
    timestamp = next_version.updated_at or next_version.created_at
    if existing_workspace is None:
        return WorkspaceState(
            workspace_id=source_version.workspace_id,
            name=source_version.title or source_version.artifact_key,
            status=WorkspaceStateStatus.active,
            source_run_id=source_version.source_run_id,
            current_run_id=source_version.source_run_id,
            created_at=source_version.created_at or timestamp,
            updated_at=timestamp,
            versions=[source_version, next_version],
            current_version_ids={source_version.artifact_key: next_version.version_id},
            metadata={"artifact_patch_enabled": True},
        )

    workspace = existing_workspace
    workspace.updated_at = timestamp
    workspace.metadata = {**workspace.metadata, "artifact_patch_enabled": True}
    for index, version in enumerate(workspace.versions):
        if version.version_id == next_version.version_id:
            workspace.versions[index] = next_version
            break
    else:
        workspace.versions.append(next_version)
    workspace.current_version_ids[source_version.artifact_key] = next_version.version_id
    return workspace


def build_clarification_to_patch_prompt(
    *,
    artifact_id: str,
    base_version: int,
    blocks: list[dict[str, Any]],
    clarification_question: str,
    clarification_answer: str,
) -> str:
    payload = json.dumps(blocks, ensure_ascii=False, indent=2)
    return (
        "你是需求评审系统中的 Patch 生成器。\n\n"
        "任务：\n"
        "根据 clarification 问答和当前 PRD 分块内容，输出一个结构化 patch JSON。\n"
        "你不能重写整篇 PRD。\n"
        "你只能输出 JSON，不要输出 markdown，不要解释。\n\n"
        "规则：\n"
        "1. 顶层字段必须包含 schema_version, patch_id, artifact_id, base_version, clarification_id, author, ops, summary\n"
        "2. ops 里的 action 只能是 set_field, replace_text, insert_block_after, insert_block_before, append_text, delete_block\n"
        "3. 对 set_field 和 replace_text，必须同时提供 old_value 和 new_value\n"
        "4. 对 insert_block_after 和 insert_block_before，必须提供 new_block\n"
        "5. 不允许生成全文重写或未被 clarification 明确提到的变更\n"
        "6. 如果找不到准确 block_id，不要编造，返回空 ops\n"
        "7. old_value 必须从输入文档逐字拷贝\n"
        "8. 输出必须是合法 JSON\n\n"
        f"[Artifact Metadata]\nartifact_id={artifact_id}\nbase_version={base_version}\n\n"
        f"[Current PRD Blocks]\n{payload}\n\n"
        f"[Clarification]\nquestion={clarification_question}\nanswer={clarification_answer}\n\n"
        "输出：只输出 patch JSON。"
    )


async def apply_artifact_patch_async(
    artifact_version_id: str,
    patch_payload: ArtifactPatch | dict[str, Any],
    options: dict[str, Any] | None = None,
) -> ArtifactPatchApplyResult:
    patch = (
        patch_payload
        if isinstance(patch_payload, ArtifactPatch)
        else ArtifactPatch.model_validate(patch_payload)
    )
    workspace_db_path = _resolve_workspace_db_path(options)
    output_root = _resolve_output_root(options)
    failure_mode = _resolve_failure_mode(options)
    artifact_repository = ArtifactRepository(workspace_db_path)
    workspace_repository = WorkspaceRepository(workspace_db_path)

    _require_repository_value(
        await artifact_repository.initialize(), "artifact_repository.initialize"
    )
    _require_repository_value(
        await workspace_repository.initialize(), "workspace_repository.initialize"
    )

    source_version = _require_repository_value(
        await artifact_repository.get_version(str(artifact_version_id).strip()),
        "artifact_repository.get_version",
    )

    issues: list[PatchApplyIssue] = []
    if patch.artifact_id != source_version.artifact_key:
        issues.append(
            _build_issue(
                PatchFailureCode.artifact_mismatch,
                f"patch artifact_id {patch.artifact_id} does not match source artifact_key {source_version.artifact_key}",
            )
        )
    if patch.base_version != source_version.version_number:
        issues.append(
            _build_issue(
                PatchFailureCode.base_version_mismatch,
                f"patch base_version {patch.base_version} does not match source version {source_version.version_number}",
            )
        )
    if not patch.ops:
        issues.append(
            _build_issue(PatchFailureCode.empty_ops, "patch.ops must not be empty")
        )
    if issues:
        return _result_from_issues(patch, issues, failure_mode=failure_mode)

    source_document = _load_structured_document(source_version)
    try:
        next_document, applied_ops = _apply_patch_to_document(source_document, patch)
    except LookupError as exc:
        issue = PatchApplyIssue.model_validate(json.loads(str(exc)))
        return _result_from_issues(patch, [issue], failure_mode=failure_mode)
    except ValueError as exc:
        try:
            issue = PatchApplyIssue.model_validate(json.loads(str(exc)))
        except json.JSONDecodeError:
            issue = _build_issue(PatchFailureCode.schema_invalid, str(exc))
        return _result_from_issues(patch, [issue], failure_mode=failure_mode)

    target_dir = (
        output_root
        / source_version.workspace_id
        / source_version.artifact_key
        / f"v{next_document.version}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    content_path = target_dir / "artifact.json"
    patch_path = target_dir / "patch.json"
    diff_path = target_dir / "diff.json"
    content_path.write_text(_serialize_document(next_document), encoding="utf-8")
    patch_path.write_text(
        json.dumps(patch.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    diff_payload = _build_diff_payload(source_document, next_document, patch)
    diff_path.write_text(
        json.dumps(diff_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    timestamp = _utc_now_iso()
    next_version = ArtifactVersion(
        version_id=f"apv-{uuid4().hex}",
        workspace_id=source_version.workspace_id,
        artifact_key=source_version.artifact_key,
        artifact_type=source_version.artifact_type,
        status=ArtifactVersionStatus.active,
        version_number=source_version.version_number + 1,
        title=source_version.title,
        parent_version_id=source_version.version_id,
        source_run_id=source_version.source_run_id,
        created_at=timestamp,
        updated_at=timestamp,
        content_path=str(content_path),
        content_checksum=_compute_sha256(content_path),
        diff_from_parent_path=str(diff_path),
        patch_from_parent_path=str(patch_path),
        change_summary=patch.summary or f"Applied patch {patch.patch_id}",
        trace_links=[
            TraceLink(
                trace_id=f"trace-{uuid4().hex}",
                source_type="artifact_version",
                source_id="",
                target_type="artifact_version",
                target_id=source_version.version_id,
                link_type="patched_from",
                source_run_id=source_version.source_run_id,
                metadata={
                    "patch_id": patch.patch_id,
                    "clarification_id": patch.clarification_id,
                },
            )
        ],
        metadata={
            "artifact_id": patch.artifact_id,
            "patch_id": patch.patch_id,
            "clarification_id": patch.clarification_id,
            "patch_author": patch.author.model_dump(mode="json"),
            "patch_summary": patch.summary,
            "applied_op_ids": [item.op_id for item in applied_ops],
        },
    )
    next_version.trace_links[0].source_id = next_version.version_id

    _require_repository_value(
        await artifact_repository.upsert_version(next_version),
        "artifact_repository.upsert_version",
    )

    workspace_result = await workspace_repository.get_workspace(
        source_version.workspace_id
    )
    existing_workspace = workspace_result.value if workspace_result.ok else None
    workspace_state = _build_workspace_state(
        source_version=source_version,
        next_version=next_version,
        existing_workspace=existing_workspace,
    )
    _require_repository_value(
        await workspace_repository.upsert_workspace(workspace_state),
        "workspace_repository.upsert_workspace",
    )

    return ArtifactPatchApplyResult(
        patch_id=patch.patch_id,
        artifact_id=patch.artifact_id,
        base_version=patch.base_version,
        status=PatchApplyStatus.applied,
        failure_mode=failure_mode,
        issues=[],
        applied_ops=applied_ops,
        next_version_id=next_version.version_id,
        next_version_number=next_version.version_number,
        content_path=str(content_path),
        patch_path=str(patch_path),
        diff_path=str(diff_path),
        message=f"Applied patch {patch.patch_id} to artifact {patch.artifact_id}.",
    )
