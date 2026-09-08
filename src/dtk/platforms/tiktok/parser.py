"""Pure parsers turning TikTok web responses into the normalized models.

Same contract as the Douyin parsers: decoded JSON in, models out, no IO.
Missing required fields raise :class:`~dtk.core.errors.UpstreamChanged` with the
field path; withheld payloads raise
:class:`~dtk.core.errors.UpstreamRiskControl`.

TikTok's web API speaks two dialects and both appear in the P0 set:

* ``/api/item/detail/``, ``/api/user/detail/`` and ``/api/post/item_list/``
  return the camelCase web schema (``itemStruct``, ``uniqueId``, ``diggCount``).
* ``/api/comment/list/`` and its reply variant return the snake_case aweme
  schema shared with Douyin (``cid``, ``sec_uid``, ``digg_count``).

So there are two author parsers here, not one with a fallback chain - a single
lenient parser would keep working while quietly mapping half the fields to
``None``, which is exactly the silent degradation the contract forbids.

``uid`` is TikTok's numeric author id, per
``docs/design/11-data-contracts.md``; the ``secUid`` is preserved in ``raw``.
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
    epoch_to_datetime,
    image_from_url_list,
    image_from_urls,
    optional_bool,
    optional_id,
    optional_int,
    optional_str,
    seconds_to_ms,
    split_title,
)

_PLATFORM: Final = Platform.TIKTOK
_NAME: Final = _PLATFORM.value

WEB_DOMAIN: Final = "https://www.tiktok.com"

#: Status codes seen on risk-control responses rather than on data.
#:
#: Recovered from V4's behaviour; the captured-response task in
#: ``docs/design/16-salvage-and-debug.md`` must confirm them before release.
#: Business codes such as 'item does not exist' are deliberately absent: they
#: are classified above this layer, and misfiling one as risk control would
#: cool down a healthy identity every time somebody looks up a deleted video.
RISK_STATUS_CODES: Final[frozenset[int]] = frozenset({10000, 10101})


def detect_risk_control(payload: Mapping[str, Any]) -> str | None:
    """Name the risk-control signature in ``payload``, or ``None``."""
    if not payload:
        return "empty_body"
    for key in ("statusCode", "status_code"):
        code = optional_int(payload.get(key))
        if code is not None and code in RISK_STATUS_CODES:
            return f"{key}:{code}"
    if payload.get("verify_event") or payload.get("captcha"):
        return "captcha_page"
    for key in ("itemInfo", "userInfo"):
        if key in payload and not payload[key]:
            return f"empty:{key}"
    item_info = payload.get("itemInfo")
    if isinstance(item_info, Mapping) and "itemStruct" in item_info and not item_info["itemStruct"]:
        return "empty:itemInfo.itemStruct"
    user_info = payload.get("userInfo")
    if isinstance(user_info, Mapping) and "user" in user_info and not user_info["user"]:
        return "empty:userInfo.user"
    return None


def _guard(payload: Mapping[str, Any]) -> Node:
    signal = detect_risk_control(payload)
    if signal is not None:
        raise UpstreamRiskControl(
            f"tiktok risk control response: {signal}",
            details={"platform": _NAME, "signal": signal},
        )
    return Node(payload, "", _NAME)


def content_web_url(unique_id: str, content_id: str, kind: ContentKind) -> str:
    """Canonical web URL for a post. Photo mode lives under ``/photo/``."""
    slug = "photo" if kind is ContentKind.IMAGE_ALBUM else "video"
    return f"{WEB_DOMAIN}/@{unique_id}/{slug}/{content_id}"


def author_web_url(unique_id: str) -> str:
    return f"{WEB_DOMAIN}/@{unique_id}"


def _first_stated(node: Node, *keys: str) -> int | None:
    """First key the payload actually states, so a real ``0`` is not skipped."""
    for key in keys:
        value = optional_int(node.get(key))
        if value is not None:
            return value
    return None


def _web_author_stats(node: Node | None) -> AuthorStats | None:
    if node is None:
        return None
    stats = AuthorStats(
        follower_count=optional_int(node.get("followerCount")),
        following_count=optional_int(node.get("followingCount")),
        content_count=optional_int(node.get("videoCount")),
        # ``heartCount`` is likes received; ``diggCount`` is likes given. The
        # fallback is on absence, not on falsiness: a stated 0 is a measurement.
        total_digg=_first_stated(node, "heartCount", "heart"),
    )
    return None if stats == AuthorStats() else stats


def author_from_web_node(
    node: Node,
    *,
    stats: Node | None = None,
    include_raw: bool = False,
) -> Author:
    """Build an :class:`Author` from the camelCase web user object."""
    unique_id = node.id("uniqueId")
    return Author(
        platform=_PLATFORM,
        uid=node.id("id"),
        unique_id=unique_id,
        nickname=node.text("nickname"),
        signature=optional_str(node.get("signature")),
        avatar=image_from_urls(
            node.get("avatarLarger"), node.get("avatarMedium"), node.get("avatarThumb")
        ),
        web_url=author_web_url(unique_id),
        verified=optional_bool(node.get("verified")),
        stats=_web_author_stats(stats),
        raw=node.raw() if include_raw else None,
    )


def author_from_comment_node(node: Node, *, include_raw: bool = False) -> Author:
    """Build an :class:`Author` from the snake_case aweme user object.

    Returned by the comment endpoints. The numeric ``uid`` is the same id the
    web schema calls ``id``, so both dialects normalize onto the same key.
    """
    unique_id = optional_str(node.get("unique_id"))
    return Author(
        platform=_PLATFORM,
        uid=node.id("uid"),
        unique_id=unique_id,
        nickname=node.text("nickname"),
        signature=optional_str(node.get("signature")),
        avatar=image_from_url_list(node.get("avatar_thumb"))
        or image_from_url_list(node.get("avatar_larger")),
        web_url=author_web_url(unique_id) if unique_id else None,
        verified=optional_bool(node.get("verified"))
        or bool(optional_str(node.get("custom_verify"))),
        stats=None,
        raw=node.raw() if include_raw else None,
    )


def _music(node: Node | None) -> Music | None:
    if node is None:
        return None
    music = Music(
        music_id=optional_id(node.get("id")),
        title=optional_str(node.get("title")),
        author=optional_str(node.get("authorName")),
        duration_ms=seconds_to_ms(node.get("duration")),
        play_url=optional_str(node.get("playUrl")),
        cover=image_from_urls(node.get("coverLarge"), node.get("coverMedium")),
    )
    return None if music == Music() else music


def _stats(item: Node) -> ContentStats:
    """Read metrics, preferring ``statsV2``.

    ``statsV2`` carries the same counters as strings so they survive counts past
    the JavaScript safe integer range; ``stats`` is the older numeric copy.
    """
    primary = item.opt_child("statsV2")
    secondary = item.opt_child("stats")
    if primary is None and secondary is None:
        raise item.missing("stats")

    def metric(name: str) -> int | None:
        for source in (primary, secondary):
            if source is None:
                continue
            value = optional_int(source.get(name))
            if value is not None:
                return value
        return None

    return ContentStats(
        play_count=metric("playCount"),
        digg_count=metric("diggCount"),
        comment_count=metric("commentCount"),
        share_count=metric("shareCount"),
        collect_count=metric("collectCount"),
    )


def _covers(video: Node | None, image_post: Node | None) -> list[Image]:
    covers: list[Image] = []
    if video is not None:
        for key in ("cover", "originCover", "dynamicCover"):
            image = image_from_urls(video.get(key))
            if image is not None and image not in covers:
                covers.append(image)
    if image_post is not None:
        image = _image_from_url_holder(image_post.opt_child("cover"))
        if image is not None and image not in covers:
            covers.append(image)
    return covers


def _bitrate_streams(video: Node) -> list[VideoStream]:
    streams: list[VideoStream] = []
    for gear in video.opt_children("bitrateInfo"):
        addr = gear.opt_child("PlayAddr")
        if addr is None:
            continue
        image = image_from_urls(addr.get("UrlList"))
        if image is None:
            continue
        streams.append(
            VideoStream(
                url=image.url,
                urls=image.urls,
                width=optional_int(video.get("width")),
                height=optional_int(video.get("height")),
                bitrate=optional_int(gear.get("Bitrate")),
                format=optional_str(gear.get("CodecType")) or optional_str(video.get("format")),
                size_bytes=optional_int(addr.get("DataSize")),
                watermark=False,
            )
        )
    return streams


def _image_from_url_holder(node: Node | None) -> Image | None:
    """Read TikTok's ``{"imageURL": {"urlList": [...]}}`` container."""
    if node is None:
        return None
    holder = node.opt_child("imageURL")
    if holder is None:
        return None
    return image_from_urls(holder.get("urlList"))


def _album_images(image_post: Node) -> list[Image]:
    images: list[Image] = []
    for item in image_post.children("images"):
        image = _image_from_url_holder(item)
        if image is None:
            raise item.missing("imageURL")
        images.append(
            image.model_copy(
                update={
                    "width": optional_int(item.get("imageWidth")) or image.width,
                    "height": optional_int(item.get("imageHeight")) or image.height,
                }
            )
        )
    return images


def _media(
    item: Node,
    kind: ContentKind,
    video: Node | None,
    image_post: Node | None,
    *,
    require_media: bool,
) -> Media:
    covers = _covers(video, image_post)

    if kind is ContentKind.IMAGE_ALBUM:
        images = _album_images(image_post) if image_post is not None else []
        if require_media and not images:
            raise item.missing("imagePost")
        return Media(covers=covers, video=None, streams=[], images=images)

    main: VideoStream | None = None
    streams: list[VideoStream] = []
    if video is not None:
        play = image_from_urls(video.get("playAddr"), video.get("downloadAddr"))
        if play is not None:
            main = VideoStream(
                url=play.url,
                urls=play.urls,
                width=optional_int(video.get("width")),
                height=optional_int(video.get("height")),
                bitrate=optional_int(video.get("bitrate")),
                format=optional_str(video.get("format")),
                size_bytes=optional_int(video.get("size")),
                watermark=False,
            )
        streams = _bitrate_streams(video)
    if main is None and require_media:
        if video is None:
            raise item.missing("video")
        raise video.missing("playAddr")
    return Media(covers=covers, video=main, streams=streams, images=[])


def _tags(item: Node) -> list[str]:
    tags: list[str] = []
    for extra in item.opt_children("textExtra"):
        name = optional_str(extra.get("hashtagName"))
        if name is not None and name not in tags:
            tags.append(name)
    for challenge in item.opt_children("challenges"):
        name = optional_str(challenge.get("title"))
        if name is not None and name not in tags:
            tags.append(name)
    return tags


def content_from_node(item: Node, *, fetched_at: datetime) -> Content:
    """Build a :class:`Content` from one ``itemStruct``.

    Shared by the detail endpoint and the post list, which return identical
    objects.
    """
    content_id = item.id("id")
    title, description = split_title(item.text("desc"))

    author_node = item.child("author")
    author = author_from_web_node(author_node, stats=item.opt_child("authorStats"))

    # ``takeDown`` only: ``itemMute`` means the audio track was muted, which
    # happens to plenty of live posts. Folding it in here would both report a
    # playable video as deleted and switch off the media requirement for it,
    # letting a genuine schema change through as a half-filled model.
    is_deleted = optional_bool(item.get("takeDown"))
    is_private = optional_bool(item.get("privateItem")) or optional_bool(item.get("secret"))

    image_post = item.opt_child("imagePost")
    album = image_post is not None and bool(image_post.opt_children("images"))
    kind = ContentKind.IMAGE_ALBUM if album else ContentKind.VIDEO

    video = item.opt_child("video")
    # A taken-down or private post keeps its metadata but drops its media URLs.
    media = _media(item, kind, video, image_post, require_media=not (is_deleted or is_private))

    duration_ms = None
    if kind is ContentKind.VIDEO and video is not None:
        duration_ms = seconds_to_ms(video.get("duration"))

    poi = item.opt_child("poi")
    return Content(
        platform=_PLATFORM,
        content_id=content_id,
        kind=kind,
        web_url=content_web_url(author.unique_id or "", content_id, kind),
        title=title,
        description=description,
        created_at=epoch_to_datetime(item.get("createTime")),
        duration_ms=duration_ms,
        is_deleted=is_deleted,
        is_private=is_private,
        author=author,
        stats=_stats(item),
        media=media,
        music=_music(item.opt_child("music")),
        tags=_tags(item),
        location=optional_str(poi.get("name")) if poi is not None else None,
        fetched_at=fetched_at,
        raw=item.raw(),
    )


def parse_content(payload: Mapping[str, Any], *, fetched_at: datetime) -> Content:
    """Parse ``/api/item/detail/``."""
    root = _guard(payload)
    item = root.child("itemInfo").child("itemStruct")
    return content_from_node(item, fetched_at=fetched_at)


def parse_author(payload: Mapping[str, Any]) -> Author:
    """Parse ``/api/user/detail/``.

    The counters live beside the user object rather than inside it, which is why
    ``stats`` is threaded in separately.
    """
    root = _guard(payload)
    user_info = root.child("userInfo")
    return author_from_web_node(
        user_info.child("user"),
        stats=user_info.opt_child("stats") or user_info.opt_child("statsV2"),
        include_raw=True,
    )


def parse_author_list(payload: Mapping[str, Any]) -> Page[Author]:
    """Parse ``/api/user/list/``, which answers for both sides of the graph.

    Each entry is the same ``{user, stats}`` pair ``/api/user/detail/`` returns
    for one author, so the entries reuse the profile parser rather than a second
    reading of the same schema.

    The cursor is ``minCursor``. TikTok reports ``hasMore`` honestly here, and a
    hidden list answers with an empty page rather than an error - which is what
    most accounts do for the following side.
    """
    root = _guard(payload)
    # An empty result omits `userList` entirely rather than sending `[]`, which
    # is what a hidden following list looks like - measured 2026-09-08, where
    # the same account returned 30 followers and no `userList` key at all for
    # the following side. Absent is therefore an empty page; a list that IS
    # present stays strict, so a malformed entry is still reported rather than
    # silently dropped.
    entries = root.children("userList") if root.has("userList") else []
    items = [
        author_from_web_node(
            entry.child("user"),
            stats=entry.opt_child("stats") or entry.opt_child("statsV2"),
        )
        for entry in entries
    ]
    has_more = optional_bool(root.present("hasMore"))
    cursor = optional_id(root.get("minCursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("minCursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_author_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/api/post/item_list/``."""
    root = _guard(payload)
    items = [content_from_node(item, fetched_at=fetched_at) for item in root.children("itemList")]
    has_more = optional_bool(root.present("hasMore"))
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def comment_from_node(
    node: Node,
    *,
    content_id: str | None = None,
    parent_id: str | None = None,
) -> Comment:
    """Build a :class:`Comment` from one aweme-schema comment object."""
    resolved_content_id = optional_id(node.get("aweme_id")) or content_id
    if resolved_content_id is None:
        raise node.missing("aweme_id")
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
        author=author_from_comment_node(node.child("user")),
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
    # ``optional_id`` and not ``optional_str``: a page claiming more results
    # while handing back cursor 0 would send the paginator back to page one for
    # ever, which is a schema change, not a last page.
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_comments(payload: Mapping[str, Any], *, content_id: str | None = None) -> Page[Comment]:
    """Parse ``/api/comment/list/``."""
    return _comment_page(payload, content_id=content_id, parent_id=None)


def parse_comment_replies(
    payload: Mapping[str, Any],
    *,
    content_id: str | None = None,
    parent_id: str | None = None,
) -> Page[Comment]:
    """Parse ``/api/comment/list/reply/``."""
    return _comment_page(payload, content_id=content_id, parent_id=parent_id)


def parse_reply_previews(
    payload: Mapping[str, Any], *, content_id: str | None = None
) -> list[Comment]:
    """Extract the replies TikTok inlines under each top level comment."""
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
    "author_from_comment_node",
    "author_from_web_node",
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
