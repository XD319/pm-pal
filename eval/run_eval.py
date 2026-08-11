"""Minimal regression evaluator for pm_pal workflow.

Usage:
    python eval/run_eval.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pm_pal.metrics import build_runtime_trace_summary  # noqa: E402
from pm_pal.workflow import build_review_graph  # noqa: E402

REQUIRED_TRACE_AGENTS = ("parser", "planner", "risk", "reviewer", "reporter")
REQUIRED_TRACE_FIELDS = (
    "start",
    "end",
    "duration_ms",
    "model",
    "status",
    "input_chars",
    "output_chars",
    "raw_output_path",
    "error_message",
)
REQUIRED_METRICS_FIELDS = (
    "coverage_ratio",
    "uncovered_requirements",
    "requirement_to_tasks",
    "total_latency_ms",
    "planner_latency_ms",
    "risk_latency_ms",
    "cache_hit_count",
    "cache_miss_count",
    "cache_total_count",
    "cache_hit_rate",
    "slowest_span_name",
    "slowest_span_duration_ms",
    "parallel_enabled",
)
DEFAULT_PROFILE = "full"
DEFAULT_WORKERS = 1
SMOKE_CASE_IDS = (
    "prd_case_01",
    "prd_case_05",
    "prd_case_11",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run minimal workflow regression eval")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("eval/cases/prd_test_inputs.jsonl"),
        help="Path to eval case jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("eval/eval_report.json"),
        help="Path to aggregated eval report",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("eval/runs"),
        help="Directory to store per-case workflow outputs",
    )
    parser.add_argument(
        "--profile",
        choices=("smoke", "full"),
        default=DEFAULT_PROFILE,
        help="Eval profile to run. Defaults to full.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Maximum number of eval cases to execute concurrently. Defaults to 1.",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    return args


def _load_cases(cases_path: Path) -> list[dict[str, Any]]:
    if not cases_path.exists():
        raise FileNotFoundError(f"Cases file not found: {cases_path}")

    cases: list[dict[str, Any]] = []
    for index, line in enumerate(
        cases_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {index}: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"Case at line {index} must be a JSON object")
        case_id = item.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Missing or invalid case_id at line {index}")
        cases.append(item)
    return cases


def _select_cases(cases: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    if profile == "full":
        return cases

    smoke_cases = [case for case in cases if str(case.get("case_id")) in SMOKE_CASE_IDS]
    if smoke_cases:
        return smoke_cases

    raise ValueError(
        "Smoke profile selected but no smoke cases were found. "
        f"Expected one of: {', '.join(SMOKE_CASE_IDS)}"
    )


def _case_to_requirement_doc(case: dict[str, Any]) -> str:
    return json.dumps(case, ensure_ascii=False, indent=2)


def _resolve_model_provider(result: dict[str, Any]) -> tuple[str, str]:
    model = "unknown"
    provider = "unknown"
    try:
        from pm_pal.runtime.config.config import Config as _Cfg

        cfg = _Cfg()
        model = cfg.llm_model or "unknown"
        provider = cfg.llm_provider or "unknown"
    except Exception:
        trace = result.get("trace", {})
        if isinstance(trace, dict):
            for agent_name in ("parser", "reviewer"):
                span = trace.get(agent_name, {})
                if isinstance(span, dict):
                    maybe_model = span.get("model", "")
                    if (
                        isinstance(maybe_model, str)
                        and maybe_model
                        and maybe_model not in ("unknown", "none")
                    ):
                        model = maybe_model
                        break
    return model, provider


def _build_report_json(result: dict[str, Any], run_id: str) -> dict[str, Any]:
    model, provider = _resolve_model_provider(result)
    report_data: dict[str, Any] = {
        "schema_version": "v1.1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "provider": provider,
        "project": "pm_pal",
    }
    report_data.update(result)
    return report_data


def _check_report_json_valid(report_data: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    required_top_keys = (
        "schema_version",
        "run_id",
        "created_at",
        "model",
        "provider",
        "project",
        "trace",
        "metrics",
    )
    for key in required_top_keys:
        if key not in report_data:
            errors.append(f"missing key: {key}")

    trace = report_data.get("trace")
    if not isinstance(trace, dict):
        errors.append("trace must be an object")

    metrics = report_data.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics must be an object")

    return len(errors) == 0, errors


def _check_trace_complete(trace: Any) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(trace, dict):
        return False, ["trace is not an object"]

    for agent in REQUIRED_TRACE_AGENTS:
        span = trace.get(agent)
        if not isinstance(span, dict):
            errors.append(f"missing agent trace: {agent}")
            continue
        for field in REQUIRED_TRACE_FIELDS:
            if field not in span:
                errors.append(f"{agent}: missing field {field}")
    return len(errors) == 0, errors


def _check_coverage_ratio(
    report_data: dict[str, Any],
) -> tuple[bool, list[str], float | None]:
    errors: list[str] = []
    ratio_value: float | None = None

    metrics = report_data.get("metrics")
    if not isinstance(metrics, dict):
        return False, ["metrics is not an object"], None

    raw_ratio = metrics.get("coverage_ratio")
    if isinstance(raw_ratio, (int, float)):
        ratio_value = float(raw_ratio)
        if ratio_value < 0 or ratio_value > 1:
            errors.append("coverage_ratio must be between 0 and 1")
    else:
        errors.append("coverage_ratio missing or not numeric")

    return len(errors) == 0, errors, ratio_value


def _check_metrics_fields_present(
    report_data: dict[str, Any],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    metrics = report_data.get("metrics")
    if not isinstance(metrics, dict):
        return False, ["metrics is not an object"]

    for field in REQUIRED_METRICS_FIELDS:
        if field not in metrics:
            errors.append(f"metrics missing field: {field}")
    return len(errors) == 0, errors


def _collect_case_runtime_snapshot(report_data: dict[str, Any]) -> dict[str, Any]:
    trace = report_data.get("trace", {})
    runtime_summary = build_runtime_trace_summary(
        trace if isinstance(trace, dict) else {}
    )
    metrics = (
        report_data.get("metrics", {})
        if isinstance(report_data.get("metrics"), dict)
        else {}
    )
    return {
        "total_latency_ms": int(metrics.get("total_latency_ms", 0) or 0),
        "parallel_enabled": bool(metrics.get("parallel_enabled", False)),
        "cache_hit_count": int(metrics.get("cache_hit_count", 0) or 0),
        "cache_miss_count": int(metrics.get("cache_miss_count", 0) or 0),
        "cache_hit_rate": float(metrics.get("cache_hit_rate", 0.0) or 0.0),
        "slowest_span_name": runtime_summary.get("slowest_span_name", ""),
        "slowest_span_duration_ms": int(
            runtime_summary.get("slowest_span_duration_ms", 0) or 0
        ),
        "failed_spans": list(runtime_summary.get("failed_spans", []) or []),
        "completed_primary_spans": list(
            runtime_summary.get("completed_primary_spans", []) or []
        ),
        "cache_backend_usage": dict(
            runtime_summary.get("cache_backend_usage", {}) or {}
        ),
    }


async def _run_case(
    graph: Any,
    case: dict[str, Any],
    runs_dir: Path,
) -> dict[str, Any]:
    case_id = str(case.get("case_id"))
    started_at = datetime.now(timezone.utc)
    started_perf = perf_counter()
    run_id = f"{case_id}_{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = await graph.ainvoke(
            {
                "requirement_doc": _case_to_requirement_doc(case),
                "run_dir": str(run_dir),
            }
        )
        if not isinstance(result, dict):
            raise ValueError("workflow result must be an object")

        report_data = _build_report_json(result, run_id)
        final_report = result.get("final_report", "")
        trace = result.get("trace", {})
        runtime_summary = _collect_case_runtime_snapshot(report_data)

        (run_dir / "report.md").write_text(str(final_report), encoding="utf-8")
        (run_dir / "report.json").write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (run_dir / "run_trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        report_ok, report_errors = _check_report_json_valid(report_data)
        trace_ok, trace_errors = _check_trace_complete(trace)
        coverage_ok, coverage_errors, coverage_ratio = _check_coverage_ratio(
            report_data
        )
        metrics_fields_ok, metrics_fields_errors = _check_metrics_fields_present(
            report_data
        )

        checks = {
            "report_json_valid": {"passed": report_ok, "errors": report_errors},
            "trace_complete": {"passed": trace_ok, "errors": trace_errors},
            "coverage_ratio_present": {
                "passed": coverage_ok,
                "errors": coverage_errors,
            },
            "metrics_fields_present": {
                "passed": metrics_fields_ok,
                "errors": metrics_fields_errors,
            },
        }
        all_ok = all(item["passed"] for item in checks.values())

        return {
            "case_id": case_id,
            "title": case.get("title"),
            "scenario_type": case.get("scenario_type"),
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": "passed" if all_ok else "failed",
            "checks": checks,
            "coverage_ratio": coverage_ratio,
            "runtime_summary": runtime_summary,
            "trace_status": {
                agent: (trace.get(agent, {}) if isinstance(trace, dict) else {}).get(
                    "status"
                )
                for agent in REQUIRED_TRACE_AGENTS
            },
            "case_duration_sec": round(perf_counter() - started_perf, 4),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "case_id": case_id,
            "title": case.get("title"),
            "scenario_type": case.get("scenario_type"),
            "run_id": run_id,
            "run_dir": str(run_dir),
            "status": "error",
            "checks": {
                "report_json_valid": {
                    "passed": False,
                    "errors": ["workflow failed before report validation"],
                },
                "trace_complete": {
                    "passed": False,
                    "errors": ["workflow failed before trace validation"],
                },
                "coverage_ratio_present": {
                    "passed": False,
                    "errors": ["workflow failed before coverage validation"],
                },
                "metrics_fields_present": {
                    "passed": False,
                    "errors": ["workflow failed before metrics validation"],
                },
            },
            "coverage_ratio": None,
            "runtime_summary": {
                "total_latency_ms": 0,
                "parallel_enabled": False,
                "cache_hit_count": 0,
                "cache_miss_count": 0,
                "cache_hit_rate": 0.0,
                "slowest_span_name": "",
                "slowest_span_duration_ms": 0,
                "failed_spans": [],
                "completed_primary_spans": [],
                "cache_backend_usage": {},
            },
            "trace_status": {},
            "error": str(exc),
            "case_duration_sec": round(perf_counter() - started_perf, 4),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }


async def _run_cases(
    graph: Any,
    cases: list[dict[str, Any]],
    runs_dir: Path,
    workers: int,
) -> list[dict[str, Any]]:
    if workers == 1:
        case_results: list[dict[str, Any]] = []
        for idx, case in enumerate(cases, start=1):
            case_id = case.get("case_id", f"case_{idx}")
            print(f"[{idx}/{len(cases)}] Running case: {case_id} (workers={workers})")
            case_result = await _run_case(graph, case, runs_dir)
            case_results.append(case_result)
            print(
                f"  -> {case_result['status']} ({case_result['case_duration_sec']:.4f}s)"
            )
        return case_results

    semaphore = asyncio.Semaphore(workers)
    indexed_results: list[tuple[int, dict[str, Any]]] = []

    async def _run_indexed_case(
        idx: int, case: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        case_id = case.get("case_id", f"case_{idx + 1}")
        async with semaphore:
            print(
                f"[{idx + 1}/{len(cases)}] Running case: {case_id} (workers={workers})"
            )
            case_result = await _run_case(graph, case, runs_dir)
            print(
                f"  -> {case_result['status']} ({case_result['case_duration_sec']:.4f}s)"
            )
            return idx, case_result

    tasks = [
        asyncio.create_task(_run_indexed_case(idx, case))
        for idx, case in enumerate(cases)
    ]
    for task in asyncio.as_completed(tasks):
        indexed_results.append(await task)

    indexed_results.sort(key=lambda item: item[0])
    return [item[1] for item in indexed_results]


def _build_duration_summary(
    case_results: list[dict[str, Any]],
    wall_time_sec: float,
    workers: int,
) -> dict[str, Any]:
    per_case_duration_sec = {
        str(item.get("case_id")): round(float(item.get("case_duration_sec", 0.0)), 4)
        for item in case_results
    }
    sum_case_time_sec = round(sum(per_case_duration_sec.values()), 4)
    avg_case_duration_sec = (
        round(sum_case_time_sec / len(case_results), 4) if case_results else 0.0
    )
    speedup_estimate = (
        round(sum_case_time_sec / wall_time_sec, 4) if wall_time_sec > 0 else 0.0
    )
    slowest_cases = sorted(
        (
            {
                "case_id": str(item.get("case_id")),
                "duration_sec": round(float(item.get("case_duration_sec", 0.0)), 4),
                "status": item.get("status"),
            }
            for item in case_results
        ),
        key=lambda item: item["duration_sec"],
        reverse=True,
    )[:3]
    return {
        "total_duration_sec": round(wall_time_sec, 4),
        "wall_time_sec": round(wall_time_sec, 4),
        "sum_case_time_sec": sum_case_time_sec,
        "avg_case_duration_sec": avg_case_duration_sec,
        "per_case_duration_sec": per_case_duration_sec,
        "slowest_cases_top_3": slowest_cases,
        "workers": workers,
        "parallel_enabled": workers > 1,
        "speedup_estimate": speedup_estimate,
    }


def _build_runtime_summary(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    total_latency_ms = sum(
        int(item.get("runtime_summary", {}).get("total_latency_ms", 0) or 0)
        for item in case_results
    )
    cache_hits = sum(
        int(item.get("runtime_summary", {}).get("cache_hit_count", 0) or 0)
        for item in case_results
    )
    cache_misses = sum(
        int(item.get("runtime_summary", {}).get("cache_miss_count", 0) or 0)
        for item in case_results
    )
    cache_total = cache_hits + cache_misses
    backend_usage: dict[str, int] = {}
    slowest_case = {
        "case_id": "",
        "slowest_span_name": "",
        "slowest_span_duration_ms": 0,
    }
    cases_with_failed_spans: list[dict[str, Any]] = []

    for item in case_results:
        runtime_summary = (
            item.get("runtime_summary", {})
            if isinstance(item.get("runtime_summary"), dict)
            else {}
        )
        for backend_name, count in dict(
            runtime_summary.get("cache_backend_usage", {}) or {}
        ).items():
            backend_usage[str(backend_name)] = backend_usage.get(
                str(backend_name), 0
            ) + int(count or 0)
        slowest_span_duration_ms = int(
            runtime_summary.get("slowest_span_duration_ms", 0) or 0
        )
        if slowest_span_duration_ms > slowest_case["slowest_span_duration_ms"]:
            slowest_case = {
                "case_id": str(item.get("case_id", "") or ""),
                "slowest_span_name": str(
                    runtime_summary.get("slowest_span_name", "") or ""
                ),
                "slowest_span_duration_ms": slowest_span_duration_ms,
            }
        failed_spans = list(runtime_summary.get("failed_spans", []) or [])
        if failed_spans:
            cases_with_failed_spans.append(
                {
                    "case_id": str(item.get("case_id", "") or ""),
                    "failed_spans": failed_spans,
                }
            )

    return {
        "aggregate_total_latency_ms": total_latency_ms,
        "aggregate_cache_hit_count": cache_hits,
        "aggregate_cache_miss_count": cache_misses,
        "aggregate_cache_hit_rate": round(cache_hits / cache_total, 4)
        if cache_total
        else 0.0,
        "cache_backend_usage": backend_usage,
        "slowest_case_span": slowest_case,
        "cases_with_failed_spans": cases_with_failed_spans,
    }


def _print_summary(
    profile: str,
    workers: int,
    cases_file: Path,
    summary: dict[str, int],
    duration_summary: dict[str, Any],
    runtime_summary: dict[str, Any],
    out_path: Path,
) -> None:
    print("")
    print("Eval summary")
    print(f"  profile: {profile}")
    print(f"  workers: {workers}")
    print(f"  parallel_enabled: {duration_summary['parallel_enabled']}")
    print(f"  cases_file: {cases_file}")
    print(
        "  results: "
        f"{summary['passed_cases']} passed, "
        f"{summary['failed_cases']} failed, "
        f"{summary['error_cases']} error, "
        f"{summary['total_cases']} total"
    )
    print(
        "  timing: "
        f"wall={duration_summary['wall_time_sec']:.4f}s, "
        f"sum_case={duration_summary['sum_case_time_sec']:.4f}s, "
        f"avg_case={duration_summary['avg_case_duration_sec']:.4f}s, "
        f"speedup_estimate={duration_summary['speedup_estimate']:.4f}x"
    )
    print(
        "  runtime: "
        f"aggregate_total_latency_ms={runtime_summary['aggregate_total_latency_ms']}, "
        f"cache_hit_rate={runtime_summary['aggregate_cache_hit_rate']:.4f}, "
        f"slowest_case_span={runtime_summary['slowest_case_span']['case_id']}::"
        f"{runtime_summary['slowest_case_span']['slowest_span_name']}"
    )
    if duration_summary["slowest_cases_top_3"]:
        print("  slowest cases:")
        for item in duration_summary["slowest_cases_top_3"]:
            print(
                f"    - {item['case_id']}: {item['duration_sec']:.4f}s ({item['status']})"
            )
    print(f"  report: {out_path}")


async def _amain() -> int:
    load_dotenv()
    args = _parse_args()

    all_cases = _load_cases(args.cases)
    cases = _select_cases(all_cases, args.profile)
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    graph = build_review_graph()
    eval_started_at = datetime.now(timezone.utc)
    eval_started_perf = perf_counter()
    case_results = await _run_cases(graph, cases, args.runs_dir, args.workers)
    wall_time_sec = perf_counter() - eval_started_perf

    total = len(case_results)
    passed = sum(1 for item in case_results if item.get("status") == "passed")
    failed = sum(1 for item in case_results if item.get("status") == "failed")
    errored = sum(1 for item in case_results if item.get("status") == "error")
    summary = {
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": failed,
        "error_cases": errored,
    }
    duration_summary = _build_duration_summary(
        case_results, wall_time_sec, args.workers
    )
    runtime_summary = _build_runtime_summary(case_results)

    eval_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": eval_started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": str(args.cases),
        "profile": args.profile,
        "workers": args.workers,
        "parallel_enabled": args.workers > 1,
        "selected_case_ids": [str(case.get("case_id")) for case in cases],
        "available_case_ids": [str(case.get("case_id")) for case in all_cases],
        "runs_dir": str(args.runs_dir),
        "summary": summary,
        "duration_summary": duration_summary,
        "runtime_summary": runtime_summary,
        "cases": case_results,
    }

    args.out.write_text(
        json.dumps(eval_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print_summary(
        args.profile,
        args.workers,
        args.cases,
        summary,
        duration_summary,
        runtime_summary,
        args.out,
    )
    return 0 if (failed == 0 and errored == 0) else 1


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
