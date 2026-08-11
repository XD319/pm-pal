"""Runtime metrics derived from workflow trace spans."""

from __future__ import annotations

from datetime import datetime
from typing import Any

_PRIMARY_RUNTIME_SPANS = ("parser", "planner", "risk", "reviewer", "reporter")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _span_duration_ms(span: Any) -> int:
    if not isinstance(span, dict):
        return 0
    raw = span.get("duration_ms")
    try:
        if raw is not None:
            return max(0, int(raw or 0))
    except (TypeError, ValueError):
        pass

    start = _parse_iso(span.get("start"))
    end = _parse_iso(span.get("end"))
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _total_latency_ms(trace: dict[str, Any]) -> int:
    starts: list[datetime] = []
    ends: list[datetime] = []
    for span in trace.values():
        if not isinstance(span, dict):
            continue
        start = _parse_iso(span.get("start"))
        end = _parse_iso(span.get("end"))
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    if not starts or not ends:
        return 0
    return max(0, int((max(ends) - min(starts)).total_seconds() * 1000))


def build_runtime_trace_summary(trace: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact completed-run summary derived from trace spans."""

    safe_trace = trace if isinstance(trace, dict) else {}
    cache_backend_usage: dict[str, int] = {}
    cache_hit_count = 0
    cache_miss_count = 0
    completed_primary_spans: list[str] = []
    failed_spans: list[str] = []
    slowest_spans: list[dict[str, Any]] = []

    for name, span in safe_trace.items():
        if not isinstance(span, dict):
            continue

        status = str(span.get("status", "") or "")
        duration_ms = _span_duration_ms(span)
        slowest_spans.append(
            {
                "name": str(name),
                "duration_ms": duration_ms,
                "status": status or "unknown",
            }
        )

        if name in _PRIMARY_RUNTIME_SPANS and status in {"ok", "success", "completed"}:
            completed_primary_spans.append(str(name))
        if status and status not in {"ok", "success", "completed"}:
            failed_spans.append(str(name))

        cache_hit = span.get("cache_hit")
        if cache_hit is True:
            cache_hit_count += 1
        elif cache_hit is False:
            cache_miss_count += 1

        backend_name = str(span.get("cache_backend", "") or "").strip()
        if backend_name:
            cache_backend_usage[backend_name] = (
                cache_backend_usage.get(backend_name, 0) + 1
            )

    slowest_spans.sort(key=lambda item: item["duration_ms"], reverse=True)
    cache_total = cache_hit_count + cache_miss_count
    slowest = (
        slowest_spans[0]
        if slowest_spans
        else {"name": "", "duration_ms": 0, "status": "unknown"}
    )
    return {
        "total_spans": len(
            [span for span in safe_trace.values() if isinstance(span, dict)]
        ),
        "completed_primary_spans": completed_primary_spans,
        "failed_spans": failed_spans,
        "cache_backend_usage": cache_backend_usage,
        "cache_total_count": cache_total,
        "cache_hit_rate": round(cache_hit_count / cache_total, 4)
        if cache_total
        else 0.0,
        "slowest_span_name": str(slowest.get("name", "") or ""),
        "slowest_span_duration_ms": int(slowest.get("duration_ms", 0) or 0),
        "slowest_spans_top_3": slowest_spans[:3],
    }


def compute_runtime_metrics(trace: dict[str, Any] | None) -> dict[str, Any]:
    """Return run-level latency/cache/parallel metrics from trace."""

    safe_trace = trace if isinstance(trace, dict) else {}
    cache_hit_count = 0
    cache_miss_count = 0

    for span in safe_trace.values():
        if not isinstance(span, dict):
            continue
        cache_hit = span.get("cache_hit")
        if cache_hit is True:
            cache_hit_count += 1
        elif cache_hit is False:
            cache_miss_count += 1

    parallel_span = safe_trace.get("planner_risk_parallel", {})
    parallel_enabled = False
    if isinstance(parallel_span, dict):
        parallel_enabled = bool(
            parallel_span.get("parallelized")
            or parallel_span.get("parallel_enabled")
            or parallel_span.get("fan_out") == "parser->planner+risk"
        )

    runtime_summary = build_runtime_trace_summary(safe_trace)

    return {
        "total_latency_ms": _total_latency_ms(safe_trace),
        "planner_latency_ms": _span_duration_ms(safe_trace.get("planner")),
        "risk_latency_ms": _span_duration_ms(safe_trace.get("risk")),
        "cache_hit_count": cache_hit_count,
        "cache_miss_count": cache_miss_count,
        "cache_total_count": runtime_summary["cache_total_count"],
        "cache_hit_rate": runtime_summary["cache_hit_rate"],
        "slowest_span_name": runtime_summary["slowest_span_name"],
        "slowest_span_duration_ms": runtime_summary["slowest_span_duration_ms"],
        "parallel_enabled": parallel_enabled,
    }
