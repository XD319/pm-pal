"""Redact sensitive values from mappings and serialized config dumps."""
from __future__ import annotations

import re
from typing import Any

_REDACTED = "***REDACTED***"
_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|secret|token|password|credential)",
    re.IGNORECASE,
)
_SENSITIVE_HEADER_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|secret|token|password)\s*[:=]\s*\S+"
)


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "")))


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(str(key)):
            redacted[key] = _REDACTED
        else:
            redacted[key] = redact_value(value)
    return redacted


def redact_text(text: str) -> str:
    if not text:
        return text
    return _SENSITIVE_HEADER_RE.sub(r"\1=***REDACTED***", text)
