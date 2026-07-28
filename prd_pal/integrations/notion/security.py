"""Notion webhook signature verification."""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Mapping

_SIGNATURE_DISABLED_ENV = "MARRDP_NOTION_SIGNATURE_DISABLED"
_SIGNING_SECRET_ENV = "MARRDP_NOTION_SIGNING_SECRET"
_VERIFICATION_TOKEN_ENV = "MARRDP_NOTION_VERIFICATION_TOKEN"
_FALSE_VALUES = {"0", "false", "no", "off"}


class NotionSignatureVerificationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class NotionSecuritySettings:
    signature_disabled: bool = True
    signing_secret: str = ""

    @property
    def signature_enabled(self) -> bool:
        return not self.signature_disabled


def _env_disabled(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def get_notion_security_settings() -> NotionSecuritySettings:
    signing_secret = str(os.getenv(_SIGNING_SECRET_ENV, "") or "").strip()
    if not signing_secret:
        signing_secret = str(os.getenv(_VERIFICATION_TOKEN_ENV, "") or "").strip()
    return NotionSecuritySettings(
        signature_disabled=_env_disabled(_SIGNATURE_DISABLED_ENV, default=True),
        signing_secret=signing_secret,
    )


def _header(headers: Mapping[str, str], *names: str) -> str:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    for name in names:
        value = lowered.get(name.lower(), "")
        if value:
            return value.strip()
    return ""


def build_notion_signature(*, signing_secret: str, body: bytes) -> str:
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        body or b"",
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_notion_signature(
    *,
    headers: Mapping[str, str],
    body: bytes,
    settings: NotionSecuritySettings | None = None,
) -> None:
    resolved = settings or get_notion_security_settings()
    if resolved.signature_disabled:
        return

    signature = _header(headers, "x-notion-signature")
    if not signature:
        raise NotionSignatureVerificationError(
            "invalid_notion_signature",
            "Notion signature verification failed: missing X-Notion-Signature header.",
        )
    if not resolved.signing_secret:
        raise NotionSignatureVerificationError(
            "notion_signature_not_configured",
            "Notion signature verification is enabled but no signing secret is configured.",
        )

    expected = build_notion_signature(
        signing_secret=resolved.signing_secret,
        body=body,
    )
    if not hmac.compare_digest(signature.lower(), expected.lower()):
        raise NotionSignatureVerificationError(
            "invalid_notion_signature",
            "Notion signature verification failed: signature mismatch.",
        )
