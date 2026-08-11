from __future__ import annotations

import pytest

from pm_pal.service import review_service
from pm_pal.runtime.config.config import Config


@pytest.mark.asyncio
async def test_review_prd_text_async_applies_llm_runtime_overrides_without_leaking(
    tmp_path, monkeypatch, sample_prd_text: str
):
    observed: dict[str, object] = {}
    for key in (
        "PM_PAL_LLM",
        "LLM",
        "PM_PAL_SMART_LLM",
        "SMART_LLM",
        "PM_PAL_TEMPERATURE",
        "TEMPERATURE",
        "PM_PAL_LLM_KWARGS",
        "LLM_KWARGS",
    ):
        monkeypatch.delenv(key, raising=False)

    async def fake_run_review(requirement_doc: str, **kwargs):
        cfg = Config()
        observed["requirement_doc"] = requirement_doc
        observed["llm"] = cfg.llm
        observed["temperature"] = cfg.temperature
        observed["llm_kwargs"] = cfg.llm_kwargs
        return {
            "run_id": "20260311T000000Z",
            "run_dir": str(tmp_path / "20260311T000000Z"),
            "result": {
                "metrics": {"coverage_ratio": 0.0},
                "trace": {},
                "high_risk_ratio": 0.0,
                "revision_round": 0,
            },
            "report_paths": {
                "report_md": str(tmp_path / "20260311T000000Z" / "report.md"),
                "report_json": str(tmp_path / "20260311T000000Z" / "report.json"),
                "run_trace": str(tmp_path / "20260311T000000Z" / "run_trace.json"),
            },
        }

    monkeypatch.setattr(review_service, "run_review", fake_run_review)
    monkeypatch.setattr(
        review_service, "build_delivery_handoff_outputs", lambda *args, **kwargs: {}
    )

    summary = await review_service.review_prd_text_async(
        prd_text=sample_prd_text,
        config_overrides={
            "outputs_root": str(tmp_path),
            "llm": "deepseek:deepseek-chat",
            "temperature": 0.1,
            "llm_kwargs": {"max_retries": 1},
        },
    )

    assert summary.run_id == "20260311T000000Z"
    assert observed == {
        "requirement_doc": sample_prd_text,
        "llm": "deepseek:deepseek-chat",
        "temperature": 0.1,
        "llm_kwargs": {"max_retries": 1},
    }

    cfg = Config()
    assert cfg.llm == "openai:gpt-5-nano"
    assert cfg.temperature == 0.2
