"""Scheduled collection: what a due entry becomes, and how a broken one backs off.

The backoff gets most of the attention because it is the difference between a
watchlist and a scraper: a mistyped id fails every run forever otherwise, and
the pool it burns is the one every interactive request shares.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dtk.core.errors import InvalidParam
from dtk.services import watchlist


def entry(**overrides: object) -> SimpleNamespace:
    fields: dict = {
        "platform": "douyin",
        "kind": "author",
        "target_id": "MS4wLjABAAAAexample",
        "interval_seconds": 3600,
        "consecutive_failures": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestTaskFor:
    def test_an_author_collects_the_post_list(self):
        """Not the profile: the list carries the author record on every item, so
        one request answers both "what is new" and "how many followers now"."""
        endpoint, params = watchlist.task_for(entry())
        assert endpoint == "douyin.author_posts"
        assert params["author_id"] == "MS4wLjABAAAAexample"
        assert params["count"] == 20

    def test_a_post_collects_its_own_detail(self):
        endpoint, params = watchlist.task_for(entry(kind="content", target_id="7408"))
        assert endpoint == "douyin.content_detail"
        assert params == {"content_id": "7408"}

    def test_the_platform_is_part_of_the_endpoint(self):
        endpoint, _ = watchlist.task_for(entry(platform="tiktok"))
        assert endpoint == "tiktok.author_posts"

    def test_every_kind_has_a_capability_and_a_parameter(self):
        # The three tables have to agree or a kind is unroutable at runtime.
        assert set(watchlist.KINDS) == set(watchlist.CAPABILITY) == set(watchlist.PARAMETER)


class TestBackoff:
    def test_the_first_failures_do_not_change_the_schedule(self):
        """One transient network error should not reschedule an entry."""
        assert watchlist.backoff_seconds(entry(consecutive_failures=1)) == 3600

    def test_repeated_failures_double_the_wait(self):
        assert watchlist.backoff_seconds(entry(consecutive_failures=2)) == 7200
        assert watchlist.backoff_seconds(entry(consecutive_failures=3)) == 14400

    def test_the_backoff_has_a_ceiling(self):
        # It keeps retrying: a platform outage ends, and an entry that gave up
        # for good would need someone to notice and re-enable it by hand.
        assert (
            watchlist.backoff_seconds(entry(consecutive_failures=50))
            == watchlist.MAX_BACKOFF_SECONDS
        )

    def test_a_long_interval_is_not_shortened_by_the_ceiling(self):
        # A daily entry that fails stays daily rather than being pulled in to
        # the eight-hour cap.
        assert watchlist.backoff_seconds(entry(interval_seconds=86400)) == 86400


class TestValidation:
    def test_an_interval_under_the_floor_is_refused_by_name(self):
        with pytest.raises(InvalidParam) as caught:
            watchlist.validate("douyin", "author", "x", 30, floor=900)
        assert caught.value.details["minimum_seconds"] == 900

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(InvalidParam):
            watchlist.validate("douyin", "everything", "x", 3600, floor=900)

    def test_an_unknown_platform_is_refused(self):
        with pytest.raises(InvalidParam):
            watchlist.validate("myspace", "author", "x", 3600, floor=900)

    def test_an_empty_target_is_refused(self):
        with pytest.raises(InvalidParam):
            watchlist.validate("douyin", "author", "   ", 3600, floor=900)


class TestLabel:
    def test_reads_a_nickname_off_a_page_of_posts(self):
        result = {
            "data": {
                "items": [{"author": {"nickname": "someone"}}],
                "has_more": False,
            }
        }
        assert watchlist.label_from_result(result) == "someone"

    def test_reads_a_nickname_off_a_single_post(self):
        assert watchlist.label_from_result({"data": {"author": {"nickname": "a"}}}) == "a"

    def test_reads_a_profile_directly(self):
        assert watchlist.label_from_result({"data": {"nickname": "profile"}}) == "profile"

    @pytest.mark.parametrize(
        "payload", [None, {}, {"data": None}, {"data": {}}, {"data": {"items": []}}, "text"]
    )
    def test_a_shape_it_does_not_know_costs_nothing(self, payload):
        # The label is a convenience. An unrecognised shape must not fail a
        # collection that otherwise worked.
        assert watchlist.label_from_result(payload) is None
