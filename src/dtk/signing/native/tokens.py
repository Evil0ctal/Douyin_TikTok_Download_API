"""Guest tokens Douyin and TikTok expect alongside a signature.

Ported from ``crawlers/douyin/web/utils.py`` (``TokenManager``,
``VerifyFpManager``) and ``crawlers/tiktok/web/utils.py`` (``TokenManager``) on
the ``main`` branch (V4, commit 8c98fb7).

What changed from V4
--------------------
* **No config file.** V4 read cookies and endpoint definitions out of
  ``crawlers/*/web/config.yaml`` at import time, which is how a live login
  cookie ended up in the repository. Every endpoint here is a parameter; the
  defaults below hold nothing secret.
* **No client construction.** V4 opened a blocking ``httpx.Client`` per call.
  Every network call here takes an injected ``httpx.AsyncClient`` so the caller
  controls proxy, timeout and identity affinity.
* Randomness and the clock are injected, so output can be reproduced in tests.
* Comments and log events are English; events are dotted names.

Offline seam
------------
:func:`gen_real_ms_token`, :func:`gen_ttwid` and :func:`gen_odin_tt` are the only
functions here that touch the network, and they touch it exactly once, through
the injected client. Everything else is a pure function. The default endpoint
constants are the last known good values; confirming them against a live browser
session is the minting task in docs/design/16-salvage-and-debug.md, not something
that can be settled offline.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Final

import httpx

from dtk.core.errors import SigningFailed, UpstreamChanged
from dtk.core.logging import get_logger

logger = get_logger(__name__)

#: Alphabet V4 drew fake msToken characters from, kept as is so a fake token is
#: indistinguishable in shape from the ones V4 produced.
MS_TOKEN_ALPHABET: Final = "ABCDEFGHIGKLMNOPQRSTUVWXYZabcdefghigklmnopqrstuvwxyz0123456789="

#: Douyin's fake msToken length before the ``==`` suffix.
DOUYIN_MS_TOKEN_LENGTH: Final = 126
#: TikTok's is longer.
TIKTOK_MS_TOKEN_LENGTH: Final = 146

#: Lengths a real Douyin msToken comes back as. Anything else means the endpoint
#: changed shape and the value should not be trusted.
DOUYIN_MS_TOKEN_SIZES: Final[tuple[int, ...]] = (120, 128)

#: Alphabet for the random half of ``verify_fp`` / ``s_v_web_id``.
VERIFY_FP_ALPHABET: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

#: Fixed layout of the 36 character tail of ``verify_fp``: a UUID v4 skeleton.
_VERIFY_FP_SEPARATORS: Final[tuple[int, ...]] = (8, 13, 18, 23)
_VERIFY_FP_VERSION_INDEX: Final = 14
_VERIFY_FP_VARIANT_INDEX: Final = 19
_VERIFY_FP_LENGTH: Final = 36


@dataclass(frozen=True, slots=True)
class MsTokenSpec:
    """Everything the msToken report endpoint needs.

    ``str_data`` is an opaque blob the platform's SDK produces. It is not a
    credential, but it does change when the SDK changes, so it lives in settings
    rather than in code.
    """

    url: str
    magic: int
    version: int
    data_type: int
    str_data: str
    user_agent: str

    def payload(self, *, timestamp_ms: int | None = None) -> bytes:
        body = {
            "magic": self.magic,
            "version": self.version,
            "dataType": self.data_type,
            "strData": self.str_data,
            "tspFromClient": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
        }
        return json.dumps(body).encode("utf-8")


@dataclass(frozen=True, slots=True)
class TtwidSpec:
    """Registration call that mints a ``ttwid`` cookie."""

    url: str
    data: str


#: Public registration endpoint. Same host for both platforms; the body differs.
TTWID_REGISTER_URL: Final = "https://ttwid.bytedance.com/ttwid/union/register/"

#: Douyin guest ttwid registration. Contains no credential.
DOUYIN_TTWID: Final = TtwidSpec(
    url=TTWID_REGISTER_URL,
    data=json.dumps(
        {
            "region": "cn",
            "aid": 1768,
            "needFid": False,
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https",
            "union": True,
        }
    ),
)

#: Requesting the TikTok home page is enough to be handed an ``odin_tt`` cookie.
TIKTOK_ODIN_TT_URL: Final = "https://www.tiktok.com"


def gen_false_ms_token(
    length: int = DOUYIN_MS_TOKEN_LENGTH,
    *,
    rng: random.Random | None = None,
    suffix: str = "==",
) -> str:
    """A locally generated msToken.

    The platform accepts it for many endpoints and rejects it for some. V4 used
    it as the fallback whenever the real endpoint failed, and so do we, but the
    caller is told which one it got so the choice shows up in the request log
    rather than silently degrading.
    """
    source = rng or random.Random()
    return "".join(source.choice(MS_TOKEN_ALPHABET) for _ in range(length)) + suffix


async def gen_real_ms_token(
    client: httpx.AsyncClient,
    spec: MsTokenSpec,
    *,
    expected_sizes: tuple[int, ...] = DOUYIN_MS_TOKEN_SIZES,
    timestamp_ms: int | None = None,
) -> str:
    """Exchange the SDK report payload for a real msToken cookie.

    Raises:
        SigningFailed: the endpoint could not be reached or refused the payload.
        UpstreamChanged: the response carried no msToken, or one of an
            unexpected length.
    """
    headers = {"User-Agent": spec.user_agent, "Content-Type": "application/json"}
    try:
        response = await client.post(
            spec.url, content=spec.payload(timestamp_ms=timestamp_ms), headers=headers
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("identity.mstoken.request_failed", url=spec.url, error=str(exc))
        raise SigningFailed(f"msToken endpoint unreachable: {exc}") from exc

    token = response.cookies.get("msToken")
    if token is None:
        raise UpstreamChanged("cookies.msToken")
    if expected_sizes and len(token) not in expected_sizes:
        logger.warning("identity.mstoken.unexpected_size", size=len(token))
        raise UpstreamChanged("cookies.msToken.length")
    return token


async def gen_ms_token(
    client: httpx.AsyncClient,
    spec: MsTokenSpec,
    *,
    expected_sizes: tuple[int, ...] = DOUYIN_MS_TOKEN_SIZES,
    fallback_length: int = DOUYIN_MS_TOKEN_LENGTH,
    rng: random.Random | None = None,
) -> tuple[str, bool]:
    """Real msToken when the endpoint cooperates, fake one when it does not.

    Returns:
        The token and whether it is real. V4 returned only the token, so a
        degraded identity looked exactly like a healthy one.
    """
    try:
        return await gen_real_ms_token(client, spec, expected_sizes=expected_sizes), True
    except (SigningFailed, UpstreamChanged):
        logger.info("identity.mstoken.fallback")
        return gen_false_ms_token(fallback_length, rng=rng), False


async def gen_ttwid(
    client: httpx.AsyncClient,
    spec: TtwidSpec = DOUYIN_TTWID,
    *,
    cookie: str | None = None,
) -> str:
    """Register a guest ``ttwid``.

    ``cookie`` is only needed on TikTok, where registration is bound to an
    already issued cookie jar.

    Raises:
        SigningFailed: the endpoint could not be reached.
        UpstreamChanged: the response carried no ttwid.
    """
    headers = {"Content-Type": "text/plain"}
    if cookie is not None:
        headers["Cookie"] = cookie
    try:
        response = await client.post(spec.url, content=spec.data, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("identity.ttwid.request_failed", url=spec.url, error=str(exc))
        raise SigningFailed(f"ttwid endpoint unreachable: {exc}") from exc

    ttwid = response.cookies.get("ttwid")
    if ttwid is None:
        raise UpstreamChanged("cookies.ttwid")
    return ttwid


async def gen_odin_tt(
    client: httpx.AsyncClient,
    url: str = TIKTOK_ODIN_TT_URL,
) -> str:
    """Fetch the TikTok home page for its ``odin_tt`` cookie.

    Raises:
        SigningFailed: the page could not be reached.
        UpstreamChanged: the response carried no odin_tt.
    """
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("identity.odin_tt.request_failed", url=url, error=str(exc))
        raise SigningFailed(f"odin_tt endpoint unreachable: {exc}") from exc

    odin_tt = response.cookies.get("odin_tt")
    if odin_tt is None:
        raise UpstreamChanged("cookies.odin_tt")
    return odin_tt


def _to_base36(value: int) -> str:
    if value <= 0:
        return "0"
    digits: list[str] = []
    while value > 0:
        remainder = value % 36
        digits.append(str(remainder) if remainder < 10 else chr(ord("a") + remainder - 10))
        value //= 36
    return "".join(reversed(digits))


def gen_verify_fp(*, now_ms: int | None = None, rng: random.Random | None = None) -> str:
    """Generate a ``verify_fp``.

    Shape: ``verify_<base36 milliseconds>_<uuid4-like 36 characters>``, where the
    tail keeps a UUID v4 skeleton - underscores at 8/13/18/23, ``4`` at 14, and
    the variant nibble at 19.
    """
    source = rng or random.Random()
    milliseconds = now_ms if now_ms is not None else round(time.time() * 1000)
    alphabet_size = len(VERIFY_FP_ALPHABET)

    tail: list[str] = [""] * _VERIFY_FP_LENGTH
    for index in _VERIFY_FP_SEPARATORS:
        tail[index] = "_"
    tail[_VERIFY_FP_VERSION_INDEX] = "4"
    for index in range(_VERIFY_FP_LENGTH):
        if tail[index]:
            continue
        position = int(source.random() * alphabet_size)
        if index == _VERIFY_FP_VARIANT_INDEX:
            position = 3 & position | 8
        tail[index] = VERIFY_FP_ALPHABET[position]

    return "verify_" + _to_base36(milliseconds) + "_" + "".join(tail)


def gen_s_v_web_id(*, now_ms: int | None = None, rng: random.Random | None = None) -> str:
    """``s_v_web_id`` has the same shape as ``verify_fp`` and is minted the same way."""
    return gen_verify_fp(now_ms=now_ms, rng=rng)


__all__ = [
    "DOUYIN_MS_TOKEN_LENGTH",
    "DOUYIN_MS_TOKEN_SIZES",
    "DOUYIN_TTWID",
    "MS_TOKEN_ALPHABET",
    "TIKTOK_MS_TOKEN_LENGTH",
    "TIKTOK_ODIN_TT_URL",
    "TTWID_REGISTER_URL",
    "VERIFY_FP_ALPHABET",
    "MsTokenSpec",
    "TtwidSpec",
    "gen_false_ms_token",
    "gen_ms_token",
    "gen_odin_tt",
    "gen_real_ms_token",
    "gen_s_v_web_id",
    "gen_ttwid",
    "gen_verify_fp",
]
