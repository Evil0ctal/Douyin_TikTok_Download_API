"""Which requests an endpoint says a guest identity cannot serve in full.

The scheduler routes on this, so a wrong answer here either spends a guest on a
page Douyin will refuse it (#782) or serves an author feed missing its newest
posts without saying so (#783).
"""

from __future__ import annotations

import pytest

from dtk.core.types import LoginNeed, Platform
from dtk.platforms import get_adapter
from dtk.platforms.base import EndpointSpec, is_later_page


@pytest.mark.parametrize("cursor", [None, "", "  ", "0", 0, " 0 "])
def test_a_missing_or_zero_cursor_is_the_first_page(cursor):
    assert is_later_page(cursor) is False


@pytest.mark.parametrize("cursor", ["1787228699000", "20", 20, "eyJvZmZzZXQiOjIwfQ=="])
def test_any_other_cursor_is_a_later_page(cursor):
    assert is_later_page(cursor) is True


def test_an_endpoint_says_nothing_unless_measured():
    spec = EndpointSpec(name="x.y", path="/y")

    assert spec.login_for({}) is LoginNeed.NONE
    assert spec.login_for({"cursor": "9"}) is LoginNeed.NONE


def test_paging_falls_back_to_the_first_page_rule():
    spec = EndpointSpec(name="x.y", path="/y", login=LoginNeed.REQUIRED)

    assert spec.login_for({"cursor": "9"}) is LoginNeed.REQUIRED


def test_douyin_author_posts_prefers_a_login_and_pages_only_with_one():
    spec = get_adapter(Platform.DOUYIN).endpoints.specs["douyin.author_posts"]

    assert spec.login_for({"sec_user_id": "a"}) is LoginNeed.PREFERRED
    assert spec.login_for({"sec_user_id": "a", "cursor": "1787228699000"}) is LoginNeed.REQUIRED


def test_douyin_author_likes_is_served_only_to_a_login():
    """Measured 2026-10-20 on one author whose likes are public: a logged-in
    identity got 20 posts and a next page, three guests got the zero-byte
    answer Douyin gives a private list. Left at NONE, every unpinned lookup
    went to a guest and came back "does not exist" for every author."""
    spec = get_adapter(Platform.DOUYIN).endpoints.specs["douyin.author_likes"]

    assert spec.login_for({"sec_user_id": "a"}) is LoginNeed.REQUIRED
    assert spec.login_for({"sec_user_id": "a", "cursor": "1792223926000"}) is LoginNeed.REQUIRED


@pytest.mark.parametrize("endpoint", ["douyin.comments", "douyin.content_detail"])
def test_endpoints_a_guest_reads_in_full_are_left_alone(endpoint):
    """Measured on the same day: comments page fine for a guest. Marking them
    would route every comment read onto the operator's few logged-in accounts."""
    spec = get_adapter(Platform.DOUYIN).endpoints.specs[endpoint]

    assert spec.login_for({"cursor": "20"}) is LoginNeed.NONE


def test_no_tiktok_endpoint_asks_for_a_login():
    """Nothing about TikTok was measured, so nothing about it changes."""
    specs = get_adapter(Platform.TIKTOK).endpoints.specs.values()

    assert {spec.login for spec in specs} == {LoginNeed.NONE}
    assert {spec.paging_login for spec in specs} == {None}
