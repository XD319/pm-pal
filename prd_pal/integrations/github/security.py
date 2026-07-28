"""GitHub webhook signature verification for X-Hub-Signature-256."""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Mapping


_SIGNATURE_DISABLED_ENV = "MARRDP_GITHUB_SIGNATURE_DISABLED"
_WEBHOOK_SECRET_ENV = "MARRDP_GITHUB_WEBHOOK_SECRET"
_FALSE_VALUES = {"0", "false", "no", "off"}


class GitHubSignatureVerificationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GitHubSecuritySettings:
    signature_disabled: bool = True
    webhook_secret: str = ""


def _env_disabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def get_github_security_settings() -> GitHubSecuritySettings:
    return GitHubSecuritySettings(
        signature_disabled=_env_disabled(_SIGNATURE_DISABLED_ENV, default=True),
        webhook_secret=str(os.getenv(_WEBHOOK_SECRET_ENV, "") or "").strip(),
    )


def _header(headers: Mapping[str, str], *names: str) -> str:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    for name in names:
        value = lowered.get(name.lower(), "")
        if value:
            return value.strip()
    return ""


def build_github_signature(*, secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_github_signature(
    *,
    headers: Mapping[str, str],
    body: bytes,
    settings: GitHubSecuritySettings | None = None,
) -> None:
    resolved = settings or get_github_security_settings()
    if resolved.signature_disabled:
        return

    secret = str(resolved.webhook_secret or "").strip()
    if not secret:
        raise GitHubSignatureVerificationError(
            "missing_github_webhook_secret",
            "GitHub webhook secret is not configured.",
        )

    signature = _header(headers, "X-Hub-Signature-256")
    if not signature:
        raise GitHubSignatureVerificationError(
            "missing_github_signature",
            "Missing X-Hub-Signature-256 header.",
        )

    expected = build_github_signature(secret=secret, body=body)
    if not hmac.compare_digest(signature, expected):
        raise GitHubSignatureVerificationError(
            "invalid_github_signature",
            "GitHub webhook signature verification failed.",
        )
