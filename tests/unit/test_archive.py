"""The content archive, and the classification derived on write.

Everything here is a pure function of what the parser already returned. That is
the point: docs/design/README.md records AI content analysis as a non-goal, so
"auto-classify" has to mean derivation, and derivation means a rule change is a
backfill rather than a re-crawl.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dtk.core.types import ContentKind, Platform
from dtk.models.content import (
    Author,
    AuthorStats,
    Content,
    ContentStats,
    Image,
    Media,
    Music,
    Page,
    VideoStream,
)
from dtk.services import archive

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _content(**overrides: object) -> Content:
    fields: dict = {
        "platform": Platform.TIKTOK,
        "content_id": "7",
        "kind": ContentKind.VIDEO,
        "web_url": "https://www.tiktok.com/@a/video/7",
        "title": "hello",
        "description": "world",
        "author": Author(platform=Platform.TIKTOK, uid="MS4wLjABu", nickname="A"),
        "stats": ContentStats(),
        "media": Media(),
        "fetched_at": NOW,
    }
    fields.update(overrides)
    return Content(**fields)


# --------------------------------------------------------------------------
# Duration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration_ms", "expected"),
    [
        (None, "unknown"),
        (0, "unknown"),
        (-1, "unknown"),
        (15_000, "short"),
        (59_999, "short"),
        (60_000, "medium"),
        (179_999, "medium"),
        (180_000, "long"),
        (3_600_000, "long"),
    ],
)
def test_duration_buckets(duration_ms: int | None, expected: str) -> None:
    """None stays unknown. Calling a missing duration 'short' invents data."""
    assert archive.duration_bucket(duration_ms) == expected


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1080, 1920, "portrait"),
        (1920, 1080, "landscape"),
        (1080, 1080, "square"),
        (None, 1920, "unknown"),
        (0, 0, "unknown"),
    ],
)
def test_orientation(width: int | None, height: int | None, expected: str) -> None:
    assert archive.orientation_of(width, height) == expected


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (360, 640, "sd"),
        (720, 1280, "hd"),
        (1080, 1920, "fhd"),
        (2160, 3840, "uhd"),
        (None, None, "unknown"),
    ],
)
def test_resolution_is_keyed_on_the_shorter_edge(
    width: int | None, height: int | None, expected: str
) -> None:
    """A portrait 1080x1920 and a landscape 1920x1080 are the same quality."""
    assert archive.resolution_class(width, height) == expected
    assert archive.resolution_class(height, width) == expected


# --------------------------------------------------------------------------
# Script, which decides how search has to match
# --------------------------------------------------------------------------


def test_chinese_text_is_cjk() -> None:
    assert archive.script_of("\u4eca\u5929\u505a\u4e86\u4e00\u9053\u83dc", None) == "cjk"


def test_english_text_is_latin() -> None:
    assert archive.script_of("a recipe for dinner", None) == "latin"


def test_a_brand_name_does_not_make_a_chinese_caption_mixed() -> None:
    """Otherwise almost every Douyin caption would classify as mixed."""
    caption = "\u4eca\u5929\u7528 iPhone \u62cd\u4e86\u8fd9\u6761\u89c6\u9891\u6548\u679c\u975e\u5e38\u597d\u753b\u8d28\u6e05\u6670\u989c\u8272\u4e5f\u5f88\u51c6\u63a8\u8350\u5927\u5bb6\u8bd5\u8bd5\u770b"
    assert archive.script_of(caption, None) == "cjk"


def test_genuinely_bilingual_text_is_mixed() -> None:
    assert (
        archive.script_of("\u4eca\u5929\u505a\u4e86\u4e00\u9053\u83dc today I cooked a dish", None)
        == "mixed"
    )


def test_emoji_and_punctuation_alone_say_nothing() -> None:
    """A caption that is only emoji must not flip class on one stray letter."""
    assert archive.script_of("\u1f525\u1f525\u1f525 !!! 123", None) == "unknown"


# --------------------------------------------------------------------------
# Row construction
# --------------------------------------------------------------------------


def test_a_content_row_carries_its_classification() -> None:
    content = _content(
        duration_ms=30_000,
        tags=["food", "recipe"],
        media=Media(video=VideoStream(url="https://cdn/v.mp4", width=1080, height=1920)),
        music=Music(music_id="m1", title="Song"),
    )
    row = archive.content_row(content, now=NOW, store_raw=False)

    assert row["orientation"] == "portrait"
    assert row["duration_bucket"] == "short"
    assert row["resolution_class"] == "fhd"
    assert row["tags"] == ["food", "recipe"]
    assert row["music_id"] == "m1"
    assert row["availability"] == "live"


def test_raw_is_only_kept_when_the_operator_asked() -> None:
    content = _content(raw={"platform": "payload"})

    assert archive.content_row(content, now=NOW, store_raw=False)["raw"] is None
    assert archive.content_row(content, now=NOW, store_raw=True)["raw"] == {"platform": "payload"}


def test_a_deleted_post_is_recorded_as_deleted_not_dropped() -> None:
    """An archive whose whole point is outliving the platform must keep the row."""
    row = archive.content_row(_content(is_deleted=True), now=NOW, store_raw=False)
    assert row["availability"] == "deleted"


def test_media_is_kept_whole_so_a_download_can_retry_without_reparsing() -> None:
    content = _content(
        media=Media(
            video=VideoStream(url="https://cdn/a.mp4", urls=["https://cdn/a.mp4", "https://m2/a"]),
            covers=[Image(url="https://cdn/c.jpg")],
        )
    )
    row = archive.content_row(content, now=NOW, store_raw=False)

    assert row["cover_url"] == "https://cdn/c.jpg"
    assert len(row["media"]["video"]["urls"]) == 2


def test_an_author_row_reads_stats_when_a_list_endpoint_omitted_them() -> None:
    """List endpoints return an abbreviated author with no stats at all."""
    bare = archive.author_row(
        Author(platform=Platform.TIKTOK, uid="u", nickname="A"), now=NOW, store_raw=False
    )
    assert bare["follower_count"] is None

    full = archive.author_row(
        Author(
            platform=Platform.TIKTOK,
            uid="u",
            nickname="A",
            stats=AuthorStats(follower_count=10, content_count=2),
        ),
        now=NOW,
        store_raw=False,
    )
    assert full["follower_count"] == 10
    assert full["content_count"] == 2


# --------------------------------------------------------------------------
# What gets collected from a task's parsed models
# --------------------------------------------------------------------------


class _Session:
    """Records the rows each upsert would write, without a database."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)


async def test_a_page_archives_every_item_and_every_byline() -> None:
    page = Page[Content](
        items=[_content(content_id="1"), _content(content_id="2")], cursor=None, has_more=False
    )
    session = _Session()

    written = await archive.record(session, (page,), now=NOW)  # type: ignore[arg-type]

    assert written == 2
    # One author statement and one content statement, not one per item.
    assert len(session.statements) == 2


async def test_a_full_profile_beats_the_abbreviated_author_on_a_post() -> None:
    """Both name the same uid; the one with stats must be the one written."""
    post = _content()
    profile = Author(
        platform=Platform.TIKTOK,
        uid="MS4wLjABu",
        nickname="A",
        stats=AuthorStats(follower_count=99),
    )
    session = _Session()

    await archive.record(session, (post, profile), now=NOW)  # type: ignore[arg-type]

    authors = session.statements[0].compile().params  # type: ignore[attr-defined]
    assert authors["follower_count_m0"] == 99


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------


def test_a_cursor_round_trips() -> None:
    from dtk.db.models import ArchivedContent

    row = ArchivedContent(
        platform="tiktok",
        content_id="7",
        kind="video",
        web_url="https://x/7",
        author_uid="u",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    cursor = archive._cursor_encode(row)
    decoded = archive._cursor_decode(cursor)

    assert decoded is not None
    stamp, platform, content_id = decoded
    assert (platform, content_id) == ("tiktok", "7")
    assert stamp == NOW


@pytest.mark.parametrize("bad", ["", "!!!!", "Zm9v", "not-base64-at-all-@@@"])
def test_an_unreadable_cursor_is_refused_rather_than_restarting(bad: str) -> None:
    """Silently starting over from the top would look like an infinite feed."""
    assert archive._cursor_decode(bad) is None


def test_the_page_size_is_bounded() -> None:
    """The row carries a whole media block, so a page of 500 is megabytes."""
    assert archive.DEFAULT_PAGE <= archive.MAX_PAGE
    assert archive.MAX_PAGE <= 200


def test_a_filter_defaults_to_matching_everything() -> None:
    spec = archive.ArchiveFilter()
    assert spec.platform is None
    assert spec.query is None
