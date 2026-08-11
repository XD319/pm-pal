"""Shared runner for pm_pal CLI and API."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Any

from .memory import (
    DEFAULT_MEMORY_DB_PATH,
    MemoryService,
    process_review_memory_extraction_async,
)
from .monitoring import append_audit_event, normalize_audit_context
from .review.memory_store import FileBackedMemoryStore, NoopMemoryStore
from .review.normalizer import NormalizedRequirement, normalize_requirement
from .workflow import build_review_graph

ProgressHook = Callable[[str, str, dict[str, Any]], None]


def make_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def make_unique_run_id(
    outputs_root: str | Path = "outputs",
    *,
    existing_ids: set[str] | None = None,
    reserve_dir: bool = False,
    max_attempts: int = 30,
) -> str:
    """Return a timestamp run_id that does not already exist under outputs_root.

    The public run_id format is intentionally unchanged because reports, APIs,
    and historical directory scans all rely on YYYYMMDDTHHMMSSZ.
    """

    root = Path(outputs_root)
    if reserve_dir:
        root.mkdir(parents=True, exist_ok=True)
    reserved = set(existing_ids or set())
    last_candidate = ""
    for _ in range(max(1, int(max_attempts))):
        candidate = make_run_id()
        last_candidate = candidate
        if candidate not in reserved and not (root / candidate).exists():
            if reserve_dir:
                try:
                    (root / candidate).mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    sleep(0.05)
                    continue
            return candidate
        sleep(0.05)
    raise RuntimeError(
        f"unable to allocate unique run_id after collision on {last_candidate or '<unknown>'}"
    )


def resolve_model_provider(result: dict[str, Any]) -> tuple[str, str]:
    model, provider = "unknown", "unknown"
    try:
        from pm_pal.runtime.config.config import Config as _Cfg

        cfg = _Cfg()
        model = cfg.llm_model or "unknown"
        provider = cfg.llm_provider or "unknown"
    except Exception:
        trace = result.get("trace", {})
        if isinstance(trace, dict):
            for agent_name in ("parser", "reviewer"):
                maybe_model = trace.get(agent_name, {}).get("model", "")
                if (
                    isinstance(maybe_model, str)
                    and maybe_model
                    and maybe_model not in ("unknown", "none")
                ):
                    model = maybe_model
                    break
    return model, provider


def build_report_data(result: dict[str, Any], run_id: str) -> dict[str, Any]:
    model, provider = resolve_model_provider(result)
    report_data: dict[str, Any] = {
        "schema_version": "v1.1",
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "model": model,
        "provider": provider,
        "project": "pm_pal",
    }
    parallel_review_meta = (
        result.get("parallel_review_meta")
        if isinstance(result.get("parallel_review_meta"), dict)
        else {}
    )
    if not parallel_review_meta:
        parallel_review_meta = (
            result.get("parallel-review_meta")
            if isinstance(result.get("parallel-review_meta"), dict)
            else {}
        )
    if parallel_review_meta:
        memory_influence = (
            parallel_review_meta.get("memory_influence")
            if isinstance(parallel_review_meta.get("memory_influence"), dict)
            else {}
        )
        report_data["observability"] = {
            "selected_profile": str(
                parallel_review_meta.get("selected_profile", "") or ""
            ),
            "profile_routing_reason": str(
                parallel_review_meta.get("profile_routing_reason", "") or ""
            ),
            "memory_mode": str(parallel_review_meta.get("memory_mode", "off") or "off"),
            "retrieved_memories": list(
                parallel_review_meta.get("retrieved_memory_cards", []) or []
            ),
            "rejected_memory_candidates": list(
                parallel_review_meta.get("rejected_memory_candidates", []) or []
            ),
            "memory_influence": {
                "findings": list(memory_influence.get("findings", []) or []),
                "clarification_questions": list(
                    memory_influence.get("clarification_questions", []) or []
                ),
                "open_questions": list(
                    memory_influence.get("open_questions", []) or []
                ),
            },
        }
    report_data.update(result)
    if parallel_review_meta:
        report_data["parallel-review_meta"] = parallel_review_meta
    return report_data


def write_outputs(
    run_dir: str | Path, run_id: str, result: dict[str, Any]
) -> dict[str, str]:
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)

    report_path = run_path / "report.md"
    state_path = run_path / "report.json"
    trace_path = run_path / "run_trace.json"

    report_path.write_text(str(result.get("final_report", "")), encoding="utf-8")
    state_path.write_text(
        json.dumps(build_report_data(result, run_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    trace_path.write_text(
        json.dumps(result.get("trace", {}), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "report_md": str(report_path),
        "report_json": str(state_path),
        "run_trace": str(trace_path),
    }


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_memory_config(
    *,
    outputs_root: str | Path,
    review_memory_path: str | Path | None,
    review_memory_enabled: bool | None,
    review_memory_seeds_dir: str | Path | None,
) -> dict[str, Any]:
    configured_path = str(review_memory_path or "").strip()
    enabled = (
        review_memory_enabled
        if review_memory_enabled is not None
        else _truthy(os.getenv("REVIEW_MEMORY_ENABLED", ""))
    )
    if not configured_path and enabled:
        configured_path = str(Path(outputs_root) / "_memory" / "review_memory.json")
    return {
        "enabled": bool(enabled or configured_path),
        "path": configured_path,
        "seeds_dir": str(
            review_memory_seeds_dir or os.getenv("REVIEW_MEMORY_SEEDS_DIR", "")
        ).strip(),
    }


def _resolve_normalizer_cache_config(
    normalizer_cache_path: str | Path | None,
) -> dict[str, Any]:
    configured_path = str(
        normalizer_cache_path or os.getenv("NORMALIZER_CACHE_PATH", "")
    ).strip()
    return {"path": configured_path}


def _resolve_structured_memory_config(
    *,
    review_memory_db_path: str | Path | None,
    review_memory_extract_enabled: bool | None,
    review_memory_max_kept: int | None,
) -> dict[str, Any]:
    enabled = (
        review_memory_extract_enabled
        if review_memory_extract_enabled is not None
        else _truthy(os.getenv("REVIEW_MEMORY_EXTRACT_ENABLED", ""))
    )
    configured_path = str(review_memory_db_path or "").strip() or str(
        DEFAULT_MEMORY_DB_PATH
    )
    return {
        "enabled": bool(enabled),
        "db_path": configured_path,
        "max_kept": max(1, int(review_memory_max_kept or 3)),
    }


def _resolve_memory_retrieval_config(
    *,
    review_memory_db_path: str | Path | None,
    review_memory_mode: str | None,
) -> dict[str, Any]:
    mode = (
        str(review_memory_mode or os.getenv("REVIEW_MEMORY_MODE", "off"))
        .strip()
        .lower()
        or "off"
    )
    if mode not in {"off", "assist", "strict", "hybrid"}:
        mode = "off"
    configured_path = str(review_memory_db_path or "").strip() or str(
        DEFAULT_MEMORY_DB_PATH
    )
    return {
        "mode": mode,
        "db_path": configured_path,
    }


def _resolve_memory_store(memory_config: dict[str, Any]):
    storage_path = str(memory_config.get("path", "") or "").strip()
    seeds_dir = str(memory_config.get("seeds_dir", "") or "").strip()
    if not storage_path:
        return NoopMemoryStore()
    return FileBackedMemoryStore(storage_path, seeds_dir=seeds_dir or None)


def _normalized_requirement_from_state(
    result: dict[str, Any], requirement_doc: str
) -> NormalizedRequirement:
    payload = (
        result.get("normalized_requirement")
        if isinstance(result.get("normalized_requirement"), dict)
        else {}
    )
    if payload:
        return NormalizedRequirement(
            source_text=str(payload.get("source_text", "") or requirement_doc),
            summary=str(payload.get("summary", "") or ""),
            scenarios=tuple(str(item) for item in payload.get("scenarios", []) or []),
            acceptance_criteria=tuple(
                str(item) for item in payload.get("acceptance_criteria", []) or []
            ),
            dependency_hints=tuple(
                str(item) for item in payload.get("dependency_hints", []) or []
            ),
            risk_hints=tuple(str(item) for item in payload.get("risk_hints", []) or []),
            modules=tuple(str(item) for item in payload.get("modules", []) or []),
            roles=tuple(str(item) for item in payload.get("roles", []) or []),
            headings=tuple(str(item) for item in payload.get("headings", []) or []),
            in_scope=tuple(str(item) for item in payload.get("in_scope", []) or []),
            out_of_scope=tuple(
                str(item) for item in payload.get("out_of_scope", []) or []
            ),
            completeness_signals=tuple(
                str(item) for item in payload.get("completeness_signals", []) or []
            ),
        )
    return normalize_requirement(requirement_doc)


async def run_review(
    requirement_doc: str,
    *,
    run_id: str | None = None,
    outputs_root: str | Path = "outputs",
    progress_hook: ProgressHook | None = None,
    review_mode_override: str | None = None,
    mode: str | None = None,
    review_memory_path: str | Path | None = None,
    review_memory_enabled: bool | None = None,
    review_memory_seeds_dir: str | Path | None = None,
    review_memory_db_path: str | Path | None = None,
    review_memory_extract_enabled: bool | None = None,
    review_memory_max_kept: int | None = None,
    review_memory_mode: str | None = None,
    normalizer_cache_path: str | Path | None = None,
    review_profile: dict[str, Any] | None = None,
    review_profile_pack: dict[str, Any] | None = None,
    canonical_review_request: dict[str, Any] | None = None,
    audit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_run_id = run_id or make_run_id()
    run_dir = os.path.join(str(outputs_root), resolved_run_id)
    memory_config = _resolve_memory_config(
        outputs_root=outputs_root,
        review_memory_path=review_memory_path,
        review_memory_enabled=review_memory_enabled,
        review_memory_seeds_dir=review_memory_seeds_dir,
    )
    normalizer_cache_config = _resolve_normalizer_cache_config(normalizer_cache_path)
    structured_memory_config = _resolve_structured_memory_config(
        review_memory_db_path=review_memory_db_path,
        review_memory_extract_enabled=review_memory_extract_enabled,
        review_memory_max_kept=review_memory_max_kept,
    )
    memory_retrieval_config = _resolve_memory_retrieval_config(
        review_memory_db_path=review_memory_db_path,
        review_memory_mode=review_memory_mode,
    )

    graph = build_review_graph(progress_hook=progress_hook)
    initial_state: dict[str, Any] = {
        "requirement_doc": requirement_doc,
        "run_dir": run_dir,
        "memory_config": memory_config,
        "normalizer_cache_config": normalizer_cache_config,
        "review_profile": dict(review_profile or {}),
        "review_profile_pack": dict(review_profile_pack or {}),
        "canonical_review_request": dict(canonical_review_request or {}),
        "memory_retrieval_config": memory_retrieval_config,
    }
    if isinstance(review_mode_override, str) and review_mode_override.strip():
        initial_state["review_mode_override"] = review_mode_override.strip()
    if isinstance(mode, str) and mode.strip():
        initial_state["mode"] = mode.strip()

    result = await graph.ainvoke(initial_state)
    if not isinstance(result, dict):
        raise ValueError("workflow result must be an object")
    parallel_review_meta = (
        result.get("parallel_review_meta")
        if isinstance(result.get("parallel_review_meta"), dict)
        else {}
    )
    if parallel_review_meta:
        result["parallel-review_meta"] = parallel_review_meta

    memory_store = _resolve_memory_store(memory_config)
    if not isinstance(memory_store, NoopMemoryStore):
        memory_store.import_seeds()
        memory_store.store_review_case(
            run_id=resolved_run_id,
            requirement=_normalized_requirement_from_state(result, requirement_doc),
            review_payload=result.get("parallel_review")
            if isinstance(result.get("parallel_review"), dict)
            else result,
        )

    if structured_memory_config["enabled"]:
        try:
            memory_service = MemoryService.from_db_path(
                structured_memory_config["db_path"]
            )
            await memory_service.initialize()
            extraction = await process_review_memory_extraction_async(
                run_id=resolved_run_id,
                run_dir=run_dir,
                review_result=result,
                memory_service=memory_service,
                canonical_review_request=canonical_review_request,
                review_profile=review_profile,
                audit_context=audit_context,
                max_memories=int(structured_memory_config["max_kept"]),
            )
            result["memory_extraction"] = extraction.to_dict()
        except Exception as exc:
            result["memory_extraction"] = {
                "status": "skipped",
                "error": str(exc),
            }
            append_audit_event(
                run_dir,
                operation="memory_extraction_error",
                status="error",
                run_id=resolved_run_id,
                audit_context=normalize_audit_context(audit_context),
                details={"error": str(exc)},
            )

    report_paths = write_outputs(run_dir=run_dir, run_id=resolved_run_id, result=result)
    return {
        "run_id": resolved_run_id,
        "run_dir": run_dir,
        "result": result,
        "report_paths": report_paths,
    }
