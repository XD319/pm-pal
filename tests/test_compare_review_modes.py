from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pm_pal.service.review_service import ReviewResultSummary

MODULE_PATH = Path(__file__).resolve().parents[1] / "eval" / "compare_review_modes.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("compare_review_modes", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load compare_review_modes module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_select_cases_requires_at_least_two_case_ids():
    module = _load_module()
    with pytest.raises(ValueError, match="At least two case ids"):
        module._select_cases([{"case_id": "prd_case_08"}], ["prd_case_08"])


def test_run_comparison_writes_ab_metrics(tmp_path, monkeypatch):
    module = _load_module()

    cases_path = tmp_path / "cases.jsonl"
    cases = [
        {
            "case_id": "prd_case_08",
            "scenario_type": "minimal",
            "title": "Password Reset",
        },
        {
            "case_id": "prd_case_11",
            "scenario_type": "high_risk",
            "title": "Autonomous Campus Hiring Copilot",
        },
    ]
    cases_path.write_text(
        "\n".join(json.dumps(item) for item in cases) + "\n", encoding="utf-8"
    )

    metrics_map = {
        ("prd_case_08", "single_review"): {
            "findings": 1,
            "questions": 2,
            "risks": 1,
            "conflicts": 0,
            "duration": 120,
        },
        ("prd_case_08", "parallel_review"): {
            "findings": 3,
            "questions": 4,
            "risks": 2,
            "conflicts": 1,
            "duration": 240,
        },
        ("prd_case_11", "single_review"): {
            "findings": 2,
            "questions": 3,
            "risks": 2,
            "conflicts": 0,
            "duration": 180,
        },
        ("prd_case_11", "parallel_review"): {
            "findings": 5,
            "questions": 6,
            "risks": 4,
            "conflicts": 2,
            "duration": 360,
        },
    }

    def fake_review_prd_text(*, prd_text, config_overrides):
        case = json.loads(prd_text)
        case_id = case["case_id"]
        mode = config_overrides["review_mode_override"]
        run_dir = Path(config_overrides["outputs_root"])
        run_dir.mkdir(parents=True, exist_ok=True)
        report_json_path = run_dir / "report.json"
        trace_path = run_dir / "run_trace.json"
        duration = metrics_map[(case_id, mode)]["duration"]
        report_json_path.write_text(
            json.dumps(
                {
                    "parallel-review_meta": {
                        "selected_mode": mode,
                        "review_mode": mode,
                        "duration_ms": duration,
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        trace_path.write_text("{}", encoding="utf-8")
        return ReviewResultSummary(
            run_id=f"{case_id}_{mode}",
            report_md_path=str(run_dir / "report.md"),
            report_json_path=str(report_json_path),
            high_risk_ratio=0.0,
            coverage_ratio=0.0,
            revision_round=0,
            status="completed",
            run_trace_path=str(trace_path),
        )

    def fake_build_review_requirement_payload(summary):
        report_path = Path(summary.report_json_path)
        mode = report_path.parent.name
        case_id = report_path.parent.parent.name
        metric = metrics_map[(case_id, mode)]
        return {
            "review_id": summary.run_id,
            "run_id": summary.run_id,
            "findings": [{} for _ in range(metric["findings"])],
            "open_questions": [{} for _ in range(metric["questions"])],
            "risk_items": [{} for _ in range(metric["risks"])],
            "conflicts": [{} for _ in range(metric["conflicts"])],
            "report_path": summary.report_json_path,
            "review_mode": mode,
        }

    monkeypatch.setattr(module.review_service, "review_prd_text", fake_review_prd_text)
    monkeypatch.setattr(
        module.review_service,
        "_build_review_requirement_payload",
        fake_build_review_requirement_payload,
    )

    report_path = tmp_path / "comparison.json"
    payload = module.run_comparison(
        cases_path=cases_path,
        case_ids=None,
        runs_dir=tmp_path / "runs",
        report_path=report_path,
    )

    assert payload["selected_case_ids"] == ["prd_case_08", "prd_case_11"]
    assert payload["token_usage"] == "not available"
    assert payload["cases"][0]["modes"]["single_review"]["findings_count"] == 1
    assert payload["cases"][0]["modes"]["parallel_review"]["conflicts_count"] == 1
    assert payload["cases"][1]["modes"]["parallel_review"]["risk_items_count"] == 4
    assert payload["cases"][1]["modes"]["parallel_review"]["duration_ms"] == 360
    assert payload["cases"][1]["delta_parallel_minus_single"]["findings_count"] == 3

    saved_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_payload == payload
