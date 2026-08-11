"""Shared connector error taxonomy and normalized error payloads."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from pm_pal.schemas.base import AgentSchemaModel


class ConnectorErrorCode(str, Enum):
    """Stable connector error codes for public and private source handlers."""

    authentication_failed = "authentication_failed"
    permission_denied = "permission_denied"
    not_found = "not_found"
    invalid_source = "invalid_source"
    unsupported_source = "unsupported_source"
    network_unavailable = "network_unavailable"
    rate_limited = "rate_limited"


class ConnectorErrorPayload(AgentSchemaModel):
    """Serializable connector error details for service-layer mapping."""

    code: ConnectorErrorCode
    message: str
    source: str = ""
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ConnectorErrorMixin:
    """Adds normalized error payloads to connector exceptions."""

    error_code = ConnectorErrorCode.invalid_source
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        source: str = "",
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.source = str(source or "").strip()
        self.retryable = self.retryable if retryable is None else bool(retryable)
        self.details = dict(details or {})

    def to_error_payload(self) -> ConnectorErrorPayload:
        return ConnectorErrorPayload(
            code=self.error_code,
            message=str(self),
            source=self.source,
            retryable=bool(self.retryable),
            details=dict(self.details),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.to_error_payload().model_dump(mode="python")


class ConnectorValidationError(ConnectorErrorMixin, ValueError):
    """Raised when a source or response payload is invalid."""

    error_code = ConnectorErrorCode.invalid_source


class ConnectorUnsupportedSourceError(ConnectorValidationError):
    """Raised when a connector recognizes a source family but cannot handle it."""

    error_code = ConnectorErrorCode.unsupported_source


class ConnectorAuthError(ConnectorErrorMixin, PermissionError):
    """Raised when connector authentication is missing or rejected."""

    error_code = ConnectorErrorCode.authentication_failed


class ConnectorPermissionError(ConnectorErrorMixin, PermissionError):
    """Raised when a connector can authenticate but lacks access to the source."""

    error_code = ConnectorErrorCode.permission_denied


class ConnectorNotFoundError(ConnectorErrorMixin, FileNotFoundError):
    """Raised when a connector source cannot be found."""

    error_code = ConnectorErrorCode.not_found


class ConnectorNetworkError(ConnectorErrorMixin, ConnectionError):
    """Raised when a connector cannot reach a source or required dependency."""

    error_code = ConnectorErrorCode.network_unavailable
    retryable = True


class ConnectorRateLimitError(ConnectorNetworkError):
    """Raised when a remote source rejects a request due to rate limiting."""

    error_code = ConnectorErrorCode.rate_limited
    retryable = True


def get_connector_error_payload(exc: Exception) -> ConnectorErrorPayload | None:
    """Return a normalized payload when *exc* is a connector exception."""

    if isinstance(exc, ConnectorErrorMixin):
        return exc.to_error_payload()
    return None
