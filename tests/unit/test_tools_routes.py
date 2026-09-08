"""The published building blocks.

These check the two things a caller of `/tools/sign` can get wrong on their own
side - the User-Agent the signature was computed for, and which parameters come
back - plus that link parsing reports the platform-specific id rather than a
single invented one.
"""

from __future__ import annotations

import pytest

from dtk.api.routes.tools import DEFAULT_USER_AGENT
from dtk.core.types import Platform
from dtk.signing import SigningSession, native_signers
from dtk.signing.base import RequestSpec as SigningRequest
from dtk.signing.base import StaticFingerprint
from dtk.urls import identify

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _sign(platform: Platform, url: str, user_agent: str, ms_token: str = "") -> dict:
    base, _, query = url.partition("?")
    params = dict(pair.partition("=")[::2] for pair in query.split("&") if pair)
    signed = await native_signers()[platform].sign(
        SigningRequest(method="GET", url=base, params=params),
        StaticFingerprint(user_agent=user_agent, browser_platform="Win32"),
        SigningSession(cookies={"msToken": ms_token} if ms_token else {}),
    )
    return dict(signed.params)


async def test_tiktok_signing_returns_the_four_parameters_the_sdk_sends() -> None:
    params = await _sign(
        Platform.TIKTOK,
        "https://www.tiktok.com/api/user/detail/?aid=1988&uniqueId=someone",
        DEFAULT_USER_AGENT,
    )
    assert set(params) == {"X-Dynosaur", "msToken", "X-Bogus", "X-Gnarly"}


async def test_douyin_signing_returns_its_own_parameter_set() -> None:
    params = await _sign(
        Platform.DOUYIN,
        "https://www.douyin.com/aweme/v1/web/aweme/detail/?aid=6383&aweme_id=7",
        DEFAULT_USER_AGENT,
    )
    assert "a_bogus" in params


async def test_the_signature_follows_the_user_agent() -> None:
    """Which is why the endpoint echoes back the one it used.

    A caller that signs with the default and sends with their own browser's UA
    has a signature for a visitor that does not exist.
    """
    one = await _sign(
        Platform.TIKTOK, "https://www.tiktok.com/api/user/detail/?aid=1988", DEFAULT_USER_AGENT
    )
    other = await _sign(
        Platform.TIKTOK,
        "https://www.tiktok.com/api/user/detail/?aid=1988",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
    )
    assert one["X-Dynosaur"] != other["X-Dynosaur"]


# --------------------------------------------------------------------------
# Link parsing
# --------------------------------------------------------------------------


def test_a_tiktok_profile_link_yields_the_handle() -> None:
    """Because that is what TikTok's user-detail call accepts."""
    kind = identify("https://www.tiktok.com/@chainzone_led")

    assert kind.platform is Platform.TIKTOK
    assert kind.resource_id == "chainzone_led"
    assert kind.handle == "chainzone_led"


def test_a_douyin_profile_link_yields_the_sec_user_id() -> None:
    kind = identify("https://www.douyin.com/user/MS4wLjABAAAAexample")

    assert kind.platform is Platform.DOUYIN
    assert kind.resource_id == "MS4wLjABAAAAexample"


def test_a_short_link_reports_that_it_needs_following() -> None:
    """It cannot be resolved without a network call, so parse-url says so."""
    kind = identify("https://v.douyin.com/abcdef/")

    assert kind.needs_expansion is True


def test_an_unrelated_host_is_not_allowed() -> None:
    assert identify("https://example.com/video/1").allowed is False
