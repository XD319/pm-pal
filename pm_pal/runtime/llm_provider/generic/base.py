"""Provider-agnostic LangChain chat model loader used by the review runtime.

Modified from GPT Researcher: https://github.com/assafelovic/gpt-researcher
Original license: Apache-2.0
This file has been adapted for this repository's review runtime.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from enum import Enum
from typing import Any

# Providers with Settings catalog + live probe coverage :-)
CORE_PROVIDERS = frozenset(
    {
        "anthropic",
        "azure_openai",
        "dashscope",
        "deepseek",
        "ollama",
        "openai",
        "openrouter",
        "vllm_openai",
    }
)

# Still loadable via env / LLM_KWARGS, but marked experimental in catalog :-)
EXPERIMENTAL_PROVIDERS = frozenset(
    {
        "aimlapi",
        "bedrock",
        "cohere",
        "fireworks",
        "gigachat",
        "google_genai",
        "google_vertexai",
        "groq",
        "huggingface",
        "litellm",
        "mistralai",
        "netmind",
        "together",
        "xai",
    }
)

SUPPORTED_PROVIDERS = CORE_PROVIDERS | EXPERIMENTAL_PROVIDERS
_SUPPORTED_PROVIDERS = SUPPORTED_PROVIDERS  # back-compat for existing imports :-)

NO_SUPPORT_TEMPERATURE_MODELS = [
    "deepseek/deepseek-reasoner",
    "gpt-5",
    "gpt-5-mini",
    "o1",
    "o1-2024-12-17",
    "o1-mini",
    "o1-mini-2024-09-12",
    "o1-preview",
    "o3",
    "o3-2025-04-16",
    "o3-mini",
    "o3-mini-2025-01-31",
    "o4-mini",
    "o4-mini-2025-04-16",
]

SUPPORT_REASONING_EFFORT_MODELS = [
    "o3",
    "o3-2025-04-16",
    "o3-mini",
    "o3-mini-2025-01-31",
    "o4-mini",
    "o4-mini-2025-04-16",
]


class ReasoningEfforts(str, Enum):
    High = "high"
    Medium = "medium"
    Low = "low"


def provider_tier(provider: str) -> str:
    if provider in CORE_PROVIDERS:
        return "core"
    if provider in EXPERIMENTAL_PROVIDERS:
        return "experimental"
    return "unsupported"


class GenericLLMProvider:
    """Thin wrapper around a LangChain chat model instance."""

    def __init__(self, llm: Any):
        self.llm = llm

    @classmethod
    def from_provider(cls, provider: str, **kwargs: Any) -> GenericLLMProvider:
        try:
            factory = _provider_factories()[provider]
        except KeyError as exc:
            supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"Unsupported {provider}.\n\nSupported model providers are: {supported}"
            ) from exc
        llm = factory(dict(kwargs))
        return cls(llm)

    async def get_chat_response(
        self,
        messages: Any,
        stream: bool = False,
        **kwargs: Any,
    ) -> str:
        if not stream:
            result = await self.llm.ainvoke(messages, **kwargs)
            return _extract_text(result)

        text_parts: list[str] = []
        async for chunk in self.llm.astream(messages, **kwargs):
            fragment = _extract_text(chunk)
            if fragment:
                text_parts.append(fragment)
        return "".join(text_parts)


def _provider_factories() -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {
        "aimlapi": _build_aimlapi,
        "anthropic": _build_anthropic,
        "azure_openai": _build_azure_openai,
        "bedrock": _build_bedrock,
        "cohere": _build_cohere,
        "dashscope": _build_dashscope,
        "deepseek": _build_deepseek,
        "fireworks": _build_fireworks,
        "gigachat": _build_gigachat,
        "google_genai": _build_google_genai,
        "google_vertexai": _build_google_vertexai,
        "groq": _build_groq,
        "huggingface": _build_huggingface,
        "litellm": _build_litellm,
        "mistralai": _build_mistralai,
        "netmind": _build_netmind,
        "ollama": _build_ollama,
        "openai": _build_openai,
        "openrouter": _build_openrouter,
        "together": _build_together,
        "vllm_openai": _build_vllm_openai,
        "xai": _build_xai,
    }


def _extract_text(payload: Any) -> str:
    content = getattr(payload, "content", payload)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
                    continue
                nested_text = (
                    item.get("text", {}).get("value")
                    if isinstance(item.get("text"), dict)
                    else None
                )
                if isinstance(nested_text, str):
                    parts.append(nested_text)
                    continue
            text_attr = getattr(item, "text", None)
            if isinstance(text_attr, str):
                parts.append(text_attr)
        return "".join(parts)
    return str(content or "")


def _check_pkg(package_name: str) -> None:
    if importlib.util.find_spec(package_name):
        return
    pip_name = package_name.replace("_", "-")
    raise ImportError(
        f"Unable to import {pip_name}. Please install with `pip install -U {pip_name}`"
    )


def _openai_compatible_kwargs(
    kwargs: dict[str, Any],
    *,
    default_base: str | None = None,
    env_api_key: str | None = None,
    env_base: str | None = None,
) -> dict[str, Any]:
    """Normalize Settings llm_kwargs (api_key/base_url) for ChatOpenAI-style models."""
    cleaned = {key: value for key, value in kwargs.items() if value is not None}
    base_url = cleaned.pop("base_url", None)
    if base_url and "openai_api_base" not in cleaned:
        cleaned["openai_api_base"] = base_url
    if env_base and "openai_api_base" not in cleaned:
        env_base_value = os.environ.get(env_base)
        if env_base_value:
            cleaned["openai_api_base"] = env_base_value
    if default_base:
        cleaned.setdefault("openai_api_base", default_base)
    if "api_key" not in cleaned and "openai_api_key" not in cleaned and env_api_key:
        env_key_value = os.environ.get(env_api_key)
        if env_key_value:
            cleaned["openai_api_key"] = env_key_value
    return cleaned


def _build_openai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        **_openai_compatible_kwargs(
            kwargs, env_base="OPENAI_BASE_URL", env_api_key="OPENAI_API_KEY"
        )
    )


def _build_anthropic(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_anthropic")
    from langchain_anthropic import ChatAnthropic

    cleaned = {key: value for key, value in kwargs.items() if value is not None}
    return ChatAnthropic(**cleaned)


def _build_azure_openai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_openai import AzureChatOpenAI

    cleaned = {key: value for key, value in kwargs.items() if value is not None}
    model = cleaned.get("model")
    if model and "azure_deployment" not in cleaned:
        cleaned["azure_deployment"] = model
    return AzureChatOpenAI(**cleaned)


def _build_cohere(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_cohere")
    from langchain_cohere import ChatCohere

    return ChatCohere(**kwargs)


def _build_google_vertexai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_google_vertexai")
    from langchain_google_vertexai import ChatVertexAI

    return ChatVertexAI(**kwargs)


def _build_google_genai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_google_genai")
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(**kwargs)


def _build_fireworks(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_fireworks")
    from langchain_fireworks import ChatFireworks

    return ChatFireworks(**kwargs)


def _build_ollama(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_ollama")
    from langchain_ollama import ChatOllama

    cleaned = {key: value for key, value in kwargs.items() if value is not None}
    if "base_url" not in cleaned:
        env_base = os.environ.get("OLLAMA_BASE_URL")
        if env_base:
            cleaned["base_url"] = env_base
    return ChatOllama(**cleaned)


def _build_together(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_together")
    from langchain_together import ChatTogether

    return ChatTogether(**kwargs)


def _build_mistralai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_mistralai")
    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(**kwargs)


def _build_huggingface(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_huggingface")
    from langchain_huggingface import ChatHuggingFace

    model_id = kwargs.pop("model", None) or kwargs.pop("model_name", None)
    if model_id is not None:
        kwargs["model_id"] = model_id
    return ChatHuggingFace(**kwargs)


def _build_groq(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_groq")
    from langchain_groq import ChatGroq

    return ChatGroq(**kwargs)


def _build_bedrock(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_aws")
    from langchain_aws import ChatBedrock

    model_id = kwargs.pop("model", None) or kwargs.pop("model_name", None)
    if model_id is not None:
        kwargs = {"model_id": model_id, "model_kwargs": kwargs}
    return ChatBedrock(**kwargs)


def _build_dashscope(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        **_openai_compatible_kwargs(
            kwargs,
            default_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            env_api_key="DASHSCOPE_API_KEY",
        )
    )


def _build_xai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_xai")
    from langchain_xai import ChatXAI

    return ChatXAI(**kwargs)


def _build_deepseek(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        **_openai_compatible_kwargs(
            kwargs,
            default_base="https://api.deepseek.com",
            env_api_key="DEEPSEEK_API_KEY",
        )
    )


def _build_litellm(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_community")
    from langchain_community.chat_models.litellm import ChatLiteLLM

    return ChatLiteLLM(**kwargs)


def _build_gigachat(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_gigachat")
    from langchain_gigachat.chat_models import GigaChat

    kwargs.pop("model", None)
    return GigaChat(**kwargs)


def _build_openrouter(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_core.rate_limiters import InMemoryRateLimiter
    from langchain_openai import ChatOpenAI

    requests_per_second = float(os.environ.get("OPENROUTER_LIMIT_RPS", "1.0"))
    rate_limiter = InMemoryRateLimiter(
        requests_per_second=requests_per_second,
        check_every_n_seconds=0.1,
        max_bucket_size=10,
    )
    cleaned = _openai_compatible_kwargs(
        kwargs,
        default_base="https://openrouter.ai/api/v1",
        env_api_key="OPENROUTER_API_KEY",
    )
    cleaned.setdefault("request_timeout", 180)
    cleaned.setdefault("rate_limiter", rate_limiter)
    return ChatOpenAI(**cleaned)


def _build_vllm_openai(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_openai import ChatOpenAI

    cleaned = _openai_compatible_kwargs(kwargs)
    if "openai_api_base" not in cleaned:
        cleaned["openai_api_base"] = os.environ["VLLM_OPENAI_API_BASE"]
    if "api_key" not in cleaned and "openai_api_key" not in cleaned:
        cleaned["openai_api_key"] = os.environ["VLLM_OPENAI_API_KEY"]
    return ChatOpenAI(**cleaned)


def _build_aimlapi(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_openai")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        **_openai_compatible_kwargs(
            kwargs,
            default_base="https://api.aimlapi.com/v1",
            env_api_key="AIMLAPI_API_KEY",
        )
    )


def _build_netmind(kwargs: dict[str, Any]) -> Any:
    _check_pkg("langchain_netmind")
    from langchain_netmind import ChatNetmind

    return ChatNetmind(**kwargs)
