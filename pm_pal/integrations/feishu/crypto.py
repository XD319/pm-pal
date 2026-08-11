"""Feishu event payload encryption helpers (AES-256-CBC)."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class FeishuDecryptError(ValueError):
    """Raised when an encrypted Feishu event cannot be decrypted."""


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise FeishuDecryptError("empty decrypted payload")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise FeishuDecryptError("invalid PKCS7 padding")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise FeishuDecryptError("invalid PKCS7 padding bytes")
    return data[:-pad_len]


def decrypt_feishu_event_string(*, encrypt_key: str, encrypted: str) -> str:
    normalized_key = str(encrypt_key or "").strip()
    normalized_payload = str(encrypted or "").strip()
    if not normalized_key:
        raise FeishuDecryptError("encrypt_key is required")
    if not normalized_payload:
        raise FeishuDecryptError("encrypted payload is required")
    try:
        raw = base64.b64decode(normalized_payload)
    except (ValueError, TypeError) as exc:
        raise FeishuDecryptError("encrypted payload is not valid base64") from exc
    if len(raw) < 32:
        raise FeishuDecryptError("encrypted payload is too short")
    iv = raw[:16]
    ciphertext = raw[16:]
    if len(ciphertext) % 16 != 0:
        raise FeishuDecryptError("ciphertext is not block-aligned")
    key = hashlib.sha256(normalized_key.encode("utf-8")).digest()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    try:
        return _pkcs7_unpad(padded).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FeishuDecryptError("decrypted payload is not valid UTF-8") from exc


def decrypt_feishu_event_payload(*, encrypt_key: str, encrypted: str) -> dict[str, Any]:
    plaintext = decrypt_feishu_event_string(encrypt_key=encrypt_key, encrypted=encrypted)
    try:
        decoded = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise FeishuDecryptError("decrypted payload is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise FeishuDecryptError("decrypted payload must be a JSON object")
    return decoded


def encrypt_feishu_event_string(*, encrypt_key: str, plaintext: str) -> str:
    """Test helper mirroring Feishu AES-256-CBC envelope (iv + ciphertext)."""
    normalized_key = str(encrypt_key or "").strip()
    if not normalized_key:
        raise ValueError("encrypt_key is required")
    key = hashlib.sha256(normalized_key.encode("utf-8")).digest()
    raw = plaintext.encode("utf-8")
    pad_len = 16 - (len(raw) % 16)
    padded = raw + bytes([pad_len]) * pad_len
    iv = hashlib.sha256(plaintext.encode("utf-8")).digest()[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("utf-8")
