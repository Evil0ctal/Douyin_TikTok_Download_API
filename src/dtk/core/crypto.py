"""Credential encryption at rest.

Cookies and proxy URLs are stored as AES-256-GCM ciphertext. The master key
never reaches the database: it decrypts the database, so storing it there would
be circular. See docs/design/08-security.md and docs/design/10-configuration.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32
MIN_SECRET_LEN = 32


class SecretKeyMissing(RuntimeError):
    """Raised at startup when DTK_SECRET_KEY is absent or too short.

    The process must refuse to start rather than fall back to a default: an
    image that ships with a shared key is an image with no encryption at all.
    """


def derive_key(secret: str) -> bytes:
    if not secret or len(secret) < MIN_SECRET_LEN:
        raise SecretKeyMissing(
            f"DTK_SECRET_KEY must be at least {MIN_SECRET_LEN} characters; "
            "generate one with: openssl rand -base64 48"
        )
    return hashlib.sha256(secret.encode("utf-8")).digest()


class Cipher:
    """AES-256-GCM with the record id bound in as additional authenticated data.

    Binding the id prevents a ciphertext being moved between rows: a blob
    encrypted for identity A will not decrypt under identity B's id.
    """

    def __init__(self, secret: str) -> None:
        self._aead = AESGCM(derive_key(secret))

    def encrypt(self, plaintext: str, *, aad: str = "") -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        blob = self._aead.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return nonce + blob

    def decrypt(self, payload: bytes, *, aad: str = "") -> str:
        if len(payload) <= NONCE_BYTES:
            raise ValueError("ciphertext too short")
        nonce, blob = payload[:NONCE_BYTES], payload[NONCE_BYTES:]
        return self._aead.decrypt(nonce, blob, aad.encode("utf-8")).decode("utf-8")


def new_api_key(prefix_bytes: int = 6, secret_bytes: int = 24) -> tuple[str, str, str]:
    """Return ``(full_key, prefix, sha256_hash)``.

    The full key is shown to the user exactly once. Only the prefix (for display)
    and the hash (for verification) are persisted.
    """
    prefix = secrets.token_hex(prefix_bytes)
    body = base64.urlsafe_b64encode(secrets.token_bytes(secret_bytes)).decode().rstrip("=")
    full = f"dtk_{prefix}_{body}"
    return full, prefix, hash_api_key(full)


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def new_setup_token() -> str:
    return secrets.token_urlsafe(32)


__all__ = [
    "Cipher",
    "SecretKeyMissing",
    "constant_time_equals",
    "derive_key",
    "hash_api_key",
    "new_api_key",
    "new_setup_token",
]
