"""Minimal live probes for saved provider connections (mockable in tests)."""
from __future__ import annotations

from typing import Any, Callable

import httpx

ProbeFn = Callable[..., dict[str, Any]]


def package_pip_name(package: str) -> str:
    return package.replace("_", "-")


def probe_provider_connection(
    provider: str,
    *,
    api_key: str = "",
    base_url: str = "",
    extra: dict[str, Any] | None = None,
    probe: ProbeFn | None = None,
) -> dict[str, Any]:
    if probe is not None:
        return probe(
            provider,
            api_key=api_key,
            base_url=base_url,
            extra=dict(extra or {}),
        )
    extra = dict(extra or {})
    if provider == "ollama":
        return _probe_ollama(base_url)
    if provider in {"openai", "deepseek", "openrouter", "vllm_openai", "together", "xai", "aimlapi"}:
        return _probe_openai_compatible(base_url, api_key)
    if provider == "azure_openai":
        return _probe_azure(base_url, api_key, extra)
    if provider == "anthropic":
        return _probe_anthropic(api_key)
    return {
        "ok": True,
        "message": "Dependency check passed; no live probe is configured for this provider.",
    }


def _probe_ollama(base_url: str) -> dict[str, Any]:
    url = f"{(base_url or 'http://localhost:11434').rstrip('/')}/api/tags"
    response = httpx.get(url, timeout=10.0)
    response.raise_for_status()
    return {"ok": True, "message": "Ollama responded to /api/tags."}


def _probe_openai_compatible(base_url: str, api_key: str) -> dict[str, Any]:
    root = (base_url or "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(f"{root}/models", headers=headers, timeout=15.0)
    response.raise_for_status()
    return {"ok": True, "message": "Provider responded to models list probe."}


def _probe_azure(base_url: str, api_key: str, extra: dict[str, Any]) -> dict[str, Any]:
    deployment = str(extra.get("deployment") or extra.get("azure_deployment") or "").strip()
    if not base_url or not deployment:
        return {
            "ok": True,
            "message": "Azure credentials stored; add base URL and deployment for a live probe.",
        }
    url = f"{base_url.rstrip('/')}/openai/deployments/{deployment}?api-version=2024-02-01"
    headers = {"api-key": api_key} if api_key else {}
    response = httpx.get(url, headers=headers, timeout=15.0)
    response.raise_for_status()
    return {"ok": True, "message": "Azure deployment probe succeeded."}


def _probe_anthropic(api_key: str) -> dict[str, Any]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    response = httpx.get("https://api.anthropic.com/v1/models", headers=headers, timeout=15.0)
    response.raise_for_status()
    return {"ok": True, "message": "Anthropic responded to models list probe."}
