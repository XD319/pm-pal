"""Compare single-review and parallel-review outputs on representative PRDs.

Usage:
    python eval/parallel_review_compare.py
"""

from __future__ import annotations

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

from pm_pal.service.review_service import review_prd_text

CASES = [
    {
        "case_id": "simple_login",
        "complexity": "low",
        "prd": """
# Recruiter login

Allow recruiters to sign in with email and password.

## Scenarios
- Recruiter submits valid credentials and reaches the dashboard.

## Acceptance Criteria
- Valid credentials reach the dashboard.
- Invalid credentials show an inline error.
""",
    },
    {
        "case_id": "team_workflow",
        "complexity": "medium",
        "prd": """
# Interview scheduling

Support recruiter, interviewer, and candidate coordination across FE, BE, and QA.

## Scenarios
- Recruiter proposes interview slots.
- Candidate confirms one slot and notifications are sent.

## Acceptance Criteria
- Calendar conflicts are blocked.
- Notification failures are retried.
""",
    },
    {
        "case_id": "cross_system_export",
        "complexity": "high",
        "prd": """
# Cross-system export and billing sync

Allow admin users to export recruiter profiles and sync billing status across FE, BE, QA, DevOps, and Security.

## Modules
- `admin-portal`
- `billing-api`
- `audit-service`
- `finance-worker`

## Scenarios
- Admin exports recruiter profiles.
- The backend retries an external webhook when the downstream finance platform is unavailable.

## Acceptance Criteria
- Export actions produce audit records.
- Rollback guidance exists for failed exports.
- Billing sync preserves idempotent writes.
""",
    },
]
MODES = ("single_review", "parallel_review")
OUTPUT_ROOT = Path("eval/parallel_review_compare_runs")
REPORT_PATH = Path("eval/parallel_review_compare.json")


def _load_report(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _extract_stats(report_payload: dict[str, Any]) -> dict[str, Any]:
    meta = (
        report_payload.get("parallel-review_meta")
        if isinstance(report_payload.get("parallel-review_meta"), dict)
        else {}
    )
    return {
        "selected_mode": meta.get(
            "selected_mode", report_payload.get("review_mode", "unknown")
        ),
        "open_questions_count": int(meta.get("open_questions_count", 0) or 0),
        "risk_items_count": int(meta.get("risk_items_count", 0) or 0),
        "input_token_estimate": int(meta.get("input_token_estimate", 0) or 0),
        "output_token_estimate": int(meta.get("output_token_estimate", 0) or 0),
        "review_duration_ms": int(meta.get("duration_ms", 0) or 0),
        "reviewer_count": int(meta.get("reviewer_count", 0) or 0),
    }


def main() -> None:
    load_dotenv()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for case in CASES:
        print(f"Case: {case['case_id']} ({case['complexity']})")
        case_row = {
            "case_id": case["case_id"],
            "complexity": case["complexity"],
            "modes": {},
        }
        for mode in MODES:
            started = perf_counter()
            summary = review_prd_text(
                prd_text=case["prd"],
                config_overrides={
                    "outputs_root": OUTPUT_ROOT / case["case_id"] / mode,
                    "review_mode_override": mode,
                },
            )
            wall_time_ms = round((perf_counter() - started) * 1000)
            report_payload = _load_report(summary.report_json_path)
            stats = _extract_stats(report_payload)
            stats.update(
                {
                    "run_id": summary.run_id,
                    "status": summary.status,
                    "report_json_path": summary.report_json_path,
                    "trace_path": summary.run_trace_path,
                    "wall_time_ms": wall_time_ms,
                }
            )
            case_row["modes"][mode] = stats
            print(
                f"  - {mode}: open_questions={stats['open_questions_count']}, "
                f"risk_items={stats['risk_items_count']}, tokens_in={stats['input_token_estimate']}, "
                f"tokens_out={stats['output_token_estimate']}, wall_time_ms={wall_time_ms}"
            )
        rows.append(case_row)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": rows,
    }
    REPORT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved comparison report to {REPORT_PATH}")


if __name__ == "__main__":
    main()
