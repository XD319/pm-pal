"""Run a minimal A/B comparison for single_review vs parallel_review.

Usage:
    python eval/compare_review_modes.py
    python eval/compare_review_modes.py --case-id prd_case_08 --case-id prd_case_11
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pm_pal.service import review_service

DEFAULT_CASE_IDS = ("prd_case_08", "prd_case_11")
MODES = ("single_review", "parallel_review")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare single_review and parallel_review outputs."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("eval/cases/prd_test_inputs.jsonl"),
        help="Path to the PRD case JSONL file.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Case id to include. Repeat to select multiple cases. Defaults to a minimal and a high-risk case.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("eval/review_mode_comparison.json"),
        help="Path to write the comparison report JSON.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("eval/review_mode_comparison_runs"),
        help="Directory to store comparison run artifacts.",
    )
    return parser.parse_args()


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
        case_id = str(item.get("case_id", "") or "").strip()
        if not case_id:
            raise ValueError(f"Missing case_id at line {index}")
        cases.append(item)
    return cases


def _select_cases(
    cases: list[dict[str, Any]], requested_case_ids: list[str] | None
) -> list[dict[str, Any]]:
    case_ids = requested_case_ids or list(DEFAULT_CASE_IDS)
    unique_case_ids = list(
        dict.fromkeys(
            str(case_id).strip() for case_id in case_ids if str(case_id).strip()
        )
    )
    if len(unique_case_ids) < 2:
        raise ValueError("At least two case ids are required for comparison.")

    case_lookup = {str(case.get("case_id")): case for case in cases}
    missing = [case_id for case_id in unique_case_ids if case_id not in case_lookup]
    if missing:
        raise ValueError(f"Unknown case ids: {', '.join(missing)}")

    return [case_lookup[case_id] for case_id in unique_case_ids]


def _case_to_requirement_doc(case: dict[str, Any]) -> str:
    return json.dumps(case, ensure_ascii=False, indent=2)


def _load_json_object(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _extract_duration_ms(report_payload: dict[str, Any], wall_time_ms: int) -> int:
    legacy_meta = report_payload.get("parallel-review_meta")
    modern_meta = report_payload.get("parallel_review_meta")
    for meta in (legacy_meta, modern_meta):
        if isinstance(meta, dict):
            raw_duration = meta.get("duration_ms")
            if isinstance(raw_duration, (int, float)):
                return int(raw_duration)

    metrics = report_payload.get("metrics")
    if isinstance(metrics, dict):
        total_latency = metrics.get("total_latency_ms")
        if isinstance(total_latency, (int, float)):
            return int(total_latency)

    return int(wall_time_ms)


def _summarize_run(
    summary: review_service.ReviewResultSummary, wall_time_ms: int
) -> dict[str, Any]:
    review_payload = review_service._build_review_requirement_payload(summary)
    report_payload = _load_json_object(summary.report_json_path)
    return {
        "run_id": summary.run_id,
        "status": summary.status,
        "review_mode": review_payload["review_mode"],
        "findings_count": len(review_payload["findings"]),
        "open_questions_count": len(review_payload["open_questions"]),
        "risk_items_count": len(review_payload["risk_items"]),
        "conflicts_count": len(review_payload["conflicts"]),
        "duration_ms": _extract_duration_ms(report_payload, wall_time_ms),
        "wall_time_ms": int(wall_time_ms),
        "report_json_path": summary.report_json_path,
        "report_path": review_payload["report_path"],
        "trace_path": summary.run_trace_path,
        "token_usage": "not available",
    }


def _build_delta(
    single_mode: dict[str, Any], parallel_mode: dict[str, Any]
) -> dict[str, int]:
    comparable_fields = (
        "findings_count",
        "open_questions_count",
        "risk_items_count",
        "conflicts_count",
        "duration_ms",
    )
    return {
        field: int(parallel_mode[field]) - int(single_mode[field])
        for field in comparable_fields
    }


def run_comparison(
    *,
    cases_path: Path,
    case_ids: list[str] | None,
    runs_dir: Path,
    report_path: Path,
) -> dict[str, Any]:
    selected_cases = _select_cases(_load_cases(cases_path), case_ids)
    runs_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for case in selected_cases:
        case_id = str(case["case_id"])
        complexity = str(case.get("scenario_type", "unknown") or "unknown")
        title = str(case.get("title", "") or "")
        prd_text = _case_to_requirement_doc(case)

        case_row: dict[str, Any] = {
            "case_id": case_id,
            "complexity": complexity,
            "title": title,
            "modes": {},
        }

        print(f"Case: {case_id} ({complexity})")
        for mode in MODES:
            started = perf_counter()
            summary = review_service.review_prd_text(
                prd_text=prd_text,
                config_overrides={
                    "outputs_root": runs_dir / case_id / mode,
                    "review_mode_override": mode,
                },
            )
            wall_time_ms = round((perf_counter() - started) * 1000)
            metrics = _summarize_run(summary, wall_time_ms)
            case_row["modes"][mode] = metrics
            print(
                f"  - {mode}: findings={metrics['findings_count']}, "
                f"open_questions={metrics['open_questions_count']}, "
                f"risk_items={metrics['risk_items_count']}, "
                f"conflicts={metrics['conflicts_count']}, "
                f"duration_ms={metrics['duration_ms']}"
            )

        case_row["delta_parallel_minus_single"] = _build_delta(
            case_row["modes"]["single_review"],
            case_row["modes"]["parallel_review"],
        )
        rows.append(case_row)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases_file": str(cases_path),
        "selected_case_ids": [str(case["case_id"]) for case in selected_cases],
        "token_usage": "not available",
        "cases": rows,
    }
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> None:
    load_dotenv()
    args = _parse_args()
    payload = run_comparison(
        cases_path=args.cases,
        case_ids=args.case_ids,
        runs_dir=args.runs_dir,
        report_path=args.out,
    )
    print(f"Saved comparison report to {args.out}")
    print(f"Compared {len(payload['cases'])} cases across {len(MODES)} review modes.")


if __name__ == "__main__":
    main()
