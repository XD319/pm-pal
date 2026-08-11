from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from pm_pal.agents.structured_runner import run_structured_node
from pm_pal.utils.llm_structured_call import StructuredCallError
from pm_pal.utils.trace import trace_start


class DemoOutput(BaseModel):
    items: list[str]


def _validate_demo_output(payload: dict) -> DemoOutput:
    return DemoOutput.model_validate(payload)


def _empty_demo_output() -> dict:
    return {"items": []}


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    monkeypatch.setattr(
        "pm_pal.agents.structured_runner.Config",
        lambda: SimpleNamespace(resolve_llm=lambda: ("mock-provider", "mock-model")),
    )


@pytest.mark.asyncio
async def test_run_structured_node_success(monkeypatch, tmp_path):
    async def fake_llm_structured_call(*, prompt, schema, metadata):
        metadata["structured_mode"] = "fallback"
        metadata["raw_output"] = '{"items":["alpha"]}'
        return {"items": ["alpha"]}

    monkeypatch.setattr(
        "pm_pal.agents.structured_runner.llm_structured_call",
        fake_llm_structured_call,
    )

    trace: dict = {}
    result = await run_structured_node(
        agent_name="demo",
        prompt="return one item",
        schema=DemoOutput,
        validate_output=_validate_demo_output,
        empty_output=_empty_demo_output,
        trace=trace,
        run_dir=str(tmp_path),
        span=trace_start("demo", input_chars=15),
    )

    assert result.status == "ok"
    assert result.output == {"items": ["alpha"]}
    assert result.model == "mock-model"
    assert result.structured_mode == "fallback"
    assert trace["demo"]["status"] == "ok"
    assert trace["demo"]["model"] == "mock-model"


@pytest.mark.asyncio
async def test_run_structured_node_schema_failure_saves_raw_output(
    monkeypatch, tmp_path
):
    async def fake_llm_structured_call(*, prompt, schema, metadata):
        metadata["structured_mode"] = "fallback"
        metadata["raw_output"] = '{"items":"not-a-list"}'
        return {"items": "not-a-list"}

    monkeypatch.setattr(
        "pm_pal.agents.structured_runner.llm_structured_call",
        fake_llm_structured_call,
    )

    trace: dict = {}
    result = await run_structured_node(
        agent_name="demo",
        prompt="return one item",
        schema=DemoOutput,
        validate_output=_validate_demo_output,
        empty_output=_empty_demo_output,
        trace=trace,
        run_dir=str(tmp_path),
        span=trace_start("demo", input_chars=15),
    )

    assert result.status == "error"
    assert result.output == {"items": []}
    assert result.raw_output_path
    assert "schema validation failed" in result.error_message
    assert trace["demo"]["status"] == "error"
    assert "not-a-list" in (tmp_path / "raw_agent_outputs" / "demo.txt").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_run_structured_node_llm_failure_degrades_to_empty_output(
    monkeypatch, tmp_path
):
    async def fake_llm_structured_call(*, prompt, schema, metadata):
        raise StructuredCallError(
            "structured call failed: invalid json",
            raw_output="not json",
            structured_mode="fallback",
        )

    monkeypatch.setattr(
        "pm_pal.agents.structured_runner.llm_structured_call",
        fake_llm_structured_call,
    )

    trace: dict = {}
    result = await run_structured_node(
        agent_name="demo",
        prompt="return one item",
        schema=DemoOutput,
        validate_output=_validate_demo_output,
        empty_output=_empty_demo_output,
        trace=trace,
        run_dir=str(tmp_path),
        span=trace_start("demo", input_chars=15),
    )

    assert result.status == "error"
    assert result.output == {"items": []}
    assert result.raw_output == "not json"
    assert result.structured_mode == "fallback"
    assert trace["demo"]["status"] == "error"
    assert "invalid json" in trace["demo"]["error_message"]
    assert (tmp_path / "raw_agent_outputs" / "demo.txt").read_text(
        encoding="utf-8"
    ) == "not json"
