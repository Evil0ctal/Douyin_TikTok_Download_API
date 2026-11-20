"""``media.access`` and the downloader say the same thing about each CDN.

Two copies of one set of facts: what a parse result tells a caller fetching
the links itself (``media.access``), and what the downloader sends when it
fetches them (``dtk.media.plan``). A Referer or a bound cookie added to one
and not the other is a caller told something the instance does not do, or the
reverse - so they are compared here rather than trusted to stay in step.
"""

from __future__ import annotations

import pytest

from dtk.core.types import Platform
from dtk.media import plan as planner
from dtk.platforms.bilibili import parser as bilibili
from dtk.platforms.douyin import parser as douyin
from dtk.platforms.tiktok import parser as tiktok

ACCESS = {
    Platform.DOUYIN: douyin.ACCESS,
    Platform.TIKTOK: tiktok.VIDEO_ACCESS,
    Platform.BILIBILI: bilibili.ACCESS,
}


def test_every_platform_says_what_its_links_need() -> None:
    assert set(ACCESS) == set(Platform)


@pytest.mark.parametrize("platform", list(Platform))
def test_the_referer_a_caller_is_told_is_the_one_the_downloader_sends(
    platform: Platform,
) -> None:
    assert ACCESS[platform].referer == planner.REFERER[platform]


@pytest.mark.parametrize("platform", list(Platform))
def test_a_video_is_called_session_bound_exactly_when_the_downloader_carries_a_cookie(
    platform: Platform,
) -> None:
    assert ACCESS[platform].session_bound is (platform in planner.BOUND_COOKIES)


def test_tiktok_s_covers_and_slides_are_not_session_bound() -> None:
    """Only the video's links say `tk=tt_chain_token`; the image edges do not."""
    assert tiktok.ACCESS.session_bound is False
    assert tiktok.ACCESS.referer == tiktok.VIDEO_ACCESS.referer


def test_the_downloader_carries_only_the_chain_token() -> None:
    """The single exception to "no credentials, ever" - widening it is a
    decision, and this is where that decision has to be written down."""
    assert {
        platform: [cookie.name for cookie in cookies]
        for platform, cookies in planner.BOUND_COOKIES.items()
    } == {Platform.TIKTOK: ["tt_chain_token"]}
