"""Pure parsers turning Douyin web responses into the normalized models.

Every function here is a pure function: a decoded JSON body in, a model from
:mod:`dtk.models` out. No network, no configuration, no database. That is the
constraint that makes ``tests/replay/`` possible; see
``docs/design/13-testing.md``.

Three behaviours are load bearing and are what the replay tests pin down:

* **A missing required field raises** :class:`~dtk.core.errors.UpstreamChanged`
  with the dotted path that went missing. Half-filled models are never
  returned. Silent degradation is how a scraper keeps 'working' while quietly
  returning half the data.
* **A block is not a schema change.** A response whose shape is intact but whose
  payload was withheld raises :class:`~dtk.core.errors.UpstreamRiskControl`.
  V4 conflated the two, so users retried forever on bugs and filed bug reports
  for rate limits.
* **Absent is ``None``.** In particular Douyin's web API never populates
  ``statistics.play_count`` - it is a constant ``0`` placeholder - so it is
  normalized to ``None`` rather than recorded as 'nobody watched this'.

Douyin has a single ``desc`` field, so ``title`` is its first non-blank line and
``description`` is the whole text, per ``docs/design/11-data-contracts.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from dtk.core.errors import UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.models import (
    Author,
    AuthorStats,
    Comment,
    Content,
    ContentStats,
    Image,
    Media,
    Music,
    Page,
    VideoStream,
)
from dtk.platforms.common import (
    Node,
    clean_urls,
    epoch_to_datetime,
    image_from_url_list,
    optional_bool,
    optional_id,
    optional_int,
    optional_positive_int,
    optional_str,
    seconds_to_ms,
    split_title,
)

_PLATFORM: Final = Platform.DOUYIN
_NAME: Final = _PLATFORM.value

WEB_DOMAIN: Final = "https://www.douyin.com"

#: Status codes seen on risk-control responses rather than on data.
#:
#: Recovered from V4's error handling and the shapes it worked around. They are
#: a starting point, not gospel: the captured-response task in
#: ``docs/design/16-salvage-and-debug.md`` must confirm them before release.
RISK_STATUS_CODES: Final[frozenset[int]] = frozenset({8, 2154, 2156, 10000, 10001})

#: Douyin marks self-only (1) and friends-only (2) posts here; 0 is public.
_PUBLIC_PRIVATE_STATUS: Final = 0


def detect_risk_control(payload: Mapping[str, Any]) -> str | None:
    """Name the risk-control signature in ``payload``, or ``None``.

    Kept public and pure so the transport layer's ``OK / BUSINESS_ERROR /
    RISK_CONTROL / NETWORK_ERROR`` classifier and the parsers agree on what a
    block looks like. Getting this wrong in the permissive direction cools down
    a healthy identity every time a video is deleted, which is precisely the
    failure ``docs/design/02-identity-pool.md`` warns about.
    """
    if not payload:
        return "empty_body"
    code = optional_int(payload.get("status_code"))
    if code is not None and code in RISK_STATUS_CODES:
        return f"status_code:{code}"
    if payload.get("verify_center_decision_conf"):
        return "verify_center"
    if payload.get("captcha") or payload.get("verify_page"):
        return "captcha_page"
    # A structurally valid envelope whose single payload key was emptied out.
    # An empty ``aweme_list`` or ``comments`` is legitimate (no posts, no
    # comments); an empty ``aweme_detail`` or ``user`` never is.
    for key in ("aweme_detail", "user"):
        if key in payload and not payload[key]:
            return f"empty:{key}"
    return None


def _guard(payload: Mapping[str, Any]) -> Node:
    signal = detect_risk_control(payload)
    if signal is not None:
        raise UpstreamRiskControl(
            f"douyin risk control response: {signal}",
            details={"platform": _NAME, "signal": signal},
        )
    return Node(payload, "", _NAME)


def content_web_url(content_id: str, kind: ContentKind) -> str:
    """Canonical web URL for a post."""
    slug = "note" if kind is ContentKind.IMAGE_ALBUM else "video"
    return f"{WEB_DOMAIN}/{slug}/{content_id}"


def author_web_url(sec_user_id: str) -> str:
    return f"{WEB_DOMAIN}/user/{sec_user_id}"


def _author_stats(node: Node) -> AuthorStats | None:
    stats = AuthorStats(
        follower_count=optional_int(node.get("follower_count")),
        following_count=optional_int(node.get("following_count")),
        content_count=optional_int(node.get("aweme_count")),
        total_digg=optional_int(node.get("total_favorited")),
    )
    if stats == AuthorStats():
        # List endpoints return an abbreviated author with no counters at all.
        return None
    return stats


def _avatar(node: Node) -> Image | None:
    for key in ("avatar_larger", "avatar_medium", "avatar_300x300", "avatar_thumb"):
        image = image_from_url_list(node.get(key))
        if image is not None:
            return image
    return None


def _is_verified(node: Node) -> bool:
    return bool(
        optional_str(node.get("custom_verify"))
        or optional_str(node.get("enterprise_verify_reason"))
    )


def author_from_node(node: Node, *, include_raw: bool = False) -> Author:
    """Build an :class:`Author` from a Douyin user object.

    ``uid`` is ``sec_uid``: the numeric ``uid`` rotates and cannot be a primary
    key. ``unique_id`` falls back to ``short_id`` because accounts that never
    set a custom handle return an empty ``unique_id``.
    """
    sec_uid = node.id("sec_uid")
    return Author(
        platform=_PLATFORM,
        uid=sec_uid,
        unique_id=optional_str(node.get("unique_id")) or optional_id(node.get("short_id")),
        nickname=node.text("nickname"),
        signature=optional_str(node.get("signature")),
        avatar=_avatar(node),
        web_url=author_web_url(sec_uid),
        verified=_is_verified(node),
        stats=_author_stats(node),
        raw=node.raw() if include_raw else None,
    )


def _music(node: Node | None) -> Music | None:
    if node is None:
        return None
    play_url = node.opt_child("play_url")
    # ``play_url`` is sometimes present with no ``url_list`` at all, and a bare
    # ``next(... for url in None)`` there raises TypeError - a crash the caller
    # cannot classify, instead of a track with no audio link.
    play_urls = clean_urls(play_url.get("url_list") or []) if play_url is not None else []
    first_url = play_urls[0] if play_urls else None
    cover = None
    for key in ("cover_large", "cover_medium", "cover_thumb"):
        cover = image_from_url_list(node.get(key))
        if cover is not None:
            break
    music = Music(
        music_id=optional_id(node.get("id_str")) or optional_id(node.get("id")),
        title=optional_str(node.get("title")),
        author=optional_str(node.get("author")),
        duration_ms=seconds_to_ms(node.get("duration")),
        play_url=first_url,
        cover=cover,
    )
    return None if music == Music() else music


def _covers(video: Node | None) -> list[Image]:
    if video is None:
        return []
    covers: list[Image] = []
    for key in ("cover", "origin_cover", "dynamic_cover"):
        image = image_from_url_list(video.get(key))
        if image is not None and image not in covers:
            covers.append(image)
    return covers


def _stream(addr: Node, *, watermark: bool, fallback: Node | None = None) -> VideoStream | None:
    image = image_from_url_list(addr.data)
    if image is None:
        return None
    width = optional_int(addr.get("width")) or (
        optional_int(fallback.get("width")) if fallback is not None else None
    )
    height = optional_int(addr.get("height")) or (
        optional_int(fallback.get("height")) if fallback is not None else None
    )
    return VideoStream(
        url=image.url,
        urls=image.urls,
        width=width,
        height=height,
        bitrate=None,
        format=optional_str(addr.get("format"))
        or (optional_str(fallback.get("format")) if fallback is not None else None),
        size_bytes=optional_int(addr.get("data_size")),
        watermark=watermark,
    )


def _bitrate_streams(video: Node) -> list[VideoStream]:
    streams: list[VideoStream] = []
    for gear in video.opt_children("bit_rate"):
        addr = gear.opt_child("play_addr")
        if addr is None:
            continue
        stream = _stream(addr, watermark=False, fallback=video)
        if stream is None:
            continue
        streams.append(
            stream.model_copy(
                update={
                    "bitrate": optional_int(gear.get("bit_rate")),
                    "format": optional_str(gear.get("format")) or stream.format,
                }
            )
        )
    return streams


def _album_images(detail: Node) -> list[Image]:
    images: list[Image] = []
    for item in detail.opt_children("images"):
        image = image_from_url_list(item.data)
        if image is None:
            raise item.missing("url_list")
        images.append(image)
    return images


def _media(detail: Node, kind: ContentKind, *, require_media: bool) -> Media:
    video = detail.opt_child("video")
    covers = _covers(video)

    if kind is ContentKind.IMAGE_ALBUM:
        images = _album_images(detail)
        if require_media and not images:
            raise detail.missing("images")
        return Media(covers=covers, video=None, streams=[], images=images)

    main: VideoStream | None = None
    streams: list[VideoStream] = []
    if video is not None:
        play_addr = video.opt_child("play_addr")
        if play_addr is not None:
            main = _stream(play_addr, watermark=False, fallback=video)
        streams = _bitrate_streams(video)
        download = video.opt_child("download_addr")
        if download is not None:
            # The download address carries the burned-in watermark; kept as a
            # last-resort mirror rather than dropped.
            watermarked = _stream(download, watermark=True, fallback=video)
            if watermarked is not None:
                streams.append(watermarked)
    if main is None and require_media:
        if video is None:
            raise detail.missing("video")
        raise video.missing("play_addr")
    return Media(covers=covers, video=main, streams=streams, images=[])


def _tags(detail: Node) -> list[str]:
    tags: list[str] = []
    for extra in detail.opt_children("text_extra"):
        name = optional_str(extra.get("hashtag_name"))
        if name is not None and name not in tags:
            tags.append(name)
    return tags


def _stats(node: Node) -> ContentStats:
    return ContentStats(
        # Douyin's web API returns a constant 0 here regardless of real plays,
        # so recording 0 would write a fact the platform never stated.
        play_count=optional_positive_int(node.get("play_count")),
        digg_count=optional_int(node.get("digg_count")),
        comment_count=optional_int(node.get("comment_count")),
        share_count=optional_int(node.get("share_count")),
        collect_count=optional_int(node.get("collect_count")),
    )


def content_from_node(detail: Node, *, fetched_at: datetime) -> Content:
    """Build a :class:`Content` from one ``aweme`` object.

    Shared by the detail endpoint and the post list, which return the same
    object shape - the whole point of the normalization contract.
    """
    content_id = detail.id("aweme_id")
    title, description = split_title(detail.text("desc"))

    status = detail.child("status")
    is_deleted = optional_bool(status.get("is_delete"))
    is_private = (
        optional_int(status.get("private_status")) or _PUBLIC_PRIVATE_STATUS
    ) != _PUBLIC_PRIVATE_STATUS

    album = bool(detail.opt_children("images"))
    kind = ContentKind.IMAGE_ALBUM if album else ContentKind.VIDEO

    # A withdrawn or restricted post keeps its metadata but loses its media
    # URLs. Demanding them would turn 'this video was deleted' into a false
    # UPSTREAM_CHANGED bug report.
    media = _media(detail, kind, require_media=not (is_deleted or is_private))

    duration_ms = None
    if kind is ContentKind.VIDEO:
        video = detail.opt_child("video")
        duration_ms = optional_positive_int(video.get("duration")) if video is not None else None
        if duration_ms is None:
            duration_ms = optional_positive_int(detail.get("duration"))

    return Content(
        platform=_PLATFORM,
        content_id=content_id,
        kind=kind,
        web_url=content_web_url(content_id, kind),
        title=title,
        description=description,
        created_at=epoch_to_datetime(detail.get("create_time")),
        duration_ms=duration_ms,
        is_deleted=is_deleted,
        is_private=is_private,
        author=author_from_node(detail.child("author")),
        stats=_stats(detail.child("statistics")),
        media=media,
        music=_music(detail.opt_child("music")),
        tags=_tags(detail),
        location=optional_str(
            (detail.opt_child("poi_info") or Node({}, "", _NAME)).get("poi_name")
        ),
        fetched_at=fetched_at,
        raw=detail.raw(),
    )


def parse_content(payload: Mapping[str, Any], *, fetched_at: datetime) -> Content:
    """Parse ``/aweme/v1/web/aweme/detail/``."""
    root = _guard(payload)
    return content_from_node(root.child("aweme_detail"), fetched_at=fetched_at)


def parse_author(payload: Mapping[str, Any]) -> Author:
    """Parse ``/aweme/v1/web/user/profile/other/``."""
    root = _guard(payload)
    return author_from_node(root.child("user"), include_raw=True)


def parse_author_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/aweme/v1/web/aweme/post/``.

    The cursor handed back is Douyin's ``max_cursor``, a millisecond publish
    timestamp. It is stringified and treated as opaque: TikTok's cursor is an
    offset and callers must not tell the difference.
    """
    root = _guard(payload)
    items = [content_from_node(item, fetched_at=fetched_at) for item in root.children("aweme_list")]
    has_more = optional_bool(root.present("has_more"))
    cursor = optional_id(root.get("max_cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("max_cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def comment_from_node(
    node: Node,
    *,
    content_id: str | None = None,
    parent_id: str | None = None,
) -> Comment:
    """Build a :class:`Comment` from one Douyin comment object."""
    resolved_content_id = optional_id(node.get("aweme_id")) or content_id
    if resolved_content_id is None:
        raise node.missing("aweme_id")
    # ``reply_id`` is "0" on a top level comment and the parent cid on a reply.
    resolved_parent = optional_id(node.get("reply_id")) or parent_id
    return Comment(
        platform=_PLATFORM,
        comment_id=node.id("cid"),
        content_id=resolved_content_id,
        parent_id=resolved_parent,
        text=node.text("text"),
        created_at=epoch_to_datetime(node.get("create_time")),
        digg_count=optional_int(node.get("digg_count")),
        reply_count=optional_int(node.get("reply_comment_total")),
        author=author_from_node(node.child("user")),
        is_author_liked=optional_bool(node.get("is_author_digged")),
        is_pinned=bool(optional_int(node.get("stick_position")) or 0),
        raw=node.raw(),
    )


def _comment_page(
    payload: Mapping[str, Any],
    *,
    content_id: str | None,
    parent_id: str | None,
) -> Page[Comment]:
    root = _guard(payload)
    items = [
        comment_from_node(item, content_id=content_id, parent_id=parent_id)
        for item in root.children("comments")
    ]
    has_more = optional_bool(root.present("has_more"))
    # ``optional_id`` and not ``optional_str``: a page that claims more results
    # while handing back cursor 0 would send the paginator back to page one for
    # ever, which is a schema change, not a last page.
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_comments(payload: Mapping[str, Any], *, content_id: str | None = None) -> Page[Comment]:
    """Parse ``/aweme/v1/web/comment/list/``.

    ``content_id`` is a fallback for the rare comment object that omits
    ``aweme_id``; the caller always knows it because it built the request.
    """
    return _comment_page(payload, content_id=content_id, parent_id=None)


def parse_comment_replies(
    payload: Mapping[str, Any],
    *,
    content_id: str | None = None,
    parent_id: str | None = None,
) -> Page[Comment]:
    """Parse ``/aweme/v1/web/comment/list/reply/``."""
    return _comment_page(payload, content_id=content_id, parent_id=parent_id)


def parse_reply_previews(
    payload: Mapping[str, Any], *, content_id: str | None = None
) -> list[Comment]:
    """Extract the replies Douyin inlines under each top level comment.

    The comment list embeds the first few replies in ``reply_comment``. They are
    not part of the comment page - paging over them would duplicate what the
    reply endpoint returns - but they are real data already paid for, so they
    are available separately.
    """
    root = _guard(payload)
    previews: list[Comment] = []
    for comment in root.children("comments"):
        parent_id = comment.id("cid")
        for reply in comment.opt_children("reply_comment"):
            previews.append(comment_from_node(reply, content_id=content_id, parent_id=parent_id))
    return previews


__all__ = [
    "RISK_STATUS_CODES",
    "WEB_DOMAIN",
    "author_from_node",
    "author_web_url",
    "comment_from_node",
    "content_from_node",
    "content_web_url",
    "detect_risk_control",
    "parse_author",
    "parse_author_posts",
    "parse_comment_replies",
    "parse_comments",
    "parse_content",
    "parse_reply_previews",
]
