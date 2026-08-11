"""Small helper layer for request-time chat model invocation.

Modified from GPT Researcher: https://github.com/assafelovic/gpt-researcher
Original license: Apache-2.0
This file has been adapted for this repository's review runtime.
"""

from __future__ import annotations

import logging
from typing import Any

from pm_pal.runtime.llm_provider.generic.base import (
    NO_SUPPORT_TEMPERATURE_MODELS,
    SUPPORT_REASONING_EFFORT_MODELS,
    ReasoningEfforts,
)


def get_llm(llm_provider: str, **kwargs: Any):
    from pm_pal.runtime.llm_provider import GenericLLMProvider

    return GenericLLMProvider.from_provider(llm_provider, **kwargs)


def _build_provider_kwargs(
    *,
    model: str,
    temperature: float | None,
    max_tokens: int | None,
    llm_kwargs: dict[str, Any] | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    provider_kwargs: dict[str, Any] = {"model": model}
    if llm_kwargs:
        provider_kwargs.update(llm_kwargs)

    if model in SUPPORT_REASONING_EFFORT_MODELS and reasoning_effort is not None:
        provider_kwargs["reasoning_effort"] = reasoning_effort

    if model not in NO_SUPPORT_TEMPERATURE_MODELS:
        provider_kwargs["temperature"] = temperature
        provider_kwargs["max_tokens"] = max_tokens

    return provider_kwargs


async def create_chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float | None = 0.4,
    max_tokens: int | None = 4000,
    llm_provider: str | None = None,
    stream: bool = False,
    llm_kwargs: dict[str, Any] | None = None,
    reasoning_effort: str | None = ReasoningEfforts.Medium.value,
    **kwargs: Any,
) -> str:
    if not model:
        raise ValueError("Model cannot be None")
    if max_tokens is not None and max_tokens > 32001:
        raise ValueError(f"Max tokens cannot be more than 32,000, but got {max_tokens}")
    if not llm_provider:
        raise ValueError("llm_provider cannot be None")

    provider_kwargs = _build_provider_kwargs(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        llm_kwargs=llm_kwargs,
        reasoning_effort=reasoning_effort,
    )

    provider = get_llm(llm_provider, **provider_kwargs)
    try:
        return await provider.get_chat_response(messages, stream=stream, **kwargs)
    except Exception as exc:
        logging.error("Failed to get response from %s API: %s", llm_provider, exc)
        raise RuntimeError(f"Failed to get response from {llm_provider} API") from exc
