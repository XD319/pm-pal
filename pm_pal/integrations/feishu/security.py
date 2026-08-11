from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

_SIGNATURE_DISABLED_ENV = "MARRDP_FEISHU_SIGNATURE_DISABLED"
_SIGNATURE_SECRET_ENV = "MARRDP_FEISHU_WEBHOOK_SECRET"
_ENCRYPT_KEY_ENV = "MARRDP_FEISHU_ENCRYPT_KEY"
_SIGNATURE_TOLERANCE_ENV = "MARRDP_FEISHU_SIGNATURE_TOLERANCE_SEC"
_FALSE_VALUES = {"0", "false", "no", "off"}


class FeishuSignatureVerificationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FeishuSecuritySettings:
    signature_disabled: bool = True
    webhook_secret: str = ""
    encrypt_key: str = ""
    tolerance_sec: int = 300

    @property
    def signature_enabled(self) -> bool:
        return not self.signature_disabled


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


def get_feishu_security_settings() -> FeishuSecuritySettings:
    return FeishuSecuritySettings(
        signature_disabled=_env_disabled(_SIGNATURE_DISABLED_ENV, default=True),
        webhook_secret=str(os.getenv(_SIGNATURE_SECRET_ENV, "") or "").strip(),
        encrypt_key=str(os.getenv(_ENCRYPT_KEY_ENV, "") or "").strip(),
        tolerance_sec=_env_int(_SIGNATURE_TOLERANCE_ENV, default=300, minimum=0),
    )


def _header(headers: Mapping[str, str], *names: str) -> str:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    for name in names:
        value = lowered.get(name.lower(), "")
        if value:
            return value.strip()
    return ""


def build_feishu_signature(*, secret: str, timestamp: str, body: bytes) -> str:
    payload = timestamp.encode("utf-8") + (body or b"")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_feishu_encrypt_signature(
    *, encrypt_key: str, timestamp: str, nonce: str, body: bytes
) -> str:
    payload = (
        timestamp.encode("utf-8")
        + nonce.encode("utf-8")
        + encrypt_key.encode("utf-8")
        + (body or b"")
    )
    return hashlib.sha256(payload).hexdigest()


def _validate_timestamp(
    *,
    timestamp: str,
    tolerance_sec: int,
    now: int | None,
) -> None:
    try:
        request_ts = int(timestamp)
    except ValueError as exc:
        raise FeishuSignatureVerificationError(
            "invalid_feishu_signature",
            "Feishu signature verification failed: timestamp header is invalid.",
        ) from exc

    current_ts = int(time.time() if now is None else now)
    if tolerance_sec >= 0 and abs(current_ts - request_ts) > tolerance_sec:
        raise FeishuSignatureVerificationError(
            "invalid_feishu_signature",
            "Feishu signature verification failed: timestamp is outside the allowed window.",
        )


def verify_feishu_signature(
    *,
    headers: Mapping[str, str],
    body: bytes,
    settings: FeishuSecuritySettings | None = None,
    now: int | None = None,
) -> None:
    resolved = settings or get_feishu_security_settings()
    if resolved.signature_disabled:
        return
    if not resolved.webhook_secret and not resolved.encrypt_key:
        raise FeishuSignatureVerificationError(
            "feishu_signature_not_configured",
            "Feishu signature verification is enabled but MARRDP_FEISHU_WEBHOOK_SECRET is not configured.",
        )

    timestamp = _header(
        headers, "x-lark-request-timestamp", "x-feishu-request-timestamp"
    )
    signature = _header(headers, "x-lark-signature", "x-feishu-signature")
    if not timestamp or not signature:
        raise FeishuSignatureVerificationError(
            "invalid_feishu_signature",
            "Feishu signature verification failed: missing timestamp or signature headers.",
        )
    _validate_timestamp(
        timestamp=timestamp, tolerance_sec=resolved.tolerance_sec, now=now
    )

    if resolved.encrypt_key:
        nonce = _header(headers, "x-lark-request-nonce", "x-feishu-request-nonce")
        if not nonce:
            raise FeishuSignatureVerificationError(
                "invalid_feishu_signature",
                "Feishu signature verification failed: missing nonce header.",
            )
        expected = build_feishu_encrypt_signature(
            encrypt_key=resolved.encrypt_key,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
        )
        if not hmac.compare_digest(signature.lower(), expected.lower()):
            raise FeishuSignatureVerificationError(
                "invalid_feishu_signature",
                "Feishu signature verification failed: signature mismatch.",
            )
        return

    if not resolved.webhook_secret:
        raise FeishuSignatureVerificationError(
            "feishu_signature_not_configured",
            "Feishu signature verification is enabled but MARRDP_FEISHU_WEBHOOK_SECRET is not configured.",
        )

    expected = build_feishu_signature(
        secret=resolved.webhook_secret, timestamp=timestamp, body=body
    )
    if not hmac.compare_digest(signature, expected):
        raise FeishuSignatureVerificationError(
            "invalid_feishu_signature",
            "Feishu signature verification failed: signature mismatch.",
        )
