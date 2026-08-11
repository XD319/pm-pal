"""Shared structured LLM node execution helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pm_pal.runtime.config.config import Config

from ..utils.io import save_raw_agent_output
from ..utils.llm_structured_call import StructuredCallError, llm_structured_call
from ..utils.trace import Span


@dataclass(slots=True)
class StructuredNodeResult:
    """Outcome of one structured LLM node call."""

    output: dict[str, Any]
    trace: dict[str, Any]
    raw_output: str = ""
    status: str = "ok"
    error_message: str = ""
    raw_output_path: str = ""
    output_chars: int = 0
    model: str = "unknown"
    structured_mode: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


def _dump_validated_output(
    value: Any,
    *,
    by_alias: bool,
) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python", by_alias=by_alias)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError(f"validated output is not dict-like: {type(value)!r}")


async def run_structured_node(
    *,
    agent_name: str,
    prompt: str,
    schema: Any,
    validate_output: Callable[[dict[str, Any]], Any],
    empty_output: Callable[[], dict[str, Any]],
    trace: dict[str, Any],
    run_dir: str,
    span: Span,
    trace_key: str | None = None,
    raw_output_agent_name: str | None = None,
    dump_by_alias: bool = False,
    post_process: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> StructuredNodeResult:
    """Run a structured LLM call and record the standard node trace.

    The agent-specific modules keep prompt selection and state shaping; this
    helper owns the common Config lookup, structured call metadata, schema
    validation, raw-output persistence, and degraded empty-output fallback.
    """

    resolved_trace_key = trace_key or agent_name
    raw_agent = raw_output_agent_name or agent_name
    raw = ""
    model = "unknown"
    structured_mode = "unknown"

    try:
        cfg = Config()
        _, model_name = cfg.resolve_llm()
        model = model_name or "unknown"
        span.model = model

        call_meta: dict[str, Any] = {
            "agent_name": agent_name,
            "run_id": os.path.basename(run_dir) if run_dir else "",
        }
        parsed = await llm_structured_call(
            prompt=prompt,
            schema=schema,
            metadata=call_meta,
        )
        structured_mode = str(call_meta.get("structured_mode", "unknown"))
        span.set_attr("structured_mode", structured_mode)
        raw = str(call_meta.get("raw_output", "") or "")

        try:
            output = _dump_validated_output(
                validate_output(parsed),
                by_alias=dump_by_alias,
            )
            if post_process is not None:
                output = post_process(output)
            output_chars = len(raw)
            trace[resolved_trace_key] = span.end(status="ok", output_chars=output_chars)
            return StructuredNodeResult(
                output=output,
                trace=trace,
                raw_output=raw,
                status="ok",
                output_chars=output_chars,
                model=model,
                structured_mode=structured_mode,
                metadata=call_meta,
            )
        except Exception as exc:
            raw_path = (
                save_raw_agent_output(run_dir, raw_agent, raw)
                if run_dir and raw
                else ""
            )
            output = empty_output()
            output_chars = len(raw)
            error_message = f"schema validation failed: {exc}"
            trace[resolved_trace_key] = span.end(
                status="error",
                output_chars=output_chars,
                raw_output_path=raw_path,
                error_message=error_message,
            )
            return StructuredNodeResult(
                output=output,
                trace=trace,
                raw_output=raw,
                status="error",
                error_message=error_message,
                raw_output_path=raw_path,
                output_chars=output_chars,
                model=model,
                structured_mode=structured_mode,
                metadata=call_meta,
            )

    except StructuredCallError as exc:
        raw = exc.raw_output or raw
        structured_mode = exc.structured_mode
        span.set_attr("structured_mode", structured_mode)
        raw_path = (
            save_raw_agent_output(run_dir, raw_agent, raw) if run_dir and raw else ""
        )
        output_chars = len(raw)
        trace[resolved_trace_key] = span.end(
            status="error",
            output_chars=output_chars,
            raw_output_path=raw_path,
            error_message=str(exc),
        )
        return StructuredNodeResult(
            output=empty_output(),
            trace=trace,
            raw_output=raw,
            status="error",
            error_message=str(exc),
            raw_output_path=raw_path,
            output_chars=output_chars,
            model=model,
            structured_mode=structured_mode,
        )
    except Exception as exc:
        raw_path = (
            save_raw_agent_output(run_dir, raw_agent, raw) if run_dir and raw else ""
        )
        output_chars = len(raw)
        trace[resolved_trace_key] = span.end(
            status="error",
            output_chars=output_chars,
            raw_output_path=raw_path,
            error_message=str(exc),
        )
        return StructuredNodeResult(
            output=empty_output(),
            trace=trace,
            raw_output=raw,
            status="error",
            error_message=str(exc),
            raw_output_path=raw_path,
            output_chars=output_chars,
            model=model,
            structured_mode=structured_mode,
        )
