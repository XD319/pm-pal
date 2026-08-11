"""Reusable helpers for normalizing connector responses."""

from __future__ import annotations

from typing import Any, Iterable


def extract_mapping(value: Any) -> dict[str, Any] | None:
    """Return a shallow dict copy when *value* is mapping-like."""

    return dict(value) if isinstance(value, dict) else None


def extract_message(
    payload: dict[str, Any],
    *,
    keys: Iterable[str] = ("msg", "message", "error", "error_message"),
) -> str:
    """Return the first non-empty text message from a response payload."""

    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_content_type(headers: Any) -> str:
    """Normalize response content type from urllib or email headers."""

    if headers is None:
        return ""
    content_type_getter = getattr(headers, "get_content_type", None)
    if callable(content_type_getter):
        return str(content_type_getter()).lower()
    getter = getattr(headers, "get", None)
    if callable(getter):
        return str(getter("Content-Type", "") or "").split(";", 1)[0].strip().lower()
    return ""


def resolve_charset(headers: Any, *, default: str = "utf-8") -> str:
    """Normalize response charset from urllib or email headers."""

    if headers is None:
        return default
    charset_getter = getattr(headers, "get_content_charset", None)
    if callable(charset_getter):
        return str(charset_getter() or default)
    return default


def resolve_declared_length(headers: Any) -> int | None:
    """Parse Content-Length when present and numeric."""

    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw_value = getter("Content-Length")
    if raw_value in (None, ""):
        return None
    try:
        return int(str(raw_value))
    except (TypeError, ValueError):
        return None


def decode_text_body(raw_body: bytes, *, charset: str = "utf-8") -> str:
    """Decode a text response body with a forgiving fallback."""

    try:
        return raw_body.decode(charset or "utf-8")
    except LookupError:
        return raw_body.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return raw_body.decode(charset or "utf-8", errors="replace")
