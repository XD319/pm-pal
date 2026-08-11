"""Reusable cache for normalized requirement payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .normalizer import NormalizedRequirement, normalize_requirement


@dataclass(frozen=True, slots=True)
class NormalizerCacheResult:
    requirement: NormalizedRequirement
    cache_hit: bool
    cache_key: str
    cache_backend: str


class BaseNormalizerCache:
    """Abstract cache contract for normalized requirements."""

    backend_name = "unknown"

    def get(self, cache_key: str) -> NormalizedRequirement | None:
        raise NotImplementedError

    def set(self, cache_key: str, requirement: NormalizedRequirement) -> None:
        raise NotImplementedError


class InMemoryNormalizerCache(BaseNormalizerCache):
    backend_name = "memory"

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def get(self, cache_key: str) -> NormalizedRequirement | None:
        payload = self._entries.get(cache_key)
        if not isinstance(payload, dict):
            return None
        return _payload_to_requirement(payload)

    def set(self, cache_key: str, requirement: NormalizedRequirement) -> None:
        self._entries[cache_key] = _requirement_to_payload(requirement)


class FileBackedNormalizerCache(BaseNormalizerCache):
    backend_name = "file"

    def __init__(self, cache_path: str | Path) -> None:
        self.cache_path = Path(cache_path)

    def get(self, cache_key: str) -> NormalizedRequirement | None:
        payload = self._load_entries().get(cache_key)
        if not isinstance(payload, dict):
            return None
        return _payload_to_requirement(payload)

    def set(self, cache_key: str, requirement: NormalizedRequirement) -> None:
        entries = self._load_entries()
        entries[cache_key] = _requirement_to_payload(requirement)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_entries(self) -> dict[str, Any]:
        if not self.cache_path.exists():
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        entries = payload.get("entries")
        return dict(entries) if isinstance(entries, dict) else {}


_DEFAULT_MEMORY_CACHE = InMemoryNormalizerCache()


def normalize_requirement_with_cache(
    prd_text: str,
    *,
    cache: BaseNormalizerCache | None = None,
) -> NormalizerCacheResult:
    normalized_text = str(prd_text or "").strip()
    if not normalized_text:
        raise ValueError("prd_text must not be empty")

    resolved_cache = cache or _DEFAULT_MEMORY_CACHE
    cache_key = hashlib.sha1(normalized_text.encode("utf-8")).hexdigest()
    cached = resolved_cache.get(cache_key)
    if cached is not None:
        return NormalizerCacheResult(
            requirement=cached,
            cache_hit=True,
            cache_key=cache_key,
            cache_backend=resolved_cache.backend_name,
        )

    requirement = normalize_requirement(normalized_text)
    resolved_cache.set(cache_key, requirement)
    return NormalizerCacheResult(
        requirement=requirement,
        cache_hit=False,
        cache_key=cache_key,
        cache_backend=resolved_cache.backend_name,
    )


def _requirement_to_payload(requirement: NormalizedRequirement) -> dict[str, Any]:
    return asdict(requirement)


def _payload_to_requirement(payload: dict[str, Any]) -> NormalizedRequirement:
    return NormalizedRequirement(
        source_text=str(payload.get("source_text", "") or ""),
        summary=str(payload.get("summary", "") or ""),
        scenarios=tuple(str(item) for item in payload.get("scenarios", []) or []),
        acceptance_criteria=tuple(
            str(item) for item in payload.get("acceptance_criteria", []) or []
        ),
        dependency_hints=tuple(
            str(item) for item in payload.get("dependency_hints", []) or []
        ),
        risk_hints=tuple(str(item) for item in payload.get("risk_hints", []) or []),
        modules=tuple(str(item) for item in payload.get("modules", []) or []),
        roles=tuple(str(item) for item in payload.get("roles", []) or []),
        headings=tuple(str(item) for item in payload.get("headings", []) or []),
        in_scope=tuple(str(item) for item in payload.get("in_scope", []) or []),
        out_of_scope=tuple(str(item) for item in payload.get("out_of_scope", []) or []),
        completeness_signals=tuple(
            str(item) for item in payload.get("completeness_signals", []) or []
        ),
    )
