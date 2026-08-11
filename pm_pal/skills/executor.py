"""Skill executor with configurable TTL caching and trace metadata."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from .cache_backend import (
    InMemorySkillCacheBackend,
    SQLiteSkillCacheBackend,
    SkillCacheBackend,
)
from ..templates import get_template
from ..utils.trace import trace_start

_DEFAULT_CACHE_TTL_SEC = 300
_CACHE_ENABLED_ENV = "SKILLS_CACHE_ENABLED"
_CACHE_BACKEND_ENV = "SKILLS_CACHE_BACKEND"
_CACHE_SQLITE_PATH_ENV = "SKILLS_CACHE_SQLITE_PATH"
_TRUE_VALUES = {"1", "true", "yes", "on"}

SkillHandler = Callable[[BaseModel], Any] | Callable[[BaseModel], Awaitable[Any]]
TraceSink = dict[str, Any] | Callable[[str, dict[str, Any]], None]


class SkillExecutionError(RuntimeError):
    """Raised when skill execution or validation fails."""


@dataclass(frozen=True)
class SkillSpec:
    """Runtime contract for an executable skill."""

    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: SkillHandler
    config_version: str = "v1"
    cache_ttl_sec: int | None = None
    template_id: str = ""

    @property
    def ttl_sec(self) -> int:
        return (
            self.cache_ttl_sec
            if self.cache_ttl_sec is not None
            else _DEFAULT_CACHE_TTL_SEC
        )


class SkillExecutor:
    """Execute skills with optional TTL caching and per-call trace output."""

    _memory_backend = InMemorySkillCacheBackend()
    _sqlite_backends: dict[str, SQLiteSkillCacheBackend] = {}

    def __init__(
        self,
        *,
        cache_enabled: bool | None = None,
        cache_backend: SkillCacheBackend | str | None = None,
        cache_backend_path: str | None = None,
    ) -> None:
        self._cache_enabled_override = cache_enabled
        self._cache_backend_override = cache_backend
        self._cache_backend_path = cache_backend_path

    async def execute(
        self,
        spec: SkillSpec,
        payload: dict[str, Any] | BaseModel,
        *,
        trace: TraceSink | None = None,
    ) -> BaseModel:
        validated_input = spec.input_model.model_validate(payload)
        cache_key_hash = _build_cache_key_hash(spec, validated_input)
        ttl_sec = max(0, spec.ttl_sec)
        cache_enabled = self._cache_enabled()
        cache_backend = self._resolve_cache_backend()
        span = trace_start(
            spec.name,
            model="none",
            input_chars=len(_canonical_input_json(validated_input)),
        )
        if spec.template_id:
            span.set_template(get_template(spec.template_id))

        cache_hit = False
        cache_lookup_status = "disabled"
        if cache_enabled and ttl_sec > 0:
            cache_lookup = cache_backend.get(cache_key_hash, ttl_sec)
            cache_lookup_status = cache_lookup.status
            if cache_lookup.is_hit:
                cache_hit = True
                output_model = _validate_output(spec, cache_lookup.output_data)
                trace_data = span.end(status="ok")
                trace_data.update(
                    {
                        "cache_hit": True,
                        "cache_key_hash": cache_key_hash,
                        "ttl_sec": ttl_sec,
                        "cache_backend": cache_backend.name,
                        "cache_backend_target": cache_backend.target,
                        "cache_lookup_status": cache_lookup_status,
                    }
                )
                _write_trace(trace, spec.name, trace_data)
                return output_model
        elif ttl_sec <= 0:
            cache_lookup_status = "ttl_disabled"

        try:
            raw_output = spec.handler(validated_input)
            if inspect.isawaitable(raw_output):
                raw_output = await raw_output
            output_model = _validate_output(spec, raw_output)
        except ValidationError as exc:
            trace_data = span.end(status="error", error_message=str(exc))
            trace_data.update(
                {
                    "cache_hit": cache_hit,
                    "cache_key_hash": cache_key_hash,
                    "ttl_sec": ttl_sec,
                    "cache_backend": cache_backend.name,
                    "cache_backend_target": cache_backend.target,
                    "cache_lookup_status": cache_lookup_status,
                }
            )
            _write_trace(trace, spec.name, trace_data)
            raise SkillExecutionError(str(exc)) from exc
        except Exception as exc:
            trace_data = span.end(status="error", error_message=str(exc))
            trace_data.update(
                {
                    "cache_hit": cache_hit,
                    "cache_key_hash": cache_key_hash,
                    "ttl_sec": ttl_sec,
                    "cache_backend": cache_backend.name,
                    "cache_backend_target": cache_backend.target,
                    "cache_lookup_status": cache_lookup_status,
                }
            )
            _write_trace(trace, spec.name, trace_data)
            raise

        if cache_enabled and ttl_sec > 0:
            cache_backend.set(cache_key_hash, output_model.model_dump(mode="python"))

        trace_data = span.end(status="ok")
        trace_data.update(
            {
                "cache_hit": False,
                "cache_key_hash": cache_key_hash,
                "ttl_sec": ttl_sec,
                "cache_backend": cache_backend.name,
                "cache_backend_target": cache_backend.target,
                "cache_lookup_status": cache_lookup_status,
            }
        )
        _write_trace(trace, spec.name, trace_data)
        return output_model

    @classmethod
    def clear_cache(cls) -> None:
        cls._memory_backend.clear()
        for backend in cls._sqlite_backends.values():
            backend.clear()

    def _cache_enabled(self) -> bool:
        if self._cache_enabled_override is not None:
            return self._cache_enabled_override
        return _env_enabled(_CACHE_ENABLED_ENV, default=True)

    def _resolve_cache_backend(self) -> SkillCacheBackend:
        override = self._cache_backend_override
        if isinstance(override, SkillCacheBackend):
            return override

        backend_name = (
            str(override or os.getenv(_CACHE_BACKEND_ENV, "memory")).strip().lower()
        )
        if backend_name in {"sqlite", "sqlite3"}:
            backend_path = self._cache_backend_path or os.getenv(
                _CACHE_SQLITE_PATH_ENV, ".skills_cache.sqlite3"
            )
            return self._sqlite_backend(backend_path)
        return self._memory_backend

    @classmethod
    def _sqlite_backend(cls, path: str) -> SQLiteSkillCacheBackend:
        normalized_path = os.path.abspath(path)
        backend = cls._sqlite_backends.get(normalized_path)
        if backend is None:
            backend = SQLiteSkillCacheBackend(normalized_path)
            cls._sqlite_backends[normalized_path] = backend
        return backend


def _env_enabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _canonical_input_json(payload: BaseModel) -> str:
    dumped = payload.model_dump()
    return json.dumps(dumped, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_cache_key_hash(spec: SkillSpec, payload: BaseModel) -> str:
    material = "||".join(
        (spec.name, _canonical_input_json(payload), spec.config_version)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _validate_output(spec: SkillSpec, raw_output: Any) -> BaseModel:
    try:
        return spec.output_model.model_validate(raw_output)
    except ValidationError as exc:
        raise SkillExecutionError(str(exc)) from exc


def _write_trace(
    trace: TraceSink | None, name: str, trace_data: dict[str, Any]
) -> None:
    if trace is None:
        return
    if callable(trace):
        trace(name, trace_data)
        return
    trace[name] = trace_data
