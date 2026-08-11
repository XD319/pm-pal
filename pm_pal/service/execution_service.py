"""Execution orchestration services used by MCP and other entrypoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pm_pal.adapters import get_adapter
from pm_pal.execution import (
    ExecutionEvent,
    ExecutionMode,
    ExecutionTask,
    ExecutionTaskStatus,
    ExecutorRouter,
    TraceabilityMap,
    assign_task,
    cancel_task,
    complete_task,
    fail_task,
    request_review,
    start_task,
)
from pm_pal.monitoring import (
    append_audit_event,
    normalize_audit_context,
    retry_metadata_for_status,
)
from pm_pal.notifications import NotificationType, dispatch_notification
from pm_pal.packs.delivery_bundle import DeliveryBundle
from pm_pal.service.review_service import (
    _load_json_object,
    _locate_bundle_path,
    _resolve_outputs_root,
    _resolve_run_dir,
)
from pm_pal.workspace import ReviewWorkspaceRepository

TASKS_FILENAME = "execution_tasks.json"
TRACEABILITY_FILENAME = "traceability_map.json"
RUN_TRACE_FILENAME = "run_trace.json"
_HANDOFF_AGENT_TO_PACK: dict[str, str] = {
    "codex": "implementation_pack",
    "claude_code": "test_pack",
    "openclaw": "implementation_pack",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_mode(execution_mode: str) -> ExecutionMode:
    normalized = str(execution_mode or "").strip()
    try:
        return ExecutionMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in ExecutionMode)
        raise ValueError(f"execution_mode must be one of: {allowed}") from exc


def _resolve_task_status(status: str) -> ExecutionTaskStatus:
    normalized = str(status or "").strip()
    try:
        resolved = ExecutionTaskStatus(normalized)
    except ValueError as exc:
        allowed = ", ".join(
            item.value
            for item in ExecutionTaskStatus
            if item != ExecutionTaskStatus.pending
        )
        raise ValueError(f"status must be one of: {allowed}") from exc
    if resolved == ExecutionTaskStatus.pending:
        allowed = ", ".join(
            item.value
            for item in ExecutionTaskStatus
            if item != ExecutionTaskStatus.pending
        )
        raise ValueError(f"status must be one of: {allowed}")
    return resolved


def _resolve_task_status_filter(status: str) -> ExecutionTaskStatus:
    normalized = str(status or "").strip()
    try:
        return ExecutionTaskStatus(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ExecutionTaskStatus)
        raise ValueError(f"status filter must be one of: {allowed}") from exc


def _validate_options(options: dict[str, Any] | None) -> dict[str, Any]:
    resolved = options or {}
    if not isinstance(resolved, dict):
        raise TypeError("options must be an object")
    return resolved


def _resolve_audit_context(options: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(options, dict):
        return {}
    return normalize_audit_context(options.get("audit_context"))


def _append_audit_event_safe(
    run_dir: Path,
    *,
    operation: str,
    status: str,
    audit_context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    try:
        append_audit_event(
            run_dir,
            operation=operation,
            status=status,
            audit_context=audit_context,
            **kwargs,
        )
    except Exception:
        pass


def _dispatch_notification(
    run_dir: Path,
    *,
    notification_type: NotificationType | str,
    title: str,
    summary: str = "",
    audit_context: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    dispatch_notification(
        run_dir,
        notification_type=notification_type,
        title=title,
        summary=summary,
        audit_context=audit_context,
        **kwargs,
    )


def _load_bundle_context(
    bundle_id: str, outputs_root: str | Path
) -> tuple[Path, Path, DeliveryBundle]:
    bundle_path = _locate_bundle_path(bundle_id, outputs_root)
    bundle = DeliveryBundle.model_validate(_load_json_object(bundle_path))
    return bundle_path, bundle_path.parent, bundle


def _tasks_path(run_dir: Path) -> Path:
    return run_dir / TASKS_FILENAME


def _traceability_path(run_dir: Path) -> Path:
    return run_dir / TRACEABILITY_FILENAME


def _run_trace_path(run_dir: Path) -> Path:
    return run_dir / RUN_TRACE_FILENAME


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_tasks(tasks: list[ExecutionTask], run_dir: Path) -> Path:
    path = _tasks_path(run_dir)
    payload = {"tasks": [task.model_dump(mode="python") for task in tasks]}
    _write_json(path, payload)
    return path


def _load_tasks(run_dir: Path) -> list[ExecutionTask]:
    payload = _load_json_object(_tasks_path(run_dir))
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    return [
        ExecutionTask.model_validate(item)
        for item in raw_tasks
        if isinstance(item, dict)
    ]


def _load_traceability_payload(run_dir: Path) -> dict[str, Any]:
    return _load_json_object(_traceability_path(run_dir))


def _find_all_run_dirs(outputs_root: str | Path) -> list[Path]:
    root = _resolve_outputs_root(outputs_root)
    if not root.exists():
        return []
    return [path for path in root.iterdir() if path.is_dir()]


def _resolve_adapter(executor_type: str):
    normalized = str(executor_type or "").strip()
    try:
        return get_adapter(normalized)
    except ValueError as exc:
        raise ValueError(
            f"no adapter available for executor_type '{normalized}'"
        ) from exc


def _sanitize_filename_fragment(value: str) -> str:
    raw = str(value or "").strip()
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    normalized = normalized.strip("_")
    return normalized or "task"


def _task_context_path(run_dir: Path, task: ExecutionTask) -> Path:
    return run_dir / f"{_sanitize_filename_fragment(task.task_id)}_execution_context.md"


def _rewrite_request_context_paths(
    request_payload: dict[str, Any], context_path: Path
) -> dict[str, Any]:
    payload = dict(request_payload)
    payload["context_path"] = str(context_path)

    current_input = payload.get("input")
    if isinstance(current_input, dict):
        updated_input = dict(current_input)
        updated_input["context_file"] = str(context_path)
        payload["input"] = updated_input

    current_callback = payload.get("callback")
    if isinstance(current_callback, dict):
        updated_callback = dict(current_callback)
        updated_callback["context_file"] = str(context_path)
        payload["callback"] = updated_callback

    return payload


def _attach_adapter_artifacts(
    task: ExecutionTask,
    *,
    request_path: Path,
    context_path: Path,
    request_payload: dict[str, Any],
) -> ExecutionTask:
    payload = task.model_dump(mode="python")
    metadata = dict(task.metadata or {})
    metadata["adapter_artifacts"] = {
        "adapter": task.executor_type,
        "request_path": str(request_path),
        "context_path": str(context_path),
        "request_type": str(request_payload.get("request_type", "") or ""),
    }
    payload["metadata"] = metadata
    return ExecutionTask.model_validate(payload)


def _materialize_adapter_requests(
    tasks: list[ExecutionTask], bundle: DeliveryBundle, run_dir: Path
) -> list[ExecutionTask]:
    materialized_tasks: list[ExecutionTask] = []
    for task in tasks:
        adapter = _resolve_adapter(task.executor_type)
        result = adapter.build_pack({"task": task, "bundle": bundle})

        request_path = Path(str(result.get("request_path", "")).strip())
        request_payload = (
            result.get("request") if isinstance(result.get("request"), dict) else {}
        )
        if not request_path.name:
            raise ValueError(
                f"adapter did not return a request path for task '{task.task_id}'"
            )
        if not request_payload:
            request_payload = _load_json_object(request_path)

        temporary_context_path = Path(str(result.get("context_path", "")).strip())
        final_context_path = _task_context_path(run_dir, task)
        if temporary_context_path.exists():
            final_context_path.write_text(
                temporary_context_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            if temporary_context_path != final_context_path:
                temporary_context_path.unlink()

        updated_request_payload = _rewrite_request_context_paths(
            request_payload, final_context_path
        )
        _write_json(request_path, updated_request_payload)

        materialized_tasks.append(
            _attach_adapter_artifacts(
                task,
                request_path=request_path,
                context_path=final_context_path,
                request_payload=updated_request_payload,
            )
        )
    return materialized_tasks


def _normalize_agent_selection(agent: str) -> str:
    normalized = str(agent or "all").strip().lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "all": "all",
        "codex": "codex",
        "claude_code": "claude_code",
        "claude": "claude_code",
        "openclaw": "openclaw",
    }
    try:
        return alias_map[normalized]
    except KeyError as exc:
        allowed = ", ".join(sorted(alias_map))
        raise ValueError(f"agent must be one of: {allowed}") from exc


def _selected_agents(agent: str) -> list[str]:
    normalized = _normalize_agent_selection(agent)
    if normalized == "all":
        return ["codex", "claude_code", "openclaw"]
    return [normalized]


def _build_prepare_handoff_tasks(
    bundle: DeliveryBundle,
    *,
    agent: str,
    execution_mode: ExecutionMode,
) -> list[ExecutionTask]:
    now = _utc_now_iso()
    tasks: list[ExecutionTask] = []
    for executor_type in _selected_agents(agent):
        source_pack_type = _HANDOFF_AGENT_TO_PACK[executor_type]
        artifact_ref = getattr(bundle.artifacts, source_pack_type, None)
        if artifact_ref is None or not str(artifact_ref.path or "").strip():
            raise FileNotFoundError(
                f"{source_pack_type} is unavailable for agent '{executor_type}'"
            )
        task_id = f"{bundle.bundle_id}:{executor_type}:{source_pack_type}"
        tasks.append(
            ExecutionTask(
                task_id=task_id,
                bundle_id=bundle.bundle_id,
                source_pack_type=source_pack_type,
                executor_type=executor_type,
                execution_mode=execution_mode,
                created_at=now,
                updated_at=now,
                execution_log=[
                    ExecutionEvent(
                        event_id=f"{task_id}:prepared",
                        timestamp=now,
                        event_type="prepared",
                        detail=f"Prepared handoff request for {executor_type}",
                        actor="agent_handoff",
                    )
                ],
                metadata={
                    "artifact_path": str(artifact_ref.path),
                    "preview_only": True,
                    "generated_via": "prepare_agent_handoff",
                },
            )
        )
    return tasks


def _prompt_path_for_agent(run_dir: Path, agent: str) -> str:
    prompt_path = run_dir / f"{agent}_prompt.md"
    return str(prompt_path) if prompt_path.exists() else ""


def prepare_agent_handoff_for_run_for_mcp(
    *,
    run_id: str,
    agent: str = "all",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = _validate_options(options)
    audit_context = _resolve_audit_context(resolved_options)
    outputs_root = resolved_options.get("outputs_root", "outputs")
    run_dir = _resolve_run_dir(run_id, outputs_root)
    bundle_path = run_dir / "delivery_bundle.json"
    if not bundle_path.exists():
        raise FileNotFoundError(f"delivery_bundle.json not found for run_id={run_id}")

    bundle = DeliveryBundle.model_validate(_load_json_object(bundle_path))
    execution_mode = _resolve_mode(
        str(
            resolved_options.get("execution_mode", "agent_assisted") or "agent_assisted"
        )
    )
    materialized_tasks = _materialize_adapter_requests(
        _build_prepare_handoff_tasks(
            bundle, agent=agent, execution_mode=execution_mode
        ),
        bundle,
        run_dir,
    )

    requests: list[dict[str, Any]] = []
    for task in materialized_tasks:
        adapter_artifacts = (
            task.metadata.get("adapter_artifacts")
            if isinstance(task.metadata, dict)
            else {}
        )
        request_path = Path(
            str(adapter_artifacts.get("request_path", "") or "").strip()
        )
        context_path = str(adapter_artifacts.get("context_path", "") or "").strip()
        request_payload = _load_json_object(request_path) if request_path.name else {}
        requests.append(
            {
                "agent": task.executor_type,
                "task_id": task.task_id,
                "source_pack_type": task.source_pack_type,
                "request_type": str(adapter_artifacts.get("request_type", "") or ""),
                "request_path": str(request_path),
                "context_path": context_path,
                "prompt_path": _prompt_path_for_agent(run_dir, task.executor_type),
                "request": request_payload,
            }
        )

    _append_audit_event_safe(
        run_dir,
        operation="prepare_agent_handoff",
        status="prepared",
        run_id=str(bundle.source_run_id),
        bundle_id=str(bundle.bundle_id),
        audit_context=audit_context,
        details={
            "agent_selection": _normalize_agent_selection(agent),
            "request_count": len(requests),
            "agents": [item["agent"] for item in requests],
        },
        retry=retry_metadata_for_status(status="prepared", non_blocking=False),
    )

    return {
        "run_id": str(bundle.source_run_id),
        "bundle_id": str(bundle.bundle_id),
        "status": "prepared",
        "agent_selection": _normalize_agent_selection(agent),
        "handoff_source": bundle.metadata.get("handoff_source", {})
        if isinstance(bundle.metadata, dict)
        else {},
        "request_count": len(requests),
        "requests": requests,
        "paths": {
            "run_dir": str(run_dir),
            "delivery_bundle_path": str(bundle_path),
            "execution_pack_path": str(bundle.artifacts.execution_pack.path or ""),
        },
    }


def _load_task_context(
    task_id: str, outputs_root: str | Path
) -> tuple[Path, DeliveryBundle, list[ExecutionTask], int]:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise ValueError("task_id is required")

    for run_dir in _find_all_run_dirs(outputs_root):
        tasks = _load_tasks(run_dir)
        for index, task in enumerate(tasks):
            if task.task_id != normalized_task_id:
                continue
            bundle_payload = _load_json_object(run_dir / "delivery_bundle.json")
            if not bundle_payload:
                raise FileNotFoundError(
                    f"delivery_bundle.json not found for run_id={run_dir.name}"
                )
            bundle = DeliveryBundle.model_validate(bundle_payload)
            return run_dir, bundle, tasks, index
    raise FileNotFoundError(f"execution task not found: {normalized_task_id}")


def _normalize_artifact_paths(artifact_paths: dict[str, Any] | None) -> dict[str, str]:
    if artifact_paths is None:
        return {}
    if not isinstance(artifact_paths, dict):
        raise TypeError("artifact_paths must be an object")

    normalized: dict[str, str] = {}
    for raw_key, raw_value in artifact_paths.items():
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if key and value:
            normalized[key] = value
    return normalized


def append_execution_event(
    task: ExecutionTask,
    *,
    actor: str,
    event_type: str,
    detail: str = "",
) -> ExecutionTask:
    now = _utc_now_iso()
    execution_log = list(task.execution_log)
    execution_log.append(
        ExecutionEvent(
            event_id=f"{task.task_id}:{event_type}:{len(execution_log) + 1}",
            timestamp=now,
            event_type=str(event_type or "update").strip() or "update",
            detail=str(detail or "").strip(),
            actor=str(actor or "system").strip() or "system",
        )
    )
    payload = task.model_dump(mode="python")
    payload["updated_at"] = now
    payload["execution_log"] = execution_log
    return ExecutionTask.model_validate(payload)


def _merge_task_writeback(
    task: ExecutionTask,
    *,
    assigned_to: str = "",
    result_summary: str = "",
    artifact_paths: dict[str, str] | None = None,
) -> ExecutionTask:
    payload = task.model_dump(mode="python")
    metadata = dict(task.metadata or {})

    normalized_assigned_to = str(assigned_to or "").strip()
    normalized_result_summary = str(result_summary or "").strip()
    if normalized_assigned_to:
        payload["assigned_to"] = normalized_assigned_to
    if normalized_result_summary:
        payload["result_summary"] = normalized_result_summary

    if artifact_paths:
        existing_artifact_paths = metadata.get("artifact_paths")
        merged_artifact_paths = (
            dict(existing_artifact_paths)
            if isinstance(existing_artifact_paths, dict)
            else {}
        )
        merged_artifact_paths.update(artifact_paths)
        metadata["artifact_paths"] = merged_artifact_paths

    payload["metadata"] = metadata
    return ExecutionTask.model_validate(payload)


def _apply_lifecycle_update(
    task: ExecutionTask,
    *,
    to_status: ExecutionTaskStatus,
    actor: str,
    detail: str,
    result_summary: str,
    assigned_to: str,
) -> tuple[ExecutionTask, bool]:
    normalized_actor = (
        str(actor or "").strip()
        or str(task.assigned_to or "").strip()
        or task.executor_type
        or "external_executor"
    )
    normalized_detail = str(detail or "").strip()
    normalized_result_summary = str(result_summary or "").strip()
    normalized_assigned_to = str(assigned_to or "").strip()

    if str(task.status) == to_status.value:
        return task, False

    if to_status == ExecutionTaskStatus.assigned:
        executor = (
            normalized_assigned_to
            or str(task.assigned_to or "").strip()
            or normalized_actor
        )
        return assign_task(task, executor=executor, actor=normalized_actor), True
    if to_status == ExecutionTaskStatus.in_progress:
        return start_task(task, actor=normalized_actor), True
    if to_status == ExecutionTaskStatus.waiting_review:
        return request_review(
            task, actor=normalized_actor, detail=normalized_detail or "checkpoint ready"
        ), True
    if to_status == ExecutionTaskStatus.completed:
        summary = normalized_result_summary or normalized_detail or "completed"
        return complete_task(task, actor=normalized_actor, result_summary=summary), True
    if to_status == ExecutionTaskStatus.failed:
        reason = normalized_result_summary or normalized_detail or "failed"
        return fail_task(task, actor=normalized_actor, reason=reason), True
    if to_status == ExecutionTaskStatus.cancelled:
        reason = normalized_detail or normalized_result_summary or "cancelled"
        return cancel_task(task, actor=normalized_actor, reason=reason), True
    raise ValueError(f"unsupported execution task status: {to_status}")


def _build_execution_snapshot_payload(
    *,
    run_dir: Path,
    bundle: DeliveryBundle,
    tasks: list[ExecutionTask],
    updated_at: str,
    last_task: ExecutionTask,
) -> dict[str, Any]:
    repository = ReviewWorkspaceRepository(run_dir)
    base_snapshot = repository.build_status_snapshot(
        run_id=bundle.source_run_id,
        bundle_id=bundle.bundle_id,
        bundle_status=bundle.status,
        updated_at=updated_at,
    )

    counts = {status.value: 0 for status in ExecutionTaskStatus}
    for task in tasks:
        counts[str(task.status)] = counts.get(str(task.status), 0) + 1

    payload = base_snapshot.model_dump(mode="python")
    payload["execution"] = {
        "updated_at": updated_at,
        "last_task_id": last_task.task_id,
        "last_status": last_task.status,
        "task_count": len(tasks),
        "counts": counts,
        "tasks": [
            {
                "task_id": task.task_id,
                "source_pack_type": task.source_pack_type,
                "executor_type": task.executor_type,
                "status": task.status,
                "assigned_to": task.assigned_to,
                "updated_at": task.updated_at,
            }
            for task in tasks
        ],
    }
    return payload


def _save_execution_status_snapshot(
    run_dir: Path,
    bundle: DeliveryBundle,
    tasks: list[ExecutionTask],
    updated_task: ExecutionTask,
) -> tuple[Path, dict[str, Any]]:
    repository = ReviewWorkspaceRepository(run_dir)
    existing_payload = _load_json_object(repository.status_snapshot_path)
    updated_payload = dict(existing_payload) if existing_payload else {}
    updated_payload.update(
        _build_execution_snapshot_payload(
            run_dir=run_dir,
            bundle=bundle,
            tasks=tasks,
            updated_at=updated_task.updated_at,
            last_task=updated_task,
        )
    )
    _write_json(repository.status_snapshot_path, updated_payload)
    return repository.status_snapshot_path, updated_payload


def _append_execution_update_trace(
    run_dir: Path,
    *,
    task: ExecutionTask,
    from_status: str,
    actor: str,
    detail: str,
    result_summary: str,
    artifact_paths: dict[str, str],
) -> tuple[Path, dict[str, Any]]:
    trace_path = _run_trace_path(run_dir)
    trace_payload = _load_json_object(trace_path)
    execution_updates = trace_payload.get("execution_updates")
    if not isinstance(execution_updates, list):
        execution_updates = []

    entry = {
        "timestamp": task.updated_at,
        "task_id": task.task_id,
        "bundle_id": task.bundle_id,
        "from_status": from_status,
        "to_status": task.status,
        "actor": str(actor or "").strip() or "external_executor",
        "detail": str(detail or "").strip(),
        "result_summary": str(result_summary or task.result_summary or "").strip(),
        "artifact_paths": artifact_paths,
        "assigned_to": task.assigned_to,
    }
    execution_updates.append(entry)
    trace_payload["execution_updates"] = execution_updates
    _write_json(trace_path, trace_payload)
    return trace_path, entry


def handoff_to_executor_for_mcp(
    *,
    bundle_id: str,
    execution_mode: str = "agent_assisted",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = _validate_options(options)
    audit_context = _resolve_audit_context(resolved_options)
    _, run_dir, bundle = _load_bundle_context(
        bundle_id, resolved_options.get("outputs_root", "outputs")
    )
    router = ExecutorRouter(default_mode=_resolve_mode(execution_mode))
    tasks = router.route(bundle)
    tasks = _materialize_adapter_requests(tasks, bundle, run_dir)
    tasks_path = _save_tasks(tasks, run_dir)

    traceability = TraceabilityMap().build_from_bundle(bundle, tasks)
    traceability_path = _traceability_path(run_dir)
    traceability.save(traceability_path)

    _append_audit_event_safe(
        run_dir,
        operation="handoff",
        status="routed",
        run_id=str(bundle.source_run_id),
        bundle_id=str(bundle.bundle_id),
        audit_context=audit_context,
        details={
            "execution_mode": execution_mode,
            "task_count": len(tasks),
            "tasks_path": str(tasks_path),
            "traceability_path": str(traceability_path),
        },
        retry=retry_metadata_for_status(status="routed", non_blocking=False),
    )
    _dispatch_notification(
        run_dir,
        notification_type=NotificationType.executor_handoff_created,
        title=f"Executor handoff created: {bundle.bundle_id}",
        summary=f"{len(tasks)} execution tasks were routed in {execution_mode} mode.",
        run_id=str(bundle.source_run_id),
        bundle_id=str(bundle.bundle_id),
        metadata={
            "execution_mode": execution_mode,
            "task_count": len(tasks),
            "tasks_path": str(tasks_path),
            "traceability_path": str(traceability_path),
        },
        audit_context=audit_context,
    )

    return {
        "bundle_id": bundle.bundle_id,
        "status": "routed",
        "task_count": len(tasks),
        "tasks": [task.model_dump(mode="python") for task in tasks],
        "paths": {
            "bundle_path": str(run_dir / "delivery_bundle.json"),
            "tasks_path": str(tasks_path),
            "traceability_path": str(traceability_path),
        },
    }


def list_execution_tasks_for_mcp(
    *,
    bundle_id: str | None = None,
    status: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = _validate_options(options)
    outputs_root = resolved_options.get("outputs_root", "outputs")
    normalized_bundle_id = str(bundle_id or "").strip()
    normalized_status = (
        _resolve_task_status_filter(status) if str(status or "").strip() else None
    )

    tasks: list[ExecutionTask] = []
    bundle_ids: list[str] = []
    if normalized_bundle_id:
        _, run_dir, bundle = _load_bundle_context(normalized_bundle_id, outputs_root)
        tasks = _load_tasks(run_dir)
        bundle_ids = [bundle.bundle_id]
    else:
        for run_dir in _find_all_run_dirs(outputs_root):
            run_tasks = _load_tasks(run_dir)
            if not run_tasks:
                continue
            tasks.extend(run_tasks)
            bundle_ids.extend({task.bundle_id for task in run_tasks})

    if normalized_status is not None:
        tasks = [task for task in tasks if str(task.status) == normalized_status.value]

    counts: dict[str, int] = {}
    for task in tasks:
        counts[str(task.status)] = counts.get(str(task.status), 0) + 1

    return {
        "count": len(tasks),
        "tasks": [task.model_dump(mode="python") for task in tasks],
        "bundles": sorted(set(bundle_ids)),
        "counts": counts,
    }


def get_execution_status_for_mcp(
    *,
    bundle_id: str | None = None,
    task_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = _validate_options(options)
    outputs_root = resolved_options.get("outputs_root", "outputs")
    if bool(str(bundle_id or "").strip()) == bool(str(task_id or "").strip()):
        raise ValueError("provide exactly one of bundle_id or task_id")

    if bundle_id:
        _, run_dir, bundle = _load_bundle_context(bundle_id, outputs_root)
        tasks = _load_tasks(run_dir)
        traceability = _load_traceability_payload(run_dir)
        return {
            "bundle_id": bundle.bundle_id,
            "task_count": len(tasks),
            "tasks": [task.model_dump(mode="python") for task in tasks],
            "traceability": traceability,
        }

    run_dir, _bundle, tasks, index = _load_task_context(
        str(task_id or ""), outputs_root
    )
    task = tasks[index]
    traceability = _load_traceability_payload(run_dir)
    links = [
        link
        for link in traceability.get("links", [])
        if isinstance(link, dict) and link.get("execution_task_id") == task.task_id
    ]
    return {
        "bundle_id": task.bundle_id,
        "task": task.model_dump(mode="python"),
        "traceability": {"links": links, "counts": {"total": len(links)}},
    }


def update_execution_task_for_mcp(
    *,
    task_id: str,
    status: str,
    actor: str = "",
    assigned_to: str = "",
    detail: str = "",
    result_summary: str = "",
    artifact_paths: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = _validate_options(options)
    audit_context = _resolve_audit_context(resolved_options)
    outputs_root = resolved_options.get("outputs_root", "outputs")
    target_status = _resolve_task_status(status)
    normalized_actor = str(actor or "").strip() or "external_executor"
    normalized_assigned_to = str(assigned_to or "").strip()
    normalized_detail = str(detail or "").strip()
    normalized_result_summary = str(result_summary or "").strip()
    normalized_artifact_paths = _normalize_artifact_paths(artifact_paths)

    run_dir, bundle, tasks, index = _load_task_context(task_id, outputs_root)
    current_task = tasks[index]
    previous_status = str(current_task.status)

    updated_task, transitioned = _apply_lifecycle_update(
        current_task,
        to_status=target_status,
        actor=normalized_actor,
        detail=normalized_detail,
        result_summary=normalized_result_summary,
        assigned_to=normalized_assigned_to,
    )
    updated_task = _merge_task_writeback(
        updated_task,
        assigned_to=normalized_assigned_to,
        result_summary=normalized_result_summary,
        artifact_paths=normalized_artifact_paths,
    )

    supplemental_details: list[str] = []
    if not transitioned:
        supplemental_details.append(f"status confirmed: {target_status.value}")
    if normalized_detail and (
        not transitioned
        or target_status
        in {ExecutionTaskStatus.assigned, ExecutionTaskStatus.in_progress}
    ):
        supplemental_details.append(normalized_detail)
    if normalized_artifact_paths:
        supplemental_details.append(
            "artifact_paths=" + ", ".join(sorted(normalized_artifact_paths))
        )
    if normalized_assigned_to and target_status != ExecutionTaskStatus.assigned:
        supplemental_details.append(f"assigned_to={normalized_assigned_to}")
    if normalized_result_summary and target_status not in {
        ExecutionTaskStatus.completed,
        ExecutionTaskStatus.failed,
    }:
        supplemental_details.append(f"result_summary={normalized_result_summary}")
    if supplemental_details:
        updated_task = append_execution_event(
            updated_task,
            actor=normalized_actor,
            event_type="writeback",
            detail="; ".join(supplemental_details),
        )

    tasks[index] = updated_task
    tasks_path = _save_tasks(tasks, run_dir)
    status_snapshot_path, status_snapshot = _save_execution_status_snapshot(
        run_dir, bundle, tasks, updated_task
    )
    trace_path, trace_entry = _append_execution_update_trace(
        run_dir,
        task=updated_task,
        from_status=previous_status,
        actor=normalized_actor,
        detail=normalized_detail,
        result_summary=normalized_result_summary,
        artifact_paths=normalized_artifact_paths,
    )

    _append_audit_event_safe(
        run_dir,
        operation="execution_update",
        status=str(updated_task.status),
        run_id=str(bundle.source_run_id),
        bundle_id=str(updated_task.bundle_id),
        task_id=str(updated_task.task_id),
        actor=normalized_actor,
        audit_context=audit_context,
        details={
            "from_status": previous_status,
            "to_status": str(updated_task.status),
            "detail": normalized_detail,
            "result_summary": normalized_result_summary,
            "assigned_to": str(updated_task.assigned_to or ""),
            "artifact_paths": normalized_artifact_paths,
            "transitioned": transitioned,
        },
        retry=retry_metadata_for_status(
            status=str(updated_task.status), non_blocking=False
        ),
    )
    if transitioned and str(updated_task.status) in {
        ExecutionTaskStatus.completed.value,
        ExecutionTaskStatus.failed.value,
    }:
        notification_type = (
            NotificationType.execution_completed
            if str(updated_task.status) == ExecutionTaskStatus.completed.value
            else NotificationType.execution_failed
        )
        title = (
            f"Execution completed: {updated_task.task_id}"
            if notification_type == NotificationType.execution_completed
            else f"Execution failed: {updated_task.task_id}"
        )
        summary = (
            normalized_result_summary
            or normalized_detail
            or str(updated_task.result_summary or updated_task.status)
        )
        _dispatch_notification(
            run_dir,
            notification_type=notification_type,
            title=title,
            summary=summary,
            run_id=str(bundle.source_run_id),
            bundle_id=str(updated_task.bundle_id),
            task_id=str(updated_task.task_id),
            metadata={
                "from_status": previous_status,
                "to_status": str(updated_task.status),
                "assigned_to": str(updated_task.assigned_to or ""),
                "detail": normalized_detail,
                "result_summary": normalized_result_summary,
                "artifact_paths": normalized_artifact_paths,
            },
            audit_context=audit_context,
        )

    return {
        "bundle_id": updated_task.bundle_id,
        "task_id": updated_task.task_id,
        "task": updated_task.model_dump(mode="python"),
        "tasks_path": str(tasks_path),
        "status_snapshot_path": str(status_snapshot_path),
        "status_snapshot": status_snapshot,
        "trace_path": str(trace_path),
        "trace_entry": trace_entry,
    }


def get_traceability_for_mcp(
    *,
    requirement_id: str | None = None,
    task_id: str | None = None,
    bundle_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_options = _validate_options(options)
    outputs_root = resolved_options.get("outputs_root", "outputs")
    requirement_key = str(requirement_id or "").strip()
    task_key = str(task_id or "").strip()
    bundle_key = str(bundle_id or "").strip()
    if not any((requirement_key, task_key, bundle_key)):
        raise ValueError(
            "provide at least one of requirement_id, task_id, or bundle_id"
        )

    if bundle_key:
        _, run_dir, bundle = _load_bundle_context(bundle_key, outputs_root)
        payload = _load_traceability_payload(run_dir)
        payload["bundle_id"] = bundle.bundle_id
        return payload

    matching_links: list[dict[str, Any]] = []
    matched_bundles: list[str] = []
    for run_dir in _find_all_run_dirs(outputs_root):
        payload = _load_traceability_payload(run_dir)
        links = payload.get("links")
        if not isinstance(links, list):
            continue
        filtered = []
        for link in links:
            if not isinstance(link, dict):
                continue
            if requirement_key and link.get("requirement_id") != requirement_key:
                continue
            if task_key and link.get("execution_task_id") != task_key:
                continue
            filtered.append(link)
        if filtered:
            matching_links.extend(filtered)
            matched_bundles.append(run_dir.name)

    return {
        "links": matching_links,
        "count": len(matching_links),
        "bundles": matched_bundles,
    }
