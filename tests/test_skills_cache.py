from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict

from pm_pal.skills.executor import SkillExecutor, SkillSpec


class SampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


class SampleOutput(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def clear_skill_cache():
    SkillExecutor.clear_cache()
    yield
    SkillExecutor.clear_cache()


def build_spec(*, handler: MagicMock, ttl_sec: int) -> SkillSpec:
    return SkillSpec(
        name="tests.sample_skill",
        input_model=SampleInput,
        output_model=SampleOutput,
        handler=handler,
        config_version="tests.sample_skill@v1",
        cache_ttl_sec=ttl_sec,
    )


@pytest.mark.asyncio
async def test_cache_disabled_calls_handler_on_every_execute():
    handler = MagicMock(side_effect=[{"value": "first"}, {"value": "second"}])
    spec = build_spec(handler=handler, ttl_sec=60)
    executor = SkillExecutor(cache_enabled=False)
    trace_first: dict = {}
    trace_second: dict = {}

    first = await executor.execute(spec, {"query": "same"}, trace=trace_first)
    second = await executor.execute(spec, {"query": "same"}, trace=trace_second)

    assert first.value == "first"
    assert second.value == "second"
    assert handler.call_count == 2
    assert trace_first["tests.sample_skill"]["cache_hit"] is False
    assert trace_second["tests.sample_skill"]["cache_hit"] is False


@pytest.mark.asyncio
async def test_cache_enabled_hits_cache_and_reports_cache_hit_to_trace_callback():
    handler = MagicMock(return_value={"value": "cached"})
    spec = build_spec(handler=handler, ttl_sec=60)
    executor = SkillExecutor(cache_enabled=True)
    trace = MagicMock()

    first = await executor.execute(spec, {"query": "same"}, trace=trace)
    second = await executor.execute(spec, {"query": "same"}, trace=trace)

    assert first.value == "cached"
    assert second.value == "cached"
    handler.assert_called_once()
    assert trace.call_count == 2
    first_name, first_trace = trace.call_args_list[0].args
    second_name, second_trace = trace.call_args_list[1].args
    assert first_name == "tests.sample_skill"
    assert second_name == "tests.sample_skill"
    assert first_trace["cache_hit"] is False
    assert second_trace["cache_hit"] is True
    assert second_trace["ttl_sec"] == 60
    assert second_trace["cache_backend"] == "memory"
    assert second_trace["cache_backend_target"] == "process-local"
    assert second_trace["cache_lookup_status"] == "hit"


@pytest.mark.asyncio
async def test_ttl_expiry_reinvokes_handler_after_sleep():
    handler = MagicMock(side_effect=[{"value": "first"}, {"value": "second"}])
    spec = build_spec(handler=handler, ttl_sec=1)
    executor = SkillExecutor(cache_enabled=True)
    trace_first: dict = {}
    trace_second: dict = {}

    first = await executor.execute(spec, {"query": "same"}, trace=trace_first)
    time.sleep(1.1)
    second = await executor.execute(spec, {"query": "same"}, trace=trace_second)

    assert first.value == "first"
    assert second.value == "second"
    assert handler.call_count == 2
    assert trace_first["tests.sample_skill"]["cache_hit"] is False
    assert trace_second["tests.sample_skill"]["cache_hit"] is False


@pytest.mark.asyncio
async def test_cache_key_diff_payload_does_not_hit():
    handler = MagicMock(side_effect=[{"value": "alpha"}, {"value": "beta"}])
    spec = build_spec(handler=handler, ttl_sec=60)
    executor = SkillExecutor(cache_enabled=True)
    trace_first: dict = {}
    trace_second: dict = {}

    first = await executor.execute(spec, {"query": "alpha"}, trace=trace_first)
    second = await executor.execute(spec, {"query": "beta"}, trace=trace_second)

    assert first.value == "alpha"
    assert second.value == "beta"
    assert handler.call_count == 2
    assert trace_first["tests.sample_skill"]["cache_hit"] is False
    assert trace_second["tests.sample_skill"]["cache_hit"] is False
    assert (
        trace_first["tests.sample_skill"]["cache_key_hash"]
        != trace_second["tests.sample_skill"]["cache_key_hash"]
    )


@pytest.mark.asyncio
async def test_memory_cache_trace_exposes_debug_metadata():
    handler = MagicMock(return_value={"value": "cached"})
    spec = build_spec(handler=handler, ttl_sec=60)
    executor = SkillExecutor(cache_enabled=True)
    trace: dict = {}

    await executor.execute(spec, {"query": "same"}, trace=trace)

    skill_trace = trace["tests.sample_skill"]
    assert skill_trace["cache_hit"] is False
    assert skill_trace["cache_backend"] == "memory"
    assert skill_trace["cache_backend_target"] == "process-local"
    assert skill_trace["cache_lookup_status"] == "miss"


@pytest.mark.asyncio
async def test_sqlite_cache_reuses_results_across_executor_instances(tmp_path):
    cache_path = tmp_path / "skills-cache.sqlite3"
    handler = MagicMock(return_value={"value": "persisted"})
    spec = build_spec(handler=handler, ttl_sec=60)
    first_executor = SkillExecutor(
        cache_enabled=True,
        cache_backend="sqlite",
        cache_backend_path=str(cache_path),
    )
    second_executor = SkillExecutor(
        cache_enabled=True,
        cache_backend="sqlite",
        cache_backend_path=str(cache_path),
    )
    trace_first: dict = {}
    trace_second: dict = {}

    first = await first_executor.execute(spec, {"query": "same"}, trace=trace_first)
    second = await second_executor.execute(spec, {"query": "same"}, trace=trace_second)

    assert first.value == "persisted"
    assert second.value == "persisted"
    handler.assert_called_once()
    assert trace_first["tests.sample_skill"]["cache_hit"] is False
    assert trace_first["tests.sample_skill"]["cache_backend"] == "sqlite"
    assert trace_first["tests.sample_skill"]["cache_lookup_status"] == "miss"
    assert trace_second["tests.sample_skill"]["cache_hit"] is True
    assert trace_second["tests.sample_skill"]["cache_backend"] == "sqlite"
    assert trace_second["tests.sample_skill"]["cache_backend_target"] == str(
        cache_path.resolve()
    )
    assert trace_second["tests.sample_skill"]["cache_lookup_status"] == "hit"


@pytest.mark.asyncio
async def test_sqlite_cache_expiry_reinvokes_handler(tmp_path):
    cache_path = tmp_path / "skills-cache.sqlite3"
    handler = MagicMock(side_effect=[{"value": "first"}, {"value": "second"}])
    spec = build_spec(handler=handler, ttl_sec=1)
    first_executor = SkillExecutor(
        cache_enabled=True,
        cache_backend="sqlite",
        cache_backend_path=str(cache_path),
    )
    second_executor = SkillExecutor(
        cache_enabled=True,
        cache_backend="sqlite",
        cache_backend_path=str(cache_path),
    )
    trace_first: dict = {}
    trace_second: dict = {}

    first = await first_executor.execute(spec, {"query": "same"}, trace=trace_first)
    time.sleep(1.1)
    second = await second_executor.execute(spec, {"query": "same"}, trace=trace_second)

    assert first.value == "first"
    assert second.value == "second"
    assert handler.call_count == 2
    assert trace_first["tests.sample_skill"]["cache_lookup_status"] == "miss"
    assert trace_second["tests.sample_skill"]["cache_hit"] is False
    assert trace_second["tests.sample_skill"]["cache_backend"] == "sqlite"
    assert trace_second["tests.sample_skill"]["cache_lookup_status"] == "expired"
