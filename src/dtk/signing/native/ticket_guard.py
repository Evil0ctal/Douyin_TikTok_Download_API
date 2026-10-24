"""TikTok Web ``tt-ticket-guard`` (v2) ECDSA P-256 request signing and AES-GCM ticket decryption.

TikTok Web protects state-mutating endpoints (such as ``/api/commit/item/digg/``,
``/api/item/collect/``, ``/api/commit/follow/user/``, ``/api/follow/``) with
client-held ECDSA P-256 keys stored in ``localStorage`` under:

* ``security-sdk/s_sdk_crypt_sdk`` (PEM/DER PKCS#8 private key and SPKI public key)
* ``security-sdk/s_sdk_sign_data_key/tt_fetch`` (``ts_sign`` and AES-GCM encrypted ``encrypt_ticket``)
* ``security-sdk/s_sdk_server_cert_key`` (server ECDH public key used to wrap ``encrypt_ticket``)

For every guarded request, the client signs::

    ticket=<hex_ticket>&path=<url_path>&timestamp=<unix_seconds>

with ECDSA P-256 SHA-256 (ASN.1 DER signature, base64-encoded as ``req_sign``)
and attaches five HTTP headers:

* ``tt-ticket-guard-client-data``
* ``tt-ticket-guard-public-key``
* ``tt-ticket-guard-version: 2``
* ``tt-ticket-guard-iteration-version: 0``
* ``tt-ticket-guard-web-version: 1``
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_SERVER_PUB_B64: Final = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE/26f23653226c0c803d22246095"
    "9194774657104215762739278478122468548367764784003382756453w=="
)

TICKET_GUARD_VERSION: Final = "2"
TICKET_GUARD_ITERATION_VERSION: Final = "0"
TICKET_GUARD_WEB_VERSION: Final = "1"
REQ_CONTENT_FIELDS: Final = "ticket,path,timestamp"

_PEM_BODY_RE = re.compile(r"-----BEGIN [^-]+-----\s*(.*?)\s*-----END [^-]+-----", re.DOTALL)


def _strip_pem(pem_or_b64: str) -> str:
    m = _PEM_BODY_RE.search(pem_or_b64)
    body = m.group(1) if m else pem_or_b64
    return re.sub(r"\s+", "", body)


def derive_ticket_guard_iv() -> bytes:
    """Derives the 12-byte AES-GCM IV via PBKDF2-HMAC-SHA256('tt-ticket-guard-iv', 'secure-salt', 1000, 16)."""
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        b"tt-ticket-guard-iv",
        b"secure-salt",
        1000,
        dklen=16,
    )
    return derived[:12]


def decrypt_ticket(
    encrypt_ticket_b64: str,
    private_key_b64: str,
    *,
    server_pub_b64: str = DEFAULT_SERVER_PUB_B64,
) -> str:
    """Decrypts ``encrypt_ticket`` from ``s_sdk_sign_data_key/tt_fetch`` into the plaintext hex ``ticket``."""
    priv_der = base64.b64decode(_strip_pem(private_key_b64))
    priv_key = serialization.load_der_private_key(priv_der, password=None)
    if not isinstance(priv_key, ec.EllipticCurvePrivateKey):
        raise ValueError("Expected an EC P-256 private key")

    srv_der = base64.b64decode(_strip_pem(server_pub_b64))
    srv_pub = serialization.load_der_public_key(srv_der)
    if not isinstance(srv_pub, ec.EllipticCurvePublicKey):
        raise ValueError("Expected an EC P-256 server public key")

    shared_secret = priv_key.exchange(ec.ECDH(), srv_pub)
    aes_key = shared_secret[:16]
    iv = derive_ticket_guard_iv()
    ciphertext = base64.b64decode(encrypt_ticket_b64)
    plaintext = AESGCM(aes_key).decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")


def extract_uncompressed_public_key_b64(private_key_b64: str) -> str:
    """Extracts the 65-byte uncompressed ANSI X9.62 point (``0x04 || X || Y``) encoded in base64."""
    priv_der = base64.b64decode(_strip_pem(private_key_b64))
    priv_key = serialization.load_der_private_key(priv_der, password=None)
    pub_bytes = priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.b64encode(pub_bytes).decode("ascii")


@dataclass(frozen=True, slots=True)
class TicketGuardCredentials:
    """Holds a session's ``tt-ticket-guard`` key material and ticket."""

    private_key_b64: str
    ticket: str
    ts_sign: str
    public_key_raw_b64: str = ""

    def resolved_public_key_raw_b64(self) -> str:
        if self.public_key_raw_b64:
            return self.public_key_raw_b64
        return extract_uncompressed_public_key_b64(self.private_key_b64)

    @classmethod
    def from_local_storage(
        cls,
        crypt_sdk_raw: str | Mapping[str, Any],
        sign_data_raw: str | Mapping[str, Any],
        *,
        server_cert_raw: str | Mapping[str, Any] | None = None,
    ) -> TicketGuardCredentials:
        """Constructs :class:`TicketGuardCredentials` directly from browser ``localStorage`` values."""

        def _unwrap(val: str | Mapping[str, Any]) -> dict[str, Any]:
            obj = json.loads(val) if isinstance(val, str) else dict(val)
            if isinstance(obj.get("data"), str):
                return json.loads(obj["data"])
            if isinstance(obj.get("data"), Mapping):
                return dict(obj["data"])
            return obj

        crypt_obj = _unwrap(crypt_sdk_raw)
        sign_obj = _unwrap(sign_data_raw)

        priv_b64 = _strip_pem(str(crypt_obj.get("ec_privateKey") or ""))
        ts_sign = str(sign_obj.get("ts_sign") or "")
        ticket = str(sign_obj.get("ticket") or "")
        if not ticket:
            enc_ticket = str(sign_obj.get("encrypt_ticket") or "")
            srv_pub = DEFAULT_SERVER_PUB_B64
            if server_cert_raw:
                cert_obj = _unwrap(server_cert_raw)
                if cert_obj.get("server_pub"):
                    srv_pub = _strip_pem(str(cert_obj["server_pub"]))
            ticket = decrypt_ticket(enc_ticket, priv_b64, server_pub_b64=srv_pub)

        pub_raw_b64 = extract_uncompressed_public_key_b64(priv_b64)
        return cls(
            private_key_b64=priv_b64,
            ticket=ticket,
            ts_sign=ts_sign,
            public_key_raw_b64=pub_raw_b64,
        )


def sign_ticket_guard(
    path: str,
    creds: TicketGuardCredentials,
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Generates the five ``tt-ticket-guard-*`` HTTP headers for ``path``."""
    ts = int(time.time()) if timestamp is None else int(timestamp)
    priv_der = base64.b64decode(_strip_pem(creds.private_key_b64))
    priv_key = serialization.load_der_private_key(priv_der, password=None)
    if not isinstance(priv_key, ec.EllipticCurvePrivateKey):
        raise ValueError("Expected an EC P-256 private key")

    message = f"ticket={creds.ticket}&path={path}&timestamp={ts}".encode()
    der_sig = priv_key.sign(message, ec.ECDSA(hashes.SHA256()))
    req_sign = base64.b64encode(der_sig).decode("ascii")

    client_data_json = json.dumps(
        {
            "ts_sign": creds.ts_sign,
            "req_content": REQ_CONTENT_FIELDS,
            "req_sign": req_sign,
            "timestamp": ts,
        },
        separators=(",", ":"),
    )
    client_data_b64 = base64.b64encode(client_data_json.encode("utf-8")).decode("ascii")

    return {
        "tt-ticket-guard-client-data": client_data_b64,
        "tt-ticket-guard-public-key": creds.resolved_public_key_raw_b64(),
        "tt-ticket-guard-version": TICKET_GUARD_VERSION,
        "tt-ticket-guard-iteration-version": TICKET_GUARD_ITERATION_VERSION,
        "tt-ticket-guard-web-version": TICKET_GUARD_WEB_VERSION,
    }


__all__ = [
    "DEFAULT_SERVER_PUB_B64",
    "TicketGuardCredentials",
    "decrypt_ticket",
    "derive_ticket_guard_iv",
    "extract_uncompressed_public_key_b64",
    "sign_ticket_guard",
]
