from pm_pal.metrics import build_runtime_trace_summary, compute_runtime_metrics


def test_compute_runtime_metrics_aggregates_latency_cache_and_parallel_flags():
    trace = {
        "parser": {
            "start": "2026-03-06T10:00:00+00:00",
            "end": "2026-03-06T10:00:02+00:00",
            "duration_ms": 2000,
        },
        "planner": {
            "start": "2026-03-06T10:00:02+00:00",
            "end": "2026-03-06T10:00:05+00:00",
            "duration_ms": 3000,
        },
        "risk": {
            "start": "2026-03-06T10:00:02.500000+00:00",
            "end": "2026-03-06T10:00:06+00:00",
            "duration_ms": 3500,
        },
        "planner_risk_parallel": {
            "start": "2026-03-06T10:00:02+00:00",
            "end": "2026-03-06T10:00:06+00:00",
            "parallelized": True,
        },
        "risk_catalog.search": {
            "start": "2026-03-06T10:00:02.600000+00:00",
            "end": "2026-03-06T10:00:02.700000+00:00",
            "duration_ms": 100,
            "cache_hit": True,
            "cache_backend": "memory",
        },
        "other_skill": {
            "start": "2026-03-06T10:00:03+00:00",
            "end": "2026-03-06T10:00:03.100000+00:00",
            "duration_ms": 100,
            "cache_hit": False,
            "cache_backend": "sqlite",
            "status": "error",
        },
    }

    metrics = compute_runtime_metrics(trace)

    assert metrics["total_latency_ms"] == 6000
    assert metrics["planner_latency_ms"] == 3000
    assert metrics["risk_latency_ms"] == 3500
    assert metrics["cache_hit_count"] == 1
    assert metrics["cache_miss_count"] == 1
    assert metrics["cache_total_count"] == 2
    assert metrics["cache_hit_rate"] == 0.5
    assert metrics["slowest_span_name"] == "planner_risk_parallel"
    assert metrics["slowest_span_duration_ms"] == 4000
    assert metrics["parallel_enabled"] is True


def test_build_runtime_trace_summary_exposes_failed_spans_and_backend_usage():
    trace = {
        "parser": {"status": "ok", "duration_ms": 1000},
        "planner": {"status": "ok", "duration_ms": 3000},
        "risk": {"status": "failed", "duration_ms": 2500},
        "risk_catalog.search": {
            "status": "ok",
            "duration_ms": 120,
            "cache_hit": True,
            "cache_backend": "sqlite",
        },
        "implementation.plan": {
            "status": "ok",
            "duration_ms": 220,
            "cache_hit": False,
            "cache_backend": "sqlite",
        },
    }

    summary = build_runtime_trace_summary(trace)

    assert summary["total_spans"] == 5
    assert summary["completed_primary_spans"] == ["parser", "planner"]
    assert summary["failed_spans"] == ["risk"]
    assert summary["cache_backend_usage"] == {"sqlite": 2}
    assert summary["cache_total_count"] == 2
    assert summary["cache_hit_rate"] == 0.5
    assert summary["slowest_span_name"] == "planner"
    assert summary["slowest_span_duration_ms"] == 3000
    assert [item["name"] for item in summary["slowest_spans_top_3"]] == [
        "planner",
        "risk",
        "parser",
    ]
