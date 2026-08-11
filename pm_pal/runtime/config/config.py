"""Minimal runtime configuration for the requirement review system."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

from pm_pal.runtime.llm_provider.generic.base import (
    ReasoningEfforts,
    SUPPORTED_PROVIDERS,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "LLM": "openai:gpt-5-nano",
    "TOKEN_LIMIT": 6000,
    "TEMPERATURE": 0.2,
    "LLM_KWARGS": {},
    "REASONING_EFFORT": "medium",
}

_MISSING = object()
_RUNTIME_CONFIG_OVERRIDES: ContextVar[dict[str, Any]] = ContextVar(
    "pm_pal.runtime_config_overrides",
    default={},
)

# Dual-read: prefer PM_PAL_* ; legacy tier keys still accepted :-)
_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "LLM": (
        "PM_PAL_LLM",
        "LLM",
        "PM_PAL_SMART_LLM",
        "SMART_LLM",
        "PM_PAL_FAST_LLM",
        "FAST_LLM",
        "PM_PAL_STRATEGIC_LLM",
        "STRATEGIC_LLM",
    ),
    "TOKEN_LIMIT": (
        "PM_PAL_TOKEN_LIMIT",
        "TOKEN_LIMIT",
        "PM_PAL_SMART_TOKEN_LIMIT",
        "SMART_TOKEN_LIMIT",
        "PM_PAL_FAST_TOKEN_LIMIT",
        "FAST_TOKEN_LIMIT",
        "PM_PAL_STRATEGIC_TOKEN_LIMIT",
        "STRATEGIC_TOKEN_LIMIT",
    ),
    "TEMPERATURE": ("PM_PAL_TEMPERATURE", "TEMPERATURE"),
    "LLM_KWARGS": ("PM_PAL_LLM_KWARGS", "LLM_KWARGS"),
    "REASONING_EFFORT": ("PM_PAL_REASONING_EFFORT", "REASONING_EFFORT"),
}

_LLM_OVERRIDE_KEYS = ("LLM", "SMART_LLM", "FAST_LLM", "STRATEGIC_LLM")


@contextmanager
def runtime_config_overrides(overrides: dict[str, Any] | None = None) -> Iterator[None]:
    current = _RUNTIME_CONFIG_OVERRIDES.get({})
    merged = dict(current) if isinstance(current, dict) else {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if value is not None:
                merged[str(key)] = value
        # Collapse legacy tier overrides onto the single LLM key :-)
        for legacy_key in _LLM_OVERRIDE_KEYS:
            if legacy_key in merged and merged.get(legacy_key) is not None:
                merged["LLM"] = merged[legacy_key]
                break
    token: Token = _RUNTIME_CONFIG_OVERRIDES.set(merged)
    try:
        yield
    finally:
        _RUNTIME_CONFIG_OVERRIDES.reset(token)


class Config:
    """Load runtime settings from env vars with sensible local defaults."""

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        config = self._load_config(config_path)

        self.llm = self._get_str("LLM", config)
        self.token_limit = self._get_int("TOKEN_LIMIT", config)
        self.temperature = self._get_float("TEMPERATURE", config)
        self.llm_kwargs = self._get_dict("LLM_KWARGS", config)
        self.reasoning_effort = self.parse_reasoning_effort(
            self._get_str("REASONING_EFFORT", config) or None
        )

        self.llm_provider, self.llm_model = self.parse_llm(self.llm)

    def resolve_llm(self) -> tuple[str | None, str | None]:
        """Return ``(provider, model)`` for the configured LLM."""
        return self.llm_provider, self.llm_model

    @staticmethod
    def _load_config(config_path: str | None) -> dict[str, Any]:
        if not config_path:
            return dict(DEFAULT_CONFIG)
        if not os.path.exists(config_path):
            return dict(DEFAULT_CONFIG)

        with open(config_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        merged = dict(DEFAULT_CONFIG)
        if isinstance(loaded, dict):
            # Accept legacy JSON config keys :-)
            if "LLM" not in loaded:
                for legacy in ("SMART_LLM", "FAST_LLM", "STRATEGIC_LLM"):
                    if loaded.get(legacy):
                        loaded = {**loaded, "LLM": loaded[legacy]}
                        break
            if "TOKEN_LIMIT" not in loaded:
                for legacy in (
                    "SMART_TOKEN_LIMIT",
                    "FAST_TOKEN_LIMIT",
                    "STRATEGIC_TOKEN_LIMIT",
                ):
                    if loaded.get(legacy) is not None:
                        loaded = {**loaded, "TOKEN_LIMIT": loaded[legacy]}
                        break
            merged.update(loaded)
        return merged

    @staticmethod
    def _get_runtime_override(key: str) -> Any:
        overrides = _RUNTIME_CONFIG_OVERRIDES.get({})
        if not isinstance(overrides, dict):
            return _MISSING
        if key == "LLM":
            for candidate in _LLM_OVERRIDE_KEYS:
                if candidate in overrides:
                    return overrides[candidate]
            return _MISSING
        if key in overrides:
            return overrides[key]
        return _MISSING

    @classmethod
    def _env_lookup(cls, key: str) -> str | None:
        for env_name in _ENV_ALIASES.get(key, (key,)):
            raw = os.getenv(env_name)
            if raw is not None and str(raw).strip() != "":
                return raw
        return None

    @classmethod
    def _get_str(cls, key: str, config: dict[str, Any]) -> str:
        override = cls._get_runtime_override(key)
        if override is not _MISSING:
            return str(override) if override is not None else ""
        value = cls._env_lookup(key)
        if value is None:
            value = config.get(key)
        resolved = str(value) if value is not None else ""
        if key == "LLM" and resolved and ":" not in resolved:
            return str(config.get(key, ""))
        return resolved

    @classmethod
    def _get_int(cls, key: str, config: dict[str, Any]) -> int:
        override = cls._get_runtime_override(key)
        if override is not _MISSING:
            return int(override)
        value = cls._env_lookup(key)
        if value is None:
            return int(config.get(key, 0))
        return int(value)

    @classmethod
    def _get_float(cls, key: str, config: dict[str, Any]) -> float:
        override = cls._get_runtime_override(key)
        if override is not _MISSING:
            return float(override)
        value = cls._env_lookup(key)
        if value is None:
            return float(config.get(key, 0.0))
        return float(value)

    @classmethod
    def _get_dict(cls, key: str, config: dict[str, Any]) -> dict[str, Any]:
        override = cls._get_runtime_override(key)
        if override is not _MISSING:
            if isinstance(override, dict):
                return dict(override)
            if isinstance(override, str):
                try:
                    parsed = json.loads(override)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{key} must be valid JSON") from exc
                if not isinstance(parsed, dict):
                    raise ValueError(f"{key} must be a JSON object")
                return parsed
            raise ValueError(f"{key} must be a JSON object")
        value = cls._env_lookup(key)
        if value is None:
            loaded = config.get(key, {})
            return dict(loaded) if isinstance(loaded, dict) else {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{key} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{key} must be a JSON object")
        return parsed

    @staticmethod
    def parse_llm(llm_str: str | None) -> tuple[str | None, str | None]:
        if llm_str is None:
            return None, None
        try:
            provider, model = llm_str.split(":", 1)
        except ValueError as exc:
            raise ValueError(
                "Set PM_PAL_LLM (or LLM) as '<provider>:<model>', for example 'openai:gpt-4.1'"
            ) from exc
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported {provider}. Supported providers: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
            )
        return provider, model

    @staticmethod
    def parse_reasoning_effort(reasoning_effort_str: str | None) -> str:
        if reasoning_effort_str is None:
            return ReasoningEfforts.Medium.value
        valid = {effort.value for effort in ReasoningEfforts}
        if reasoning_effort_str not in valid:
            raise ValueError(
                f"Invalid reasoning effort: {reasoning_effort_str}. Valid options: {', '.join(sorted(valid))}"
            )
        return reasoning_effort_str
