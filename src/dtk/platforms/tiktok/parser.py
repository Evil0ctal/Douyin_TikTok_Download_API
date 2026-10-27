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

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from urllib.parse import quote

from dtk.core.errors import UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.models import (
    AlbumImage,
    Author,
    AuthorStats,
    Collection,
    Comment,
    Content,
    ContentStats,
    Hashtag,
    Image,
    LiveRoom,
    LiveStatus,
    LiveStream,
    Media,
    Mix,
    Music,
    MusicDetail,
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
        # The id every TikTok author endpoint keys on, and a different value
        # from `uid`. Optional rather than required: it is present in every
        # author node measured so far, but a missing one should degrade to
        # "look this author up" rather than fail the whole parse.
        sec_uid=optional_str(node.get("secUid")),
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
        # snake_case here: the comment endpoints are the one place TikTok keeps
        # the aweme spelling, `sec_uid` included.
        sec_uid=optional_str(node.get("sec_uid")),
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


def _album_images(image_post: Node) -> list[AlbumImage]:
    images: list[AlbumImage] = []
    for item in image_post.children("images"):
        image = _image_from_url_holder(item)
        if image is None:
            raise item.missing("imageURL")
        # `video` stays None: the slides of a photo post here carry an
        # `imageURL` and no clip, unlike a Douyin live photo's.
        images.append(
            AlbumImage(
                url=image.url,
                urls=image.urls,
                width=optional_int(item.get("imageWidth")) or image.width,
                height=optional_int(item.get("imageHeight")) or image.height,
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
    # the following side. Absent is therefore an empty page, and so is null,
    # which platforms send for an empty list as often as they drop the key; a
    # list that IS present stays strict, so a malformed entry is still
    # reported rather than silently dropped.
    entries = root.listed_children("userList")
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


def parse_related_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/api/related/item_list/``: one batch, with ``cursor: -1``."""
    root = _guard(payload)
    items = [content_from_node(item, fetched_at=fetched_at) for item in root.children("itemList")]
    return Page(items=items, cursor=None, has_more=False)


_SLUG_JUNK: Final = re.compile(r"[^0-9A-Za-z]+")


def music_web_url(music_id: str, title: str | None) -> str:
    """``/music/<title>-<id>``, as the site links a sound.

    Only the id is read: ``original-sound-<id>``, ``x-<id>`` and ``-<id>``
    all served the same page on 2026-10-20. The title is there because it is
    what the site's own links carry, reduced to the characters they use.
    """
    slug = _SLUG_JUNK.sub("-", title or "").strip("-").lower()
    return f"{WEB_DOMAIN}/music/{slug}-{music_id}"


def parse_music_detail(payload: Mapping[str, Any]) -> MusicDetail:
    """Parse ``/api/music/detail/``.

    The sound, its counters and its publisher arrive as three siblings under
    ``musicInfo``. ``author`` is absent for a library track rather than empty,
    and the sound then has no owner.
    """
    info = _guard(payload).child("musicInfo")
    node = info.child("music")
    music_id = node.id("id")
    base = _music(node) or Music()
    stats = info.opt_child("stats")
    owner = info.opt_child("author")
    return MusicDetail(
        **base.model_dump(exclude={"music_id"}),
        music_id=music_id,
        platform=_PLATFORM,
        web_url=music_web_url(music_id, base.title),
        content_count=optional_int(stats.get("videoCount")) if stats is not None else None,
        is_original=optional_bool(node.get("original")) if node.has("original") else None,
        owner=author_from_web_node(owner) if owner is not None else None,
        raw=info.raw(),
    )


def parse_music_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/api/music/item_list/``, the shared item_list envelope."""
    return parse_author_posts(payload, fetched_at=fetched_at)


def hashtag_web_url(name: str) -> str:
    return f"{WEB_DOMAIN}/tag/{quote(name, safe='')}"


def parse_hashtag_detail(payload: Mapping[str, Any]) -> Hashtag:
    """Parse ``/api/challenge/detail/``.

    The counters are read from ``statsV2``, as a post's are: measured
    2026-10-20, ``stats.videoCount`` was 0 beside a ``statsV2.videoCount`` of
    194388, and ``stats.viewCount`` a rounded 5200000000 beside the exact
    figure. ``stats`` is the fallback, for an answer that has nothing else.
    """
    info = _guard(payload).child("challengeInfo")
    node = info.child("challenge")
    primary = info.opt_child("statsV2")
    secondary = info.opt_child("stats") or node.opt_child("stats")

    def metric(name: str) -> int | None:
        for source in (primary, secondary):
            value = optional_int(source.get(name)) if source is not None else None
            if value is not None:
                return value
        return None

    name = node.text("title")
    return Hashtag(
        platform=_PLATFORM,
        hashtag_id=node.id("id"),
        name=name,
        description=optional_str(node.get("desc")),
        cover=image_from_urls(
            node.get("coverLarger"),
            node.get("coverMedium"),
            node.get("profileLarger"),
            node.get("profileMedium"),
        ),
        web_url=hashtag_web_url(name),
        content_count=metric("videoCount"),
        view_count=metric("viewCount"),
        raw=info.raw(),
    )


def parse_hashtag_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/api/challenge/item_list/``, the shared item_list envelope."""
    return parse_author_posts(payload, fetched_at=fetched_at)


def parse_search_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/api/search/item/full/``.

    The items are ordinary ``itemStruct`` objects; the envelope is snake_case
    - ``item_list``, ``has_more`` as 0 or 1, a numeric ``cursor`` - and drops
    ``item_list`` altogether on a page with nothing in it.
    """
    root = _guard(payload)
    items = [
        content_from_node(item, fetched_at=fetched_at) for item in root.listed_children("item_list")
    ]
    has_more = optional_bool(root.get("has_more")) and bool(items)
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def author_from_search_node(node: Node) -> Author:
    """An account as the user search lists it: the comment endpoints' snake_case
    user object, with the counters beside the name rather than in a block."""
    author = author_from_comment_node(node)
    stats = AuthorStats(
        follower_count=optional_int(node.get("follower_count")),
        following_count=optional_int(node.get("following_count")),
        content_count=optional_int(node.get("aweme_count")),
        total_digg=optional_int(node.get("total_favorited")),
    )
    return author.model_copy(update={"stats": None if stats == AuthorStats() else stats})


def parse_search_authors(payload: Mapping[str, Any]) -> Page[Author]:
    """Parse ``/api/search/user/full/``: ``user_list`` of ``{user_info, ...}``."""
    root = _guard(payload)
    entries = root.listed_children("user_list")
    items = [author_from_search_node(entry.child("user_info")) for entry in entries]
    has_more = optional_bool(root.get("has_more")) and bool(items)
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def playlist_web_url(unique_id: str, name: str, mix_id: str) -> str:
    """``/@<handle>/playlist/<name>-<id>``, as the profile links a playlist.

    The name is reduced the way :func:`music_web_url` reduces a title; only
    the id after the last hyphen is what the URL layer reads back.
    """
    slug = _SLUG_JUNK.sub("-", name).strip("-")
    return f"{WEB_DOMAIN}/@{unique_id}/playlist/{slug}-{mix_id}"


def parse_author_mixes(payload: Mapping[str, Any]) -> Page[Mix]:
    """Parse ``/api/user/playlist/``.

    Each entry names itself twice - ``id``/``mixId``, ``name``/``mixName`` -
    and was the same both times on every playlist measured; the first of each
    is read. An author with none answers no ``playList`` key at all.
    """
    root = _guard(payload)
    items: list[Mix] = []
    for entry in root.listed_children("playList"):
        mix_id = optional_id(entry.get("id")) or entry.id("mixId")
        name = optional_str(entry.get("name")) or entry.text("mixName")
        creator = entry.opt_child("creator")
        handle = optional_str(creator.get("uniqueId")) if creator else None
        items.append(
            Mix(
                platform=_PLATFORM,
                mix_id=mix_id,
                name=name,
                cover=image_from_urls(entry.get("cover")),
                item_count=optional_int(entry.get("videoCount")),
                web_url=playlist_web_url(handle, name, mix_id) if handle else None,
                owner_id=optional_str(creator.get("secUid")) if creator else None,
                owner_name=handle,
                raw=entry.raw(),
            )
        )
    has_more = optional_bool(root.present("hasMore"))
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


#: A room's ``status`` while it broadcasts; 4 is ended, measured 2026-10-20.
_LIVE_STATUS_ON: Final = 2

#: The pull URL keys of one quality, in the order a player would try them.
#: ``cmaf``, ``lls`` and the rest are left out: a player needs to know about
#: them, and the two here are what every player and recorder takes.
_LIVE_FORMATS: Final[tuple[str, ...]] = ("flv", "hls")


def live_web_url(unique_id: str) -> str:
    return f"{WEB_DOMAIN}/@{unique_id}/live"


def _live_streams(pull: Node | None) -> list[LiveStream]:
    """The pull URLs, from a ``pull_data.stream_data``.

    That field is a JSON document inside a string, keyed by quality (``origin``,
    ``hd``, ``sd``, ...), each with ``main.flv`` and ``main.hls``. A string
    that will not decode is a stream the page could not play either, and
    gives no streams rather than failing the room. The web app's room answer
    keeps it under ``streamData``, the webcast host's under
    ``stream_url.live_core_sdk_data``; the document inside is the same.
    """
    text = pull.get("stream_data") if pull else None
    if not isinstance(text, str) or not text:
        return []
    try:
        decoded = json.loads(text)
    except ValueError:
        return []
    qualities = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(qualities, dict):
        return []
    streams: list[LiveStream] = []
    for quality, entry in qualities.items():
        main = entry.get("main") if isinstance(entry, dict) else None
        if not isinstance(main, dict):
            continue
        for fmt in _LIVE_FORMATS:
            url = main.get(fmt)
            if isinstance(url, str) and url:
                streams.append(LiveStream(url=url, format=fmt, quality=str(quality)))
    return streams


def parse_live_room(payload: Mapping[str, Any], *, room_id: str | None = None) -> LiveRoom:
    """Parse ``/api-live/user/room/``: ``data.user`` and ``data.liveRoom``.

    The broadcast's own id is the user's ``roomId``, not anything under
    ``liveRoom``, whose ``streamId`` is the stream's.
    """
    data = _guard(payload).child("data")
    user = data.child("user")
    room = data.child("liveRoom")
    author = author_from_web_node(user)
    is_live = optional_int(room.get("status")) == _LIVE_STATUS_ON
    stats = room.opt_child("liveRoomStats")
    handle = author.unique_id or room_id or ""
    stream = room.opt_child("streamData")
    return LiveRoom(
        platform=_PLATFORM,
        room_id=handle,
        live_id=optional_id(user.get("roomId")) if is_live else None,
        is_live=is_live,
        title=optional_str(room.get("title")),
        cover=image_from_urls(room.get("coverUrl"), room.get("squareCoverImg")),
        web_url=live_web_url(handle) if handle else None,
        viewer_count=optional_int(stats.get("userCount")) if stats and is_live else None,
        started_at=epoch_to_datetime(room.get("startTime")) if is_live else None,
        author=author,
        streams=_live_streams(stream.opt_child("pull_data") if stream else None) if is_live else [],
        raw=data.raw(),
    )


def _webcast_author(owner: Node) -> Author:
    """The broadcaster as the webcast host describes one: snake_case, with
    the @handle as ``display_id`` and the numeric id as ``id_str``."""
    handle = optional_str(owner.get("display_id"))
    return Author(
        platform=_PLATFORM,
        uid=owner.id("id_str"),
        sec_uid=optional_str(owner.get("sec_uid")),
        unique_id=handle,
        nickname=owner.text("nickname"),
        avatar=image_from_url_list(owner.get("avatar_large"))
        or image_from_url_list(owner.get("avatar_thumb")),
        web_url=author_web_url(handle) if handle else None,
        verified=optional_bool(owner.get("verified")),
    )


def parse_live_room_by_id(payload: Mapping[str, Any]) -> LiveRoom:
    """Parse ``webcast.tiktok.com/webcast/room/info/``: the room is ``data``.

    The same room ``/api-live/user/room/`` describes, in the webcast host's own
    spelling, so it is normalised onto the same model: ``room_id`` is still
    the owner's @handle (``owner.display_id``), which is what a room is asked
    for by again once this broadcast is over. Measured 2026-10-26 on one room
    asked both ways: ``user_count`` here equalled ``liveRoomStats.userCount``
    there, and ``create_time`` here equalled ``startTime`` there, so they are
    the viewer count and the start. ``start_time`` beside it was 0.

    A ``room_id`` nobody has is ``status_code: 10011``, "Request params error".
    """
    room = _guard(payload).child("data")
    owner = room.opt_child("owner")
    author = _webcast_author(owner) if owner is not None else None
    is_live = optional_int(room.get("status")) == _LIVE_STATUS_ON
    handle = author.unique_id if author is not None else None
    stream = room.opt_child("stream_url")
    sdk = stream.opt_child("live_core_sdk_data") if stream else None
    return LiveRoom(
        platform=_PLATFORM,
        # A room whose owner's handle did not come back is still this room;
        # its own id is the only name left for it.
        room_id=handle or room.id("id_str"),
        live_id=room.id("id_str") if is_live else None,
        is_live=is_live,
        title=optional_str(room.get("title")),
        cover=image_from_url_list(room.get("cover")),
        web_url=live_web_url(handle) if handle else None,
        viewer_count=optional_int(room.get("user_count")) if is_live else None,
        # No like_count: it was 0 on a room of 249 viewers, beside a
        # stats.digg_count of 0, and the handle answer has no count to check
        # either against (2026-10-26).
        started_at=epoch_to_datetime(room.get("create_time")) if is_live else None,
        author=author,
        streams=_live_streams(sdk.opt_child("pull_data") if sdk else None) if is_live else [],
        raw=room.raw(),
    )


def parse_live_status(
    payload: Mapping[str, Any], *, live_ids: Sequence[str] = ()
) -> Page[LiveStatus]:
    """Parse ``webcast.tiktok.com/webcast/room/check_alive/``.

    ``data`` is a list with one ``{alive, room_id, room_id_str}`` per id asked,
    in the order asked. ``room_id`` is a JSON number past the safe integer
    range, so the id is read from ``room_id_str``. A broadcast nobody has is
    ``alive: false`` like one that has ended: the platform does not tell the
    two apart, and neither can this.

    The page still follows ``live_ids`` rather than ``data``: Douyin's answer
    leaves out an id that never was a broadcast, and should this one start to,
    the page keeps answering every id asked instead of coming back short.
    """
    answered = {
        entry.id("room_id_str"): _alive(entry) for entry in _guard(payload).children("data")
    }
    items = [
        LiveStatus(platform=_PLATFORM, live_id=live_id, is_live=answered.get(live_id, False))
        for live_id in live_ids or answered
    ]
    return Page(items=items, cursor=None, has_more=False)


def _alive(entry: Node) -> bool:
    """``alive`` as the platform sent it. Absent is a changed answer, not a
    "no": reading it as False would report every room off the day it moved."""
    value = entry.present("alive")
    if not isinstance(value, bool):
        raise entry.missing("alive")
    return value


#: The ``status`` value on a collection node that means "anyone can open this".
#:
#: Measured 2026-09-13 on one account holding two folders it had set either
#: way: the public one came back 3 and the private one 1, on both
#: ``/api/user/collection_list/`` and ``/api/collection/detail/``. Two samples
#: on one axis is enough to name 3 and not enough to claim what else exists,
#: which is why :func:`_is_public` returns None rather than False for a value
#: nobody has seen - "I do not know" and "no" are different answers, and a
#: caller deciding whether to bother asking deserves the honest one.
_STATUS_PUBLIC: Final = 3
_STATUS_PRIVATE: Final = 1


def _is_public(node: Node) -> bool | None:
    """Whether this folder is open to anyone, or None when unmeasured."""
    status = optional_int(node.get("status"))
    if status == _STATUS_PUBLIC:
        return True
    if status == _STATUS_PRIVATE:
        return False
    return None


def _collection_from_node(node: Node) -> Collection:
    """One folder, from the node shape both collection endpoints share.

    ``/api/collection/detail/`` returns this same object under
    ``collectionInfo`` that ``/api/user/collection_list/`` returns a list of -
    same keys, same types - so one builder answers for both. Verified against
    live responses from each, 2026-09-13.
    """
    return Collection(
        platform=_PLATFORM,
        collection_id=node.id("collectionId"),
        name=node.text("name"),
        cover=image_from_urls((node.get("cover") or {}).get("urlList")),
        item_count=optional_int(node.get("total")),
        is_public=_is_public(node),
        owner_id=optional_id(node.get("userId")),
        owner_name=optional_str(node.get("userName")),
        raw=node.raw(),
    )


def parse_author_collections(payload: Mapping[str, Any]) -> Page[Collection]:
    """Parse ``/api/user/collection_list/``.

    Same paging shape as :func:`parse_author_posts`: a list under one key, an
    honest ``hasMore``, and a cursor only when there is a next page.

    A guest sees the folders their owner made public and no others, so an empty
    list means "none public" rather than "none" - and never means a broken
    parser. Each entry says which it is; see :func:`_is_public`.
    """
    root = _guard(payload)
    entries = root.listed_children("collectionList")
    items = [_collection_from_node(entry) for entry in entries]
    has_more = optional_bool(root.present("hasMore"))
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_collection_detail(payload: Mapping[str, Any]) -> Collection:
    """Parse ``/api/collection/detail/``.

    One folder, asked for by id rather than by owner, which is the whole reason
    this exists alongside :func:`parse_author_collections`: given only a
    collection id - all a shared link carries - that one cannot help, because
    it needs a ``secUid`` to ask with.

    The payload wraps the same node in ``collectionInfo``. It is not optional
    the way ``collectionList`` is: a response that reached here at all was a
    success, so a missing wrapper is a changed upstream, not an absence.
    """
    root = _guard(payload)
    return _collection_from_node(root.child("collectionInfo"))


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
    "author_from_search_node",
    "author_from_web_node",
    "author_web_url",
    "comment_from_node",
    "content_from_node",
    "content_web_url",
    "detect_risk_control",
    "hashtag_web_url",
    "live_web_url",
    "music_web_url",
    "parse_author",
    "parse_author_collections",
    "parse_author_mixes",
    "parse_author_posts",
    "parse_comment_replies",
    "parse_comments",
    "parse_content",
    "parse_hashtag_detail",
    "parse_hashtag_posts",
    "parse_live_room",
    "parse_live_room_by_id",
    "parse_live_status",
    "parse_music_detail",
    "parse_music_posts",
    "parse_related_posts",
    "parse_reply_previews",
    "parse_search_authors",
    "parse_search_posts",
    "playlist_web_url",
]
