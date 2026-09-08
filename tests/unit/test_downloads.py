"""The download index: staleness, eviction policy, and the job handed to the sidecar.

The eviction rules get most of the attention here because this is the only
policy in the system that deletes something a user can see. The rest of the
project's disciplined refusal to delete anything (retention defaults to 0,
PROTECTED_TABLES, "the archive exists to outlive the platform") makes the one
exception worth pinning down precisely.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from dtk.core.types import Platform
from dtk.services import downloads

MB = 1024 * 1024


def row(bytes_total: int, *, pinned: bool = False, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        directory=name or f"douyin/a/{bytes_total}",
        bytes_total=bytes_total,
        pinned=pinned,
    )


class TestStaleness:
    def test_a_fresh_observation_is_used_as_is(self):
        recent = SimpleNamespace(last_seen_at=datetime.now(UTC) - timedelta(seconds=30))
        assert downloads.mirrors_are_stale(recent, max_age_seconds=600) is False

    def test_an_old_observation_forces_a_re_parse(self):
        # Measured on 2026-09-08: a TikTok video URL stored the day before
        # answered 403, because the link carries expire=.
        old = SimpleNamespace(last_seen_at=datetime.now(UTC) - timedelta(days=1))
        assert downloads.mirrors_are_stale(old, max_age_seconds=600) is True

    def test_zero_always_re_parses(self):
        recent = SimpleNamespace(last_seen_at=datetime.now(UTC))
        assert downloads.mirrors_are_stale(recent, max_age_seconds=0) is True

    def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing(self):
        naive = SimpleNamespace(last_seen_at=datetime.now(UTC).replace(tzinfo=None))
        assert downloads.mirrors_are_stale(naive, max_age_seconds=600) is False


class TestEviction:
    def test_removes_oldest_first_and_stops_once_under(self):
        # Rows arrive oldest first; only as much as it takes is removed.
        rows = [row(100 * MB), row(100 * MB), row(100 * MB)]
        plan = downloads.choose_evictions(rows, over_by=150 * MB)
        assert plan.paths == (rows[0].directory, rows[1].directory)
        assert plan.bytes_freed == 200 * MB

    def test_never_touches_a_pinned_download(self):
        # The exemption exists because a size policy without one eventually
        # deletes the file somebody meant to keep, and a post the platform has
        # since removed cannot be fetched again.
        pinned = row(500 * MB, pinned=True, name="douyin/a/keep")
        plan = downloads.choose_evictions([pinned, row(10 * MB)], over_by=400 * MB)
        assert "douyin/a/keep" not in plan.paths
        assert plan.pinned_bytes == 500 * MB

    def test_reports_when_everything_left_is_pinned(self):
        # Over the ceiling with nothing evictable is a real state, and the
        # sweep has to be able to say so rather than run silently forever.
        plan = downloads.choose_evictions([row(900 * MB, pinned=True)], over_by=400 * MB)
        assert plan.paths == ()
        assert plan.pinned_bytes == 900 * MB
        assert plan.over_by == 400 * MB

    def test_removes_nothing_when_already_under(self):
        plan = downloads.choose_evictions([row(10 * MB)], over_by=0)
        assert plan.paths == ()
        assert plan.bytes_freed == 0


class TestJobPayload:
    def test_carries_no_credential_and_only_this_platforms_domains(self):
        content = SimpleNamespace(
            platform="douyin",
            content_id="7408",
            kind="video",
            web_url="https://www.douyin.com/video/7408",
            title="t",
            description="d",
            platform_created_at=None,
            duration_ms=1000,
            author_uid="MS4wA",
            author_nickname="n",
            music_id=None,
            music_title=None,
            tags=[],
            location=None,
            last_seen_at=datetime.now(UTC),
            media={"video": {"url": "https://v9-v2-mps-cdn.douyinvod.com/x/v.mp4"}},
        )
        download = SimpleNamespace(id=uuid.uuid4())
        plan = downloads.plan_for(content, max_file_bytes=MB)
        payload = downloads.job_payload(download, content, plan)

        assert "douyinvod.com" in payload["domains"]
        # A Douyin post has no business reaching a TikTok CDN, and sending the
        # union would make that indistinguishable from a normal job.
        assert not any("tiktok" in domain for domain in payload["domains"])
        # Nothing that could authenticate anything crosses this boundary.
        flat = str(payload)
        for forbidden in ("cookie", "Cookie", "secret", "proxy", "postgresql"):
            assert forbidden not in flat

    def test_the_directory_is_relative_never_absolute(self):
        # The path inside the container is not the path on the host; storing an
        # absolute one would make the row wrong the moment the volume moves.
        content = SimpleNamespace(platform="tiktok", author_uid="1", content_id="2")
        assert downloads.directory_of(content) == "tiktok/1/2"


class TestPlanFor:
    def test_reads_the_platform_off_the_row(self):
        content = SimpleNamespace(
            platform="tiktok",
            media={"video": {"url": "https://v16-webapp-prime.us.tiktok.com/x"}},
        )
        plan = downloads.plan_for(content, max_file_bytes=MB)
        assert [item.kind for item in plan.items] == ["video"]

    def test_an_unknown_platform_is_an_error_not_a_silent_empty_plan(self):
        content = SimpleNamespace(platform="myspace", media={})
        with pytest.raises(ValueError):
            downloads.plan_for(content, max_file_bytes=MB)


def test_the_platform_enum_covers_every_media_allowlist() -> None:
    from dtk.media.domains import MEDIA_DOMAINS_BY_PLATFORM

    assert set(MEDIA_DOMAINS_BY_PLATFORM) == set(Platform)


def test_a_media_download_is_not_exempt_from_the_queue_ceiling() -> None:
    """The one Maintenance member that still has to queue like everything else.

    The others are rare operator actions. A download is not: a caller can start
    one per archived post, and each holds a worker slot for as long as a video
    takes, so exempting them would let a bulk download starve every read.
    """
    from dtk.api.routes.operations import _OPERATOR_TRIGGERED, Maintenance

    assert Maintenance.MEDIA_DOWNLOAD.value not in _OPERATOR_TRIGGERED
    assert Maintenance.DIAGNOSE.value in _OPERATOR_TRIGGERED
