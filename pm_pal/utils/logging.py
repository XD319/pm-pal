"""Structured logging helpers for pm_pal."""

from __future__ import annotations

import contextvars
import json
import logging
import os
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

_RUN_ID_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "requirement_review_run_id", default=""
)
_LOGGER_NAMESPACE = "pm_pal"
_DEFAULT_LOG_FORMAT = "human"
_DEFAULT_LOG_LEVEL = "INFO"
_THIRD_PARTY_WARNING_LOGGERS = (
    "httpx",
    "httpcore",
    "litellm",
    "openai",
    "urllib3",
    "fontTools",
    "fontTools.subset",
    "fontTools.ttLib",
)
_STANDARD_RECORD_ATTRS = set(logging.makeLogRecord({}).__dict__)


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _format_timestamp(created: float) -> str:
    return (
        datetime.fromtimestamp(created, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _normalize_log_level(log_level: str | int | None) -> int:
    if isinstance(log_level, int):
        return log_level
    raw_level = (
        str(log_level or os.getenv("LOG_LEVEL", _DEFAULT_LOG_LEVEL)).strip().upper()
    )
    return getattr(logging, raw_level, logging.INFO)


def _normalize_log_format(log_format: str | None) -> str:
    raw_format = (
        str(log_format or os.getenv("LOG_FORMAT", _DEFAULT_LOG_FORMAT)).strip().lower()
    )
    return "json" if raw_format == "json" else "human"


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(item) for item in value]
    return str(value)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
            continue
        extras[key] = _safe_json_value(value)
    return extras


def _short_logger_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        return _LOGGER_NAMESPACE
    prefix = f"{_LOGGER_NAMESPACE}."
    if normalized.startswith(prefix):
        return normalized[len(prefix) :]
    return normalized


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "run_id", ""):
            record.run_id = _RUN_ID_VAR.get("")
        if not hasattr(record, "node"):
            record.node = None
        if not hasattr(record, "duration_ms"):
            record.duration_ms = None
        return True


class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", "") or "",
            "node": getattr(record, "node", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """Format log records for terminal-oriented human readability."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = _format_timestamp(record.created)
        level = f"{record.levelname:<5}"
        logger_name = _short_logger_name(record.name)
        run_id = str(getattr(record, "run_id", "") or "").strip()
        duration_ms = getattr(record, "duration_ms", None)
        message = record.getMessage()

        parts = [f"[{timestamp}] {level} [{logger_name}]"]
        if run_id:
            parts.append(f"({run_id})")
        parts.append(message)
        if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
            if duration_ms >= 1000:
                parts.append(f"in {duration_ms / 1000:.1f}s")
            else:
                parts.append(f"in {int(duration_ms)}ms")

        rendered = " ".join(parts)
        if record.exc_info:
            rendered = f"{rendered}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            rendered = f"{rendered}\n{self.formatStack(record.stack_info)}"
        return rendered


def build_formatter(log_format: str | None = None) -> logging.Formatter:
    return (
        StructuredFormatter()
        if _normalize_log_format(log_format) == "json"
        else HumanReadableFormatter()
    )


class SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that ignores writes after pytest closes the capture stream. :-)"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except ValueError:
            # "I/O operation on closed file" under pytest capture.
            self.handleError(record)


def setup_logging(
    log_level: str | int | None = None, log_format: str | None = None
) -> None:
    resolved_level = _normalize_log_level(log_level)
    formatter = build_formatter(log_format)
    context_filter = _ContextFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)
    root_logger.handlers.clear()
    root_logger.filters.clear()

    stream_handler = SafeStreamHandler()
    stream_handler.setLevel(resolved_level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(context_filter)
    root_logger.addHandler(stream_handler)
    root_logger.addFilter(context_filter)

    app_logger = logging.getLogger(_LOGGER_NAMESPACE)
    app_logger.setLevel(resolved_level)
    app_logger.propagate = True

    for logger_name in _THIRD_PARTY_WARNING_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> logging.Logger:
    normalized = str(name or "").strip()
    if not normalized:
        return logging.getLogger(_LOGGER_NAMESPACE)
    if normalized == _LOGGER_NAMESPACE or normalized.startswith(
        f"{_LOGGER_NAMESPACE}."
    ):
        return logging.getLogger(normalized)
    return logging.getLogger(f"{_LOGGER_NAMESPACE}.{normalized}")


class RunLogContext(AbstractContextManager["RunLogContext"]):
    """Context manager that injects run_id into all subsequent log records."""

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "").strip()
        self._token: contextvars.Token[str] | None = None

    def __enter__(self) -> "RunLogContext":
        self._token = _RUN_ID_VAR.set(self.run_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _RUN_ID_VAR.reset(self._token)
            self._token = None


__all__ = [
    "HumanReadableFormatter",
    "RunLogContext",
    "StructuredFormatter",
    "build_formatter",
    "get_logger",
    "setup_logging",
]
