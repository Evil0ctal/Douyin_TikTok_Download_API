"""The pure Python signer: cheap, complete, and now the default.

This started as V4's ``BogusManager`` rewritten against the ``Signer`` protocol.
It costs microseconds and has no external dependency, and as of 2026-09-08 it
signs every endpoint of both platforms - ``signing.mode`` ships as ``native``, and
the browser's job is to mint identities rather than to sign what they send.

The history is kept because it is the argument for the shape of the thing. This
docstring twice claimed the algorithm was the problem, and both times it was the
parameters around it:

* Douyin. The ported ``a_bogus`` was always ACCEPTED - eight of twenty live
  requests returned the full payload, and a wrong signature would have returned
  none. The other twelve were refused ``403 Uifid Not Found``, then ``Signature
  Not Found`` once the cookie was supplied. Of the six parameters Douyin sends,
  this signer produced one. :mod:`dtk.signing.native.websign` ports the missing
  ``x-secsdk-web-signature`` and reads ``uifid`` off the jar; 24 of 24 live
  requests across all four endpoints then returned data.
* TikTok. :mod:`dtk.signing.native.tiktok_sign` ports ``X-Dynosaur`` and
  ``X-Gnarly``. The algorithm reproduces the platform's own seal byte for byte,
  but two environment constants were taken from a Node harness rather than a
  browser, and only ``/api/post/item_list/`` checks them - so one endpoint
  returned an empty body while the rest looked healthy. A capture fixed it.

Both stories have the same moral, and it is the reason ``fallback_enabled``
exists: a signer that is wrong in one place looks fine everywhere else, and
silent fallback to the other signer is what keeps it looking fine.

Two things it does that V4 did not:

* The signature follows the identity's fingerprint. V4 signed every request as
  one hard-coded Chrome 90 on a MacIntel screen no matter which identity sent it;
  a fingerprint that disagrees with itself across layers is the cheapest way to
  be spotted. TikTok enforces this: the same signed bytes replayed over a Firefox
  TLS profile are refused.
* A missing ``msToken`` is filled in locally rather than left out, and the caller
  can turn that off.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from urllib.parse import quote, unquote

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
from dtk.signing.native import tiktok_sign, websign
from dtk.signing.native.abogus import (
    DEFAULT_BROWSER_INFO,
    ABogus,
    browser_info_from_screen,
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
    """Build the ``navigator``/``screen`` geometry string A-Bogus carries.

    Nine fields, and they are not independent of one another: the viewport sits
    inside the window, the window inside the work area, the work area inside the
    screen. :func:`browser_info_from_screen` derives all of them from the one
    number a fingerprint actually holds, so the set stays self-consistent.

    Falls back to a constant when the fingerprint carries no screen size. A
    plausible default beats a randomised one - a geometry that changes per
    request is itself a signal, and this string travels verbatim inside every
    signature.
    """
    width = fingerprint.screen_width
    height = fingerprint.screen_height
    platform = fingerprint.browser_platform
    if width is None or height is None or not platform:
        return DEFAULT_BROWSER_INFO
    return browser_info_from_screen(width, height, platform)


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

        ``session`` carries the identity's cookie jar, and on Douyin it decides
        whether the request can reach a sign-protected endpoint at all: the
        visitor parameters are copies of cookies (``verifyFp`` and ``fp`` are
        ``s_v_web_id``, ``uifid`` is ``UIFID_TEMP``) and the platform's own
        signature is computed over them. Without a jar this signer produces what
        it always did, which is enough for the endpoints Douyin does not sign.

        ``msToken`` is read from that same jar, and fabricated only when the jar
        has none. Douyin does not appear to care either way - measured on one
        guest identity against ``/aweme/v1/web/aweme/detail/``, which is
        sign-protected, 2026-09-09: a fabricated token returned 100,827 bytes
        and ``status_code: 0``, no token at all 101,950, and a query token
        deliberately contradicting a different one in the Cookie header 101,833.
        All three were complete payloads. That is the *opposite* of TikTok,
        where the same experiment on 2026-09-08 measured a fabricated token at 0
        bytes, which is why :meth:`_sign_tiktok` never invents one.

        The jar is preferred anyway, for the one case the measurement above did
        not cover: an imported jar from a logged-in browser carries a real
        ``msToken``, and a query that contradicts the Cookie header it travels
        with is the incoherence docs 02 and 04 spend their length avoiding. No
        minted Douyin jar carries the cookie, so this changes nothing for one.
        """
        params = dict(spec.params or {})
        added: dict[str, str] = {}

        user_agent = identity_fingerprint.user_agent
        if not user_agent:
            raise SigningFailed("fingerprint carries no user agent")

        if self.platform is Platform.TIKTOK:
            return self._sign_tiktok(spec, params, user_agent, session)

        if self.fill_ms_token and not params.get(MS_TOKEN_PARAM):
            # Caller, then jar, then invention - the same precedence
            # `_sign_tiktok` uses, and the same one the other three visitor
            # values on this path already follow.
            token = (session.cookies if session is not None else {}).get(
                MS_TOKEN_PARAM
            ) or gen_false_ms_token(
                MS_TOKEN_LENGTHS.get(self.platform, DOUYIN_MS_TOKEN_LENGTH), rng=self._rng
            )
            params[MS_TOKEN_PARAM] = token
            added[MS_TOKEN_PARAM] = token

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
        headers: Mapping[str, str] = {}
        if self.platform is Platform.DOUYIN and session is not None:
            query, visitor, headers = self._add_web_signature(query, session)
            added.update(visitor)
        logger.debug(
            "signing.native.signed",
            platform=self.platform.value,
            endpoint=spec.endpoint,
            algorithm=self.algorithm.value,
            web_signed=bool(headers),
        )
        return SignedParams(
            query=query,
            params=added,
            headers=headers,
            signer=self.name,
            algorithm=self.algorithm,
        )

    def _sign_tiktok(
        self,
        spec: RequestSpec,
        params: dict[str, str],
        user_agent: str,
        session: SigningSession | None,
    ) -> SignedParams:
        """TikTok's own four parameters, in the SDK's order.

        See :mod:`dtk.signing.native.tiktok_sign`. Two things differ from every
        other native path and both are the platform's doing:

        * ``msToken`` is not a business parameter. The SDK appends it *between*
          ``X-Dynosaur`` and ``X-Bogus`` and seals it there, so a second copy
          among the business parameters would sign a query TikTok never sends.
          It is taken from the caller's own value first - the shadow comparison
          pins one so both signers sign identical bytes - then from the
          identity's jar, and otherwise left EMPTY. It is never invented, unlike
          every other platform here: TikTok verifies the token when one is
          present but accepts its absence, so a fabricated value turns a request
          that would have worked into one that cannot. Measured on one identity,
          2026-09-08: real token 2545 bytes, no token 2541 bytes, fabricated
          token of the same length 0 bytes and ``tt_orcas_res: 1``.
        * ``X-Bogus`` is the constant ``1``. The real 16-character X-Bogus only
          exists on websocket handshakes, so computing one here would send a
          parameter set TikTok's own page never sends.
        """
        token = params.pop(MS_TOKEN_PARAM, "") or tiktok_sign.pick_ms_token(
            session.cookies if session is not None else None
        )
        query, added = tiktok_sign.sign(
            list(params.items()), user_agent, ms_token=token, rng=self._rng
        )
        logger.debug(
            "signing.native.signed",
            platform=self.platform.value,
            endpoint=spec.endpoint,
            algorithm=self.algorithm.value,
            web_signed=True,
        )
        return SignedParams(
            query=query,
            params=added,
            signer=self.name,
            algorithm=self.algorithm,
        )

    @staticmethod
    def _add_web_signature(
        query: str, session: SigningSession
    ) -> tuple[str, dict[str, str], dict[str, str]]:
        """Append the visitor parameters and Douyin's own signature over them.

        Order matters and is the platform's, not ours: the site sends
        ``...&a_bogus=&verifyFp=&fp=&uifid=&timestamp=&x-secsdk-web-signature=``
        and the signature covers everything before it, re-serialized. Captured
        from a live page on 2026-09-08 and reproduced byte for byte.

        A jar with no ``UIFID_TEMP`` gets the query untouched rather than a
        signature over a missing visitor: the unprotected endpoints still work,
        and the protected ones fail with the platform naming `uifid`, which says
        more than anything invented here would.
        """
        uifid = websign.pick_uifid(session.cookies)
        if not uifid:
            return query, {}, {}
        pairs = [_split_pair(part) for part in query.split("&") if part]
        visitor: dict[str, str] = {}
        verify_fp = (session.cookies or {}).get(websign.VERIFY_FP_COOKIE)
        if verify_fp:
            for name in websign.VERIFY_FP_PARAMS:
                pairs.append((name, verify_fp))
                visitor[name] = verify_fp
        signed_query, signature, headers = websign.sign(pairs, uifid)
        visitor[websign.UIFID_PARAM] = uifid
        visitor[websign.SIGNATURE_PARAM] = signature
        return signed_query, visitor, headers

    def _sign_a_bogus(
        self,
        spec: RequestSpec,
        params: Mapping[str, str],
        fingerprint: SigningFingerprint,
    ) -> tuple[str, str]:
        # The value has to be percent-encoded on the way back in: the s4
        # alphabet contains '/' and '-', and the padding is a literal '='.
        query = encode_query(params, SignatureAlgorithm.A_BOGUS)
        # No method: this revision of A-Bogus hashes the query and the body and
        # not the verb. `spec` still carries it because every other signer here
        # needs it.
        bogus = ABogus(
            fingerprint.user_agent,
            browser_info=browser_info_for(fingerprint),
            rng=self._rng,
        ).get_value(
            query,
            body=(spec.body or b"").decode("utf-8", "replace"),
            content_type=(spec.headers or {}).get("Content-Type", ""),
        )
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


def _split_pair(part: str) -> tuple[str, str]:
    """One ``k=v`` back into a pair, UNQUOTED so it can be re-serialized.

    The query arrives already percent-encoded by the a_bogus layer, and the
    signature is computed over a fresh serialization of the whole thing - so
    every value has to be decoded here and encoded once, consistently, or the
    bytes hashed and the bytes sent differ.
    """
    name, _, value = part.partition("=")
    return unquote(name), unquote(value)


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
