"""The browser-rpc signer: fallback, and the yardstick the native path is held to.

browser-rpc runs CloakBrowser with the platform's own JavaScript loaded in a warm
page, so it follows the platform automatically when the algorithm changes. It
costs hundreds of milliseconds, which is why it is the fallback and not the
default (docs/design/04-transport-signing.md).

Wire contract, from doc 04::

    POST /rpc/sign    {platform, method, url, query, params, user_agent}
                    -> {a_bogus, x_bogus, ms_token, signature, ...}
    GET  /rpc/health -> {warm_contexts, backend_version, uptime}

``query`` is this client's addition to the documented shape: it is the exact
byte sequence to sign. Handing the browser a parameter map instead would let the
two sides disagree about ordering and escaping, and a signature over almost the
right bytes is simply a wrong signature. It is built by
:func:`dtk.signing.base.encode_query`, the same function ``NativeSigner`` uses,
so a request signed either way goes out over the wire byte for byte identical.

Offline seam
------------
The RPC service is the external dependency of this module. Everything else -
request shaping, response mapping, health interpretation, error classification -
is exercised by tests against a stub transport.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from dtk.core.errors import SigningFailed
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.signing.base import (
    DEFAULT_ALGORITHMS,
    SIGNER_BROWSER,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    SignerHealth,
    SigningFingerprint,
    encode_query,
)

logger = get_logger(__name__)

SIGN_PATH = "/rpc/sign"
HEALTH_PATH = "/rpc/health"

#: Response field to the query parameter it becomes, most specific first. The
#: first match decides which algorithm the response is reported as.
RESPONSE_FIELDS: Mapping[str, SignatureAlgorithm] = {
    "a_bogus": SignatureAlgorithm.A_BOGUS,
    "x_bogus": SignatureAlgorithm.X_BOGUS,
    "signature": SignatureAlgorithm.SIGNATURE,
}

#: Extra fields copied into the query untouched when the browser returns them.
PASSTHROUGH_FIELDS: Mapping[str, str] = {"ms_token": "msToken"}

#: Parameters whose value is percent-encoded on the way into the URL. X-Bogus
#: and msToken go in raw, which is how both platforms' own pages send them.
ENCODED_PARAMS: frozenset[str] = frozenset(
    {SignatureAlgorithm.A_BOGUS.value, SignatureAlgorithm.SIGNATURE.value}
)


class RpcSigner:
    """Signs by asking browser-rpc to run the platform's own code."""

    name = SIGNER_BROWSER

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        platform: Platform | None = None,
        algorithm: SignatureAlgorithm | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = client
        self.base_url = base_url.rstrip("/")
        #: Overrides the platform inferred from the request URL, for hosts the
        #: inference does not know.
        self.platform = platform
        #: Overrides the query encoding implied by the platform. Only the
        #: encoding: which signature the browser actually returns is up to the
        #: browser, and is read back off the response.
        self.algorithm = algorithm
        self.timeout = timeout

    async def sign(
        self, spec: RequestSpec, identity_fingerprint: SigningFingerprint
    ) -> SignedParams:
        platform = self.platform or spec.platform
        if platform is None:
            raise SigningFailed(f"cannot tell which platform {spec.url} belongs to")

        params = dict(spec.params or {})
        # The same encoding NativeSigner would have used, so that whichever
        # signer handles a request the platform receives the same bytes and the
        # shadow comparison compares signatures over the same input.
        query = encode_query(
            params, self.algorithm or DEFAULT_ALGORITHMS.get(platform, SignatureAlgorithm.A_BOGUS)
        )
        payload = {
            "platform": platform.value,
            "method": spec.method,
            "url": spec.url,
            "query": query,
            "params": params,
            "user_agent": identity_fingerprint.user_agent,
        }

        started = time.monotonic()
        try:
            response = await self._client.post(
                f"{self.base_url}{SIGN_PATH}", json=payload, timeout=self.timeout
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            logger.warning("signing.rpc.failed", endpoint=spec.endpoint, error=str(exc))
            raise SigningFailed(f"browser-rpc unreachable: {exc}") from exc
        except ValueError as exc:
            logger.warning("signing.rpc.malformed", endpoint=spec.endpoint, error=str(exc))
            raise SigningFailed("browser-rpc returned a non-JSON body") from exc

        if not isinstance(body, dict):
            raise SigningFailed("browser-rpc returned a non-object body")

        signed = self._build(query, params, body)
        logger.info(
            "signing.rpc.signed",
            endpoint=spec.endpoint,
            algorithm=signed.algorithm.value,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return signed

    @staticmethod
    def _build(query: str, params: Mapping[str, str], body: Mapping[str, Any]) -> SignedParams:
        """Turn an RPC response into the query string to send."""
        added: dict[str, str] = {}
        for field, param in PASSTHROUGH_FIELDS.items():
            value = body.get(field)
            # A parameter the caller already sent is inside the signed query.
            # Appending the browser's copy as well would put it in the URL twice
            # and hand the platform a query the signature does not cover.
            if value and param not in params:
                added[param] = str(value)

        algorithm: SignatureAlgorithm | None = None
        signature: str | None = None
        for field, candidate in RESPONSE_FIELDS.items():
            value = body.get(field)
            if value:
                algorithm = candidate
                signature = str(value)
                break
        if algorithm is None or signature is None:
            raise SigningFailed("browser-rpc returned no signature")

        added[algorithm.value] = signature

        parts = [query] if query else []
        for param, value in added.items():
            parts.append(f"{param}={quote(value, safe='') if param in ENCODED_PARAMS else value}")

        return SignedParams(
            query="&".join(parts),
            params=added,
            signer=SIGNER_BROWSER,
            algorithm=algorithm,
        )

    async def health(self) -> SignerHealth:
        """Probe browser-rpc. Never raises: an unhealthy signer is an answer."""
        started = time.monotonic()
        try:
            response = await self._client.get(f"{self.base_url}{HEALTH_PATH}", timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("signing.rpc.health_failed", error=str(exc))
            return SignerHealth(signer=self.name, healthy=False, detail=str(exc))

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        if not isinstance(body, dict):
            return SignerHealth(
                signer=self.name,
                healthy=False,
                detail="health endpoint returned a non-object body",
                latency_ms=latency_ms,
            )

        warm = body.get("warm_contexts")
        warm_contexts = warm if isinstance(warm, int) and not isinstance(warm, bool) else None
        uptime = body.get("uptime")
        # ``isinstance(True, int)`` is True; a boolean is not a duration.
        if isinstance(uptime, bool):
            uptime = None
        starved = warm_contexts is not None and warm_contexts <= 0
        return SignerHealth(
            signer=self.name,
            healthy=not starved,
            detail="no warm signing context" if starved else None,
            latency_ms=latency_ms,
            warm_contexts=warm_contexts,
            backend_version=str(body["backend_version"]) if body.get("backend_version") else None,
            uptime_seconds=float(uptime) if isinstance(uptime, int | float) else None,
        )


__all__ = ["HEALTH_PATH", "SIGN_PATH", "RpcSigner"]
