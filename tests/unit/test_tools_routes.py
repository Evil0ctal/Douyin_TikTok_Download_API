"""The published building blocks.

These check the two things a caller of `/tools/sign` can get wrong on their own
side - the User-Agent the signature was computed for, and which parameters come
back - plus that link parsing reports the platform-specific id rather than a
single invented one.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request

from dtk.api.deps import Principal
from dtk.api.routes import tools
from dtk.api.routes.tools import DEFAULT_USER_AGENT
from dtk.core.types import Platform, Scope, UserRole
from dtk.signing import SigningSession, native_signers
from dtk.signing.base import RequestSpec as SigningRequest
from dtk.signing.base import StaticFingerprint
from dtk.urls import identify

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


#: A read key, which is what a caller of these endpoints holds.
PRINCIPAL = Principal(
    user_id=uuid.uuid4(),
    role=UserRole.VIEWER,
    scopes=frozenset({Scope.DOUYIN_READ, Scope.TIKTOK_READ}),
    api_key_id=uuid.uuid4(),
    rate_limit_per_min=None,
)


async def batch(text: str) -> dict[str, Any]:
    """`/tools/parse-batch` called directly. It touches nothing but its argument."""
    request = cast(Request, SimpleNamespace(state=SimpleNamespace(request_id=uuid.uuid4())))
    response = await tools.parse_batch(request, tools.BatchParseRequest(text=text), PRINCIPAL)
    return cast(dict[str, Any], json.loads(bytes(response.body))["data"])


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


# --------------------------------------------------------------------------
# What one pasted line holds
# --------------------------------------------------------------------------
#
# Splitting a paste on whitespace is the obvious thing and it is wrong: the
# input people actually have is a share caption with one link buried in prose.
# The console did exactly that and turned a single Douyin share into four rows,
# three of them nonsense and the fourth carrying the caption glued to the URL.

#: A real Douyin share, written as escapes so this file stays pure ASCII
#: (docs/design/14-i18n.md). One link, wrapped in a caption and a sentence
#: telling the reader to open the app - which is the shape that used to be
#: split into four rows.
DOUYIN_SHARE = (
    "2.84 nqe:/ \u9a91\u767d\u9a6c\u7684\u4e5f\u53ef\u4ee5\u662f\u516c\u4e3b%%"
    "\u767e\u4e07\u8f6c\u573a\u53d8\u8eab https://v.douyin.com/L4FJNR3/ "
    "\u590d\u5236\u6b64\u94fe\u63a5\uff0c\u6253\u5f00Dou\u97f3\u641c\u7d22\uff01"
)


async def test_a_share_caption_yields_the_one_link_inside_it() -> None:
    data = await batch(DOUYIN_SHARE)
    assert data["total"] == 1
    item = data["items"][0]
    assert item["kind"] == "short_link"
    assert item["input"] == "https://v.douyin.com/L4FJNR3/"
    assert item["resource_id"] == "L4FJNR3"


async def test_several_links_on_one_line_are_all_kept() -> None:
    """What splitting was reaching for, without the collateral damage."""
    data = await batch(
        "https://v.douyin.com/L4NpDJ6/ https://www.tiktok.com/t/ZTR9nkkmL/",
    )
    assert [item["resource_id"] for item in data["items"]] == ["L4NpDJ6", "ZTR9nkkmL"]


async def test_a_line_with_no_link_is_still_tried_as_an_id() -> None:
    data = await batch("7156033831819037994")
    assert data["items"][0]["kind"] == "content_id"


async def test_the_same_link_twice_across_lines_is_one_item() -> None:
    """Deduped on the extracted URL, not on the line it was buried in."""
    data = await batch(
        f"{DOUYIN_SHARE}\nhttps://v.douyin.com/L4FJNR3/\n"
        "another caption https://v.douyin.com/L4FJNR3/ trailing words"
    )
    assert data["total"] == 1


async def test_every_shape_the_console_was_given_is_recognised() -> None:
    """The exact paste that produced nine rows and eight failures."""
    data = await batch(
        "\n".join(
            [
                "https://v.douyin.com/L4NpDJ6/",
                "https://www.douyin.com/video/7126745726494821640",
                DOUYIN_SHARE,
                "https://www.tiktok.com/t/ZTR9nkkmL/",
                "https://www.tiktok.com/@evil0ctal/video/7156033831819037994",
                "https://www.douyin.com/jingxuan?modal_id=7660875690212492466",
            ]
        )
    )
    assert data["total"] == 6
    assert all(item["kind"] in {"link", "short_link"} for item in data["items"]), data["items"]
    assert [item["platform"] for item in data["items"]] == [
        "douyin",
        "douyin",
        "douyin",
        "tiktok",
        "tiktok",
        "douyin",
    ]
