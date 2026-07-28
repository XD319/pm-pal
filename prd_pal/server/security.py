from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

_API_AUTH_DISABLED_ENV = "MARRDP_API_AUTH_DISABLED"
_API_KEY_ENV = "MARRDP_API_KEY"
_API_BEARER_TOKEN_ENV = "MARRDP_API_BEARER_TOKEN"
_API_RATE_LIMIT_DISABLED_ENV = "MARRDP_API_RATE_LIMIT_DISABLED"
_API_RATE_LIMIT_MAX_REQUESTS_ENV = "MARRDP_API_RATE_LIMIT_MAX_REQUESTS"
_API_RATE_LIMIT_WINDOW_SEC_ENV = "MARRDP_API_RATE_LIMIT_WINDOW_SEC"
_FALSE_VALUES = {"0", "false", "no", "off"}
_submission_rate_limits: dict[str, deque[float]] = defaultdict(deque)
_submission_rate_limit_lock = threading.Lock()


@dataclass(frozen=True)
class ApiSecuritySettings:
    auth_disabled: bool = True
    api_key: str = ""
    bearer_token: str = ""
    rate_limit_disabled: bool = True
    rate_limit_max_requests: int = 5
    rate_limit_window_sec: int = 60


def _env_disabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def _env_int(name: str, *, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw.strip()))
    except ValueError:
        return default


def security_settings() -> ApiSecuritySettings:
    return ApiSecuritySettings(
        auth_disabled=_env_disabled(_API_AUTH_DISABLED_ENV, default=True),
        api_key=str(os.getenv(_API_KEY_ENV, "") or "").strip(),
        bearer_token=str(os.getenv(_API_BEARER_TOKEN_ENV, "") or "").strip(),
        rate_limit_disabled=_env_disabled(_API_RATE_LIMIT_DISABLED_ENV, default=True),
        rate_limit_max_requests=_env_int(
            _API_RATE_LIMIT_MAX_REQUESTS_ENV, default=5, minimum=1
        ),
        rate_limit_window_sec=_env_int(
            _API_RATE_LIMIT_WINDOW_SEC_ENV, default=60, minimum=1
        ),
    )


def controlled_error_response(
    status_code: int,
    *,
    code: str,
    message: str,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    detail = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return JSONResponse(
        status_code=status_code, content={"detail": detail}, headers=headers
    )


def extract_bearer_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization", "") or "").strip()
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def authenticate_request(
    request: Request, settings: ApiSecuritySettings
) -> JSONResponse | None:
    if settings.auth_disabled:
        return None
    if not settings.api_key and not settings.bearer_token:
        return controlled_error_response(
            503,
            code="api_auth_not_configured",
            message=(
                "API authentication is enabled but no credentials were configured. "
                f"Set {_API_KEY_ENV} or {_API_BEARER_TOKEN_ENV}, or opt out with {_API_AUTH_DISABLED_ENV}=true."
            ),
        )

    provided_api_key = str(request.headers.get("x-api-key", "") or "").strip()
    provided_bearer = extract_bearer_token(request)
    if settings.api_key and provided_api_key == settings.api_key:
        return None
    if settings.bearer_token and provided_bearer == settings.bearer_token:
        return None

    if not provided_api_key and not provided_bearer:
        return controlled_error_response(
            401,
            code="authentication_required",
            message="Provide a valid X-API-Key header or Authorization: Bearer token.",
        )

    return controlled_error_response(
        401,
        code="invalid_api_credentials",
        message="The provided API credentials are invalid.",
    )


def rate_limit_identity(request: Request) -> str:
    provided_api_key = str(request.headers.get("x-api-key", "") or "").strip()
    if provided_api_key:
        return f"api-key:{provided_api_key}"
    provided_bearer = extract_bearer_token(request)
    if provided_bearer:
        return f"bearer:{provided_bearer}"
    client_host = request.client.host if request.client is not None else "unknown"
    return f"ip:{client_host}"


def client_ip(request: Request) -> str:
    forwarded_for = str(request.headers.get("x-forwarded-for", "") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def should_skip_request_logging(path: str) -> bool:
    normalized = str(path or "").strip()
    if normalized in {"/health", "/ready"}:
        return True
    if normalized.startswith(("/static/", "/assets/")):
        return True
    return normalized.endswith(
        (
            ".js",
            ".css",
            ".map",
            ".ico",
            ".svg",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".woff",
            ".woff2",
            ".ttf",
        )
    )


def _is_project_review_submission(path: str) -> bool:
    parts = [part for part in str(path or "").strip("/").split("/") if part]
    return (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "projects"
        and parts[3] == "reviews"
    )


def enforce_submission_rate_limit(
    request: Request, settings: ApiSecuritySettings
) -> JSONResponse | None:
    if request.method.upper() != "POST" or not _is_project_review_submission(
        request.url.path
    ):
        return None
    if settings.rate_limit_disabled:
        return None

    identity = rate_limit_identity(request)
    now = time.monotonic()
    with _submission_rate_limit_lock:
        requests = _submission_rate_limits[identity]
        while requests and now - requests[0] >= settings.rate_limit_window_sec:
            requests.popleft()
        if len(requests) >= settings.rate_limit_max_requests:
            retry_after = max(
                1, int(settings.rate_limit_window_sec - (now - requests[0])) + 1
            )
            return controlled_error_response(
                429,
                code="rate_limit_exceeded",
                message="Review submission rate limit exceeded. Retry later.",
                extra={
                    "limit": settings.rate_limit_max_requests,
                    "window_sec": settings.rate_limit_window_sec,
                    "retry_after_sec": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        requests.append(now)
    return None


def reset_submission_rate_limits() -> None:
    with _submission_rate_limit_lock:
        _submission_rate_limits.clear()
