"""Planning a download: what gets fetched, what it is called, how big it may be.

Every policy decision the Go sidecar is not allowed to make is made here, so
these tests are the record of what those decisions are.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from dtk.core.types import Platform
from dtk.media import plan as planner

MB = 1024 * 1024

VIDEO = "https://v9-v2-mps-cdn.douyinvod.com/x/video/tos/cn/a/?mime_type=video_mp4"
VIDEO_ALT = "https://v5-dy-ov-experiment.zjcdn.com/x/video/tos/cn/b/"
COVER = "https://p3-pc-sign.douyinpic.com/aweme/cover.jpeg"
IMAGE = "https://p3-pc-sign.douyinpic.com/aweme/slide-1.jpg"


def test_a_video_post_plans_the_video_and_one_cover() -> None:
    result = planner.build(
        {
            "video": {"url": VIDEO, "urls": [VIDEO]},
            "streams": [{"url": VIDEO_ALT, "urls": []}],
            "covers": [{"url": COVER, "urls": []}, {"url": COVER + "?2", "urls": []}],
            "images": [],
        },
        Platform.DOUYIN,
        max_file_bytes=512 * MB,
    )
    names = [item.name for item in result.items]
    # One cover, not two: the list holds an animated version and a static
    # fallback of the same frame, and both is a duplicate.
    assert names == ["video.mp4", "cover.jpeg"]
    assert result.items[0].max_bytes == 512 * MB
    # Images are capped well below the video ceiling; a 500 MB "image" means
    # something is wrong rather than that the photograph is large.
    assert result.items[1].max_bytes == planner.IMAGE_MAX_BYTES


def test_alternate_bitrates_become_mirrors_of_one_file() -> None:
    # All bitrates are the same clip. Storing several copies is not a feature.
    result = planner.build(
        {"video": {"url": VIDEO}, "streams": [{"url": VIDEO_ALT}]},
        Platform.DOUYIN,
        max_file_bytes=512 * MB,
    )
    assert len(result.items) == 1
    assert result.items[0].mirrors == (VIDEO, VIDEO_ALT)


def test_an_album_numbers_its_slides() -> None:
    result = planner.build(
        {"images": [{"url": IMAGE}, {"url": IMAGE + "?2"}, {"url": IMAGE + "?3"}]},
        Platform.DOUYIN,
        max_file_bytes=512 * MB,
    )
    assert [item.name for item in result.items] == [
        "image-01.jpg",
        "image-02.jpg",
        "image-03.jpg",
    ]


def test_the_extension_comes_from_the_url_only_when_it_is_one_we_know() -> None:
    result = planner.build(
        {"video": {"url": "https://v9-v2-mps-cdn.douyinvod.com/x/opaque-object-key"}},
        Platform.DOUYIN,
        max_file_bytes=MB,
    )
    # No usable suffix on the path, so the kind's default is used rather than
    # whatever the last dot-separated fragment happened to be.
    assert result.items[0].name == "video.mp4"

    hostile = planner.build(
        {"video": {"url": "https://v9-v2-mps-cdn.douyinvod.com/x/payload.php"}},
        Platform.DOUYIN,
        max_file_bytes=MB,
    )
    assert hostile.items[0].name == "video.mp4"


def test_a_mirror_off_the_allowlist_is_dropped_with_a_reason() -> None:
    result = planner.build(
        {"video": {"url": "https://evil.example/v.mp4", "urls": [VIDEO]}},
        Platform.DOUYIN,
        max_file_bytes=MB,
    )
    assert result.items[0].mirrors == (VIDEO,)
    assert any("evil.example" in reason for reason in result.skipped)


def test_an_item_with_no_surviving_mirror_says_so() -> None:
    result = planner.build(
        {"video": {"url": "https://evil.example/v.mp4"}},
        Platform.DOUYIN,
        max_file_bytes=MB,
    )
    assert result.empty
    assert any("allowlist" in reason for reason in result.skipped)


def test_an_empty_manifest_is_an_answer_not_an_exception() -> None:
    assert planner.build(None, Platform.DOUYIN, max_file_bytes=MB).empty
    assert planner.build({}, Platform.TIKTOK, max_file_bytes=MB).empty


def test_video_accepts_octet_stream_and_images_do_not() -> None:
    # Several edges answer a download of an mp4 with application/octet-stream:
    # a "we did not say" type, not a wrong one. An image host that does the
    # same is either broken or serving something that is not an image, and the
    # whole value of the check is refusing the HTML interstitial.
    assert "application/octet-stream" in planner.VIDEO_ACCEPT
    assert "application/octet-stream" not in planner.IMAGE_ACCEPT
    assert "text/html" not in planner.VIDEO_ACCEPT


def test_the_item_cap_bounds_a_malformed_payload() -> None:
    result = planner.build(
        {"images": [{"url": f"{IMAGE}?{index}"} for index in range(500)]},
        Platform.DOUYIN,
        max_file_bytes=MB,
    )
    assert len(result.items) == planner.MAX_ITEMS


def test_the_sidecar_carries_identity_but_no_signed_link() -> None:
    row = SimpleNamespace(
        platform="douyin",
        content_id="7408",
        kind="video",
        web_url="https://www.douyin.com/video/7408",
        title="a post",
        description="a post",
        platform_created_at=datetime(2024, 8, 30, tzinfo=UTC),
        duration_ms=485088,
        author_uid="MS4wA",
        author_nickname="someone",
        music_id="1",
        music_title="a song",
        tags=["dog"],
        location=None,
        last_seen_at=datetime(2026, 9, 8, tzinfo=UTC),
    )
    result = planner.build({"video": {"url": VIDEO}}, Platform.DOUYIN, max_file_bytes=MB)
    meta = planner.sidecar(row, result)

    assert meta["web_url"] == "https://www.douyin.com/video/7408"
    assert meta["files"] == [{"name": "video.mp4", "kind": "video"}]
    # Signed CDN links expire within hours, so a sidecar carrying one would be
    # the single misleading field in a file meant to still make sense in a year.
    assert VIDEO not in str(meta)


# --------------------------------------------------------------------------
# Export names
#
# On the volume a post owns a directory, so `video.mp4` is unambiguous. In a
# browser's downloads folder it is not: fifty saved posts are fifty files
# called `video.mp4`, and the second is `video (1).mp4`.
# --------------------------------------------------------------------------


def test_an_exported_file_says_where_it_came_from() -> None:
    assert (
        planner.export_name("douyin", "7408915107113127220", "video.mp4")
        == "dtk-douyin-7408915107113127220-video.mp4"
    )


def test_the_stored_name_is_kept_so_an_albums_slides_stay_apart() -> None:
    """Replacing it with a guessed extension would collapse a 30-slide album."""
    names = {
        planner.export_name("tiktok", "7300", stored)
        for stored in ("video.mp4", "cover.jpg", "image-01.jpg", "image-02.jpg")
    }
    assert names == {
        "dtk-tiktok-7300-video.mp4",
        "dtk-tiktok-7300-cover.jpg",
        "dtk-tiktok-7300-image-01.jpg",
        "dtk-tiktok-7300-image-02.jpg",
    }


def test_nothing_reaches_a_filename_that_could_leave_the_directory() -> None:
    """Both parts are ours and already validated; this is the belt.

    The name goes into a Content-Disposition header and then onto somebody
    else's disk, which is one place too far to rely on an upstream check.
    """
    assert planner.export_name("douyin", "../../etc/passwd", "video.mp4") == (
        "dtk-douyin-etc-passwd-video.mp4"
    )
    assert "\r" not in planner.export_name("douyin", "7408\r\nX-Evil: 1", "video.mp4")
    assert "/" not in planner.export_name("../x", "../y", "../z.mp4")


def test_a_part_that_sanitises_away_is_dropped_rather_than_left_empty() -> None:
    assert planner.export_name("douyin", "", "video.mp4") == "dtk-douyin-video.mp4"


def test_a_name_that_sanitises_to_nothing_still_comes_out_usable() -> None:
    """Never a bare prefix: `dtk` is not a filename anybody can do anything with."""
    assert planner.export_name("", "", "/") == "dtk-file"
