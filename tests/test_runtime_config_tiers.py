"""Runtime Config single-model resolution and PM_PAL_* dual-read."""

from __future__ import annotations

from pm_pal.runtime.config.config import Config, runtime_config_overrides
from pm_pal.runtime.llm_provider.generic.base import (
    CORE_PROVIDERS,
    EXPERIMENTAL_PROVIDERS,
    SUPPORTED_PROVIDERS,
    provider_tier,
)


def test_resolve_single_llm(monkeypatch):
    monkeypatch.setenv("PM_PAL_LLM", "openai:gpt-single")
    for key in (
        "LLM",
        "SMART_LLM",
        "FAST_LLM",
        "STRATEGIC_LLM",
        "PM_PAL_SMART_LLM",
        "PM_PAL_FAST_LLM",
        "PM_PAL_STRATEGIC_LLM",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = Config()
    assert cfg.llm == "openai:gpt-single"
    assert cfg.resolve_llm() == ("openai", "gpt-single")
    assert cfg.token_limit == 6000


def test_legacy_smart_llm_fallback(monkeypatch):
    monkeypatch.delenv("PM_PAL_LLM", raising=False)
    monkeypatch.delenv("LLM", raising=False)
    monkeypatch.setenv("SMART_LLM", "deepseek:deepseek-chat")

    cfg = Config()
    assert cfg.llm == "deepseek:deepseek-chat"


def test_pm_pal_llm_preferred_over_legacy(monkeypatch):
    monkeypatch.setenv("PM_PAL_LLM", "deepseek:deepseek-chat")
    monkeypatch.setenv("SMART_LLM", "openai:gpt-5-nano")

    cfg = Config()
    assert cfg.llm == "deepseek:deepseek-chat"


def test_runtime_overrides_still_win(monkeypatch):
    monkeypatch.setenv("PM_PAL_LLM", "openai:gpt-from-env")
    with runtime_config_overrides({"SMART_LLM": "openai:gpt-override"}):
        cfg = Config()
        assert cfg.llm == "openai:gpt-override"
    assert Config().llm == "openai:gpt-from-env"


def test_provider_tier_partition():
    assert provider_tier("openai") == "core"
    assert provider_tier("gigachat") == "experimental"
    assert CORE_PROVIDERS.isdisjoint(EXPERIMENTAL_PROVIDERS)
    assert SUPPORTED_PROVIDERS == CORE_PROVIDERS | EXPERIMENTAL_PROVIDERS
