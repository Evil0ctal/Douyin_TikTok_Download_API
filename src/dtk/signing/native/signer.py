"""The pure Python signer: cheap, incomplete, and no longer the default.

This is V4's ``BogusManager`` rewritten against the ``Signer`` protocol. It costs
microseconds and has no external dependency. It is NOT the default - ``signing.mode``
ships as ``rpc`` - and the docstring said the opposite here for a while, which is
worth correcting rather than deleting: the reason is measured, not architectural.

Measured against live Douyin on 2026-09-08, one identity, twenty requests:

* This signer's ``a_bogus`` is ACCEPTED. Eight of twenty returned the full
  payload, so the ported algorithm is not stale in the sense of computing a
  wrong signature - a wrong one would have returned none.
* The other twelve were refused with ``403 Uifid Not Found``. Douyin demands
  ``uifid`` on a random majority of requests, and this signer cannot produce it.
  Supplying the identity's ``UIFID_TEMP`` cookie satisfies that check and the
  refusal simply moves to ``Signature Not Found`` - ``x-secsdk-web-signature``,
  which has no port either. The browser path scored twenty of twenty.

So the gap is not the algorithm, it is the parameters around it: of the six
Douyin sends, this signer produces one. Reviving it means porting
``x-secsdk-web-signature`` and deriving ``uifid``, not rewriting A-Bogus.

Two things it does that V4 did not:

* The signature follows the identity's fingerprint. V4 signed every request as
  one hard-coded Chrome 90 on a MacIntel screen no matter which identity sent it;
  a fingerprint that disagrees with itself across layers is the cheapest way to
  be spotted.
* A missing ``msToken`` is filled in locally rather than left out, and the caller
  can turn that off.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from urllib.parse import quote

from dtk.core.errors import SigningFailed
from dtk.core.logging import get_logger
from dtk.core.types import Platform
from dtk.signing.base import (
    DEFAULT_ALGORITHMS,
    MS_TOKEN_PARAM,
    SIGNER_NATIVE,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    SignerHealth,
    SigningFingerprint,
    SigningSession,
    encode_query,
)
from dtk.signing.native.abogus import (
    DEFAULT_BROWSER_INFO,
    ABogus,
    build_browser_info,
)
from dtk.signing.native.tokens import (
    DOUYIN_MS_TOKEN_LENGTH,
    TIKTOK_MS_TOKEN_LENGTH,
    gen_false_ms_token,
)
from dtk.signing.native.xbogus import XBogus

logger = get_logger(__name__)

#: Algorithms this signer can produce. ``_signature`` needs the site's own
#: JavaScript and is browser-rpc only.
NATIVE_ALGORITHMS: frozenset[SignatureAlgorithm] = frozenset(
    {SignatureAlgorithm.A_BOGUS, SignatureAlgorithm.X_BOGUS}
)

#: Fake msToken length per platform.
MS_TOKEN_LENGTHS: Mapping[Platform, int] = {
    Platform.DOUYIN: DOUYIN_MS_TOKEN_LENGTH,
    Platform.TIKTOK: TIKTOK_MS_TOKEN_LENGTH,
}

#: Pixels a desktop Chrome window spends on its own chrome. Used to derive the
#: inner viewport from the screen size the fingerprint reports.
BROWSER_CHROME_PX = 122


def browser_info_for(fingerprint: SigningFingerprint) -> str:
    """Build the ``navigator`` geometry string A-Bogus hashes.

    Falls back to V4's constant when the fingerprint does not carry a screen
    size: a plausible default beats a randomised one, because a value that
    changes per request is itself a signal.
    """
    width = fingerprint.screen_width
    height = fingerprint.screen_height
    platform = fingerprint.browser_platform
    if width is None or height is None or not platform:
        return DEFAULT_BROWSER_INFO
    return build_browser_info(
        inner_width=width,
        inner_height=max(height - BROWSER_CHROME_PX, 1),
        outer_width=width,
        outer_height=height,
        platform=platform,
    )


class NativeSigner:
    """Signs with the ported algorithms. Stateless apart from its configuration."""

    name = SIGNER_NATIVE

    def __init__(
        self,
        platform: Platform,
        *,
        algorithm: SignatureAlgorithm | None = None,
        fill_ms_token: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        resolved = algorithm or DEFAULT_ALGORITHMS[platform]
        if resolved not in NATIVE_ALGORITHMS:
            raise SigningFailed(f"{resolved.value} has no native implementation")
        self.platform = platform
        self.algorithm = resolved
        self.fill_ms_token = fill_ms_token
        self._rng = rng or random.Random()

    async def sign(
        self,
        spec: RequestSpec,
        identity_fingerprint: SigningFingerprint,
        session: SigningSession | None = None,
    ) -> SignedParams:
        """Return the query string to send, signature appended.

        ``session`` is accepted and ignored. The native algorithms compute from
        the query and the User-Agent alone, so there is nothing here for a
        cookie jar to change - unlike ``RpcSigner``, where the jar decides what
        the browser signs. Taking the argument keeps one ``Signer`` protocol
        rather than two.
        """
        params = dict(spec.params or {})
        added: dict[str, str] = {}

        if self.fill_ms_token and not params.get(MS_TOKEN_PARAM):
            token = gen_false_ms_token(
                MS_TOKEN_LENGTHS.get(self.platform, DOUYIN_MS_TOKEN_LENGTH), rng=self._rng
            )
            params[MS_TOKEN_PARAM] = token
            added[MS_TOKEN_PARAM] = token

        user_agent = identity_fingerprint.user_agent
        if not user_agent:
            raise SigningFailed("fingerprint carries no user agent")

        try:
            if self.algorithm is SignatureAlgorithm.A_BOGUS:
                query, signature = self._sign_a_bogus(spec, params, identity_fingerprint)
            else:
                query, signature = self._sign_x_bogus(spec, params, identity_fingerprint)
        except (ValueError, TypeError, LookupError) as exc:
            # The ported algorithms raise plain built-ins on inputs they cannot
            # handle - a query too short for X-Bogus to hex-decode, a User-Agent
            # outside Latin-1. They have to become SigningFailed at this
            # boundary: SignerRegistry only falls back to browser-rpc on a
            # DtkError, and the API layer only maps DtkError to an error code.
            logger.warning(
                "signing.native.failed",
                platform=self.platform.value,
                endpoint=spec.endpoint,
                algorithm=self.algorithm.value,
                error=str(exc),
            )
            raise SigningFailed(f"{self.algorithm.value} could not be computed: {exc}") from exc

        added[self.algorithm.value] = signature
        logger.debug(
            "signing.native.signed",
            platform=self.platform.value,
            endpoint=spec.endpoint,
            algorithm=self.algorithm.value,
        )
        return SignedParams(
            query=query,
            params=added,
            signer=self.name,
            algorithm=self.algorithm,
        )

    def _sign_a_bogus(
        self,
        spec: RequestSpec,
        params: Mapping[str, str],
        fingerprint: SigningFingerprint,
    ) -> tuple[str, str]:
        # The value itself has to be percent-encoded on the way back in: it can
        # contain + and /.
        query = encode_query(params, SignatureAlgorithm.A_BOGUS)
        bogus = ABogus(
            fingerprint.user_agent,
            browser_info=browser_info_for(fingerprint),
            rng=self._rng,
        ).get_value(query, method=spec.method)
        return f"{query}&{SignatureAlgorithm.A_BOGUS.value}={quote(bogus, safe='')}", bogus

    def _sign_x_bogus(
        self,
        spec: RequestSpec,
        params: Mapping[str, str],
        fingerprint: SigningFingerprint,
    ) -> tuple[str, str]:
        query = encode_query(params, SignatureAlgorithm.X_BOGUS)
        bogus = XBogus(fingerprint.user_agent).sign(query)
        return f"{query}&{SignatureAlgorithm.X_BOGUS.value}={bogus}", bogus

    async def health(self) -> SignerHealth:
        """Always healthy: a pure function has nothing to be unhealthy about.

        Whether the algorithm still matches the platform is a different question,
        and the shadow comparison in :mod:`dtk.signing.registry` is what answers
        it.
        """
        return SignerHealth(
            signer=self.name,
            healthy=True,
            detail=f"{self.platform.value}:{self.algorithm.value}",
        )


def native_signers(
    *, rng: random.Random | None = None, fill_ms_token: bool = True
) -> dict[Platform, NativeSigner]:
    """One native signer per supported platform, ready for the registry."""
    return {
        platform: NativeSigner(platform, rng=rng, fill_ms_token=fill_ms_token)
        for platform in DEFAULT_ALGORITHMS
    }


__all__ = [
    "BROWSER_CHROME_PX",
    "DEFAULT_ALGORITHMS",
    "MS_TOKEN_PARAM",
    "NATIVE_ALGORITHMS",
    "NativeSigner",
    "browser_info_for",
    "native_signers",
]
