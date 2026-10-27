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

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

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
    LiveViewer,
    Media,
    Mix,
    Music,
    MusicDetail,
    Page,
    TrendingTopic,
    VideoStream,
)
from dtk.platforms.common import (
    Node,
    clean_urls,
    epoch_to_datetime,
    image_from_url_list,
    image_from_urls,
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
        # Same value as `uid` here, deliberately. See `Author.sec_uid`: a caller
        # reading this field should not have to know which platform it is
        # looking at to know which field holds a usable id.
        sec_uid=sec_uid,
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


def _live_clip(item: Node, *, require_media: bool) -> VideoStream | None:
    """The clip behind a live photo slide, or ``None`` for a still.

    Measured 2026-10-20 on one author's twenty posts: a live slide carries
    ``live_photo_type: 1`` and ``clip_type: 5`` beside a ``video`` of its own,
    whose ``play_addr`` is about five seconds of 720p MP4. A still has no
    ``video`` at all, or ``"video": null``. The post-level ``video`` of such a
    post is no help - its ``play_addr`` is the background music.

    A slide that says it is live and has no readable clip raises, the way a
    slide with no picture does: returning the still alone is exactly the half
    answer #792 was about, and it would go unnoticed for as long as it did
    before.
    """
    video = item.opt_child("video")
    addr = video.opt_child("play_addr") if video is not None else None
    clip = _stream(addr, watermark=False, fallback=video) if addr is not None else None
    if clip is None and require_media and optional_int(item.get("live_photo_type")):
        if video is None:
            raise item.missing("video")
        raise video.missing("play_addr" if addr is None else "play_addr.url_list")
    return clip


def _album_images(detail: Node, *, require_media: bool) -> list[AlbumImage]:
    images: list[AlbumImage] = []
    for item in detail.opt_children("images"):
        image = image_from_url_list(item.data)
        if image is None:
            raise item.missing("url_list")
        images.append(
            AlbumImage(
                **image.model_dump(),
                video=_live_clip(item, require_media=require_media),
            )
        )
    return images


def _media(detail: Node, kind: ContentKind, *, require_media: bool) -> Media:
    video = detail.opt_child("video")
    covers = _covers(video)

    if kind is ContentKind.IMAGE_ALBUM:
        images = _album_images(detail, require_media=require_media)
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


#: The ``status`` on a ``collects_list`` entry that means "anyone can open it".
#:
#: Measured 2026-09-14 on one account holding two folders it had set either
#: way: the public one came back 1 and the private one 0.
#:
#: NOT the same numbers as TikTok, which uses 3 for public and 1 for private on
#: a field with the same name. So 1 means public here and private there, and
#: anything that tried to share one constant between the two platforms would be
#: wrong in the most quietly damaging direction available - reporting a private
#: folder as public. They are deliberately defined separately, in each
#: platform's own parser, and neither is exported.
_STATUS_PUBLIC: Final = 1
_STATUS_PRIVATE: Final = 0


def _is_public(node: Node) -> bool | None:
    """Whether this folder is open to anyone, or None when unmeasured."""
    status = optional_int(node.get("status"))
    if status == _STATUS_PUBLIC:
        return True
    if status == _STATUS_PRIVATE:
        return False
    return None


def _collection_from_node(node: Node) -> Collection:
    """One folder from a ``collects_list`` entry.

    Douyin states the id twice, as a JSON number and as ``collects_id_str``.
    The string is the one taken: a 19-digit id exceeds the JavaScript safe
    integer range, and the numeric copy has already lost precision by the time
    it reaches here.
    """
    owner = node.child("user_info") if node.has("user_info") else None
    return Collection(
        platform=_PLATFORM,
        collection_id=node.id("collects_id_str"),
        name=node.text("collects_name"),
        cover=image_from_url_list(node.get("collects_cover")),
        item_count=optional_int(node.get("total_number")),
        is_public=_is_public(node),
        owner_id=optional_id(node.get("user_id_str")),
        # Douyin states a display nickname here where TikTok states an
        # @handle. Both are "what the platform prints beside the folder" and
        # neither is a stable identifier - that is owner_id.
        owner_name=optional_str(owner.get("nickname")) if owner is not None else None,
        raw=node.raw(),
    )


def parse_author_collections(payload: Mapping[str, Any]) -> Page[Collection]:
    """Parse ``/aweme/v1/web/collects/list/``.

    Always the signed-in identity's own folders - the request carries no user
    id and cannot be pointed at anyone else. See ``author_collections_params``.

    The cursor is a plain item offset under ``cursor``, not the ``max_cursor``
    timestamp ``author_posts`` pages by.
    """
    root = _guard(payload)
    entries = root.listed_children("collects_list")
    items = [_collection_from_node(entry) for entry in entries]
    has_more = optional_bool(root.present("has_more"))
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_collection_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/aweme/v1/web/collects/video/list/``.

    Ordinary posts in the ordinary ``aweme_list`` envelope, so the items reuse
    ``content_from_node`` - but this cannot reuse :func:`parse_author_posts`,
    and the reason is one key: this endpoint pages by ``cursor`` where
    ``author_posts`` pages by ``max_cursor``.

    Reusing the other parser would have worked on every single-page folder and
    then raised "missing max_cursor" the first time someone had enough saved
    posts to need a second page. Measured against the live endpoint, which
    returned ``cursor`` and no ``max_cursor`` at all.
    """
    root = _guard(payload)
    items = [content_from_node(item, fetched_at=fetched_at) for item in root.children("aweme_list")]
    has_more = optional_bool(root.present("has_more"))
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_related_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/aweme/v1/web/aweme/related/``: one batch of recommendations.

    The answer says ``has_more: 1`` and carries no cursor; the page asks for
    another batch with state of its own (see ``related_posts_params``), so a
    batch is returned as a last page rather than one promising a next that
    nothing here could ask for.
    """
    root = _guard(payload)
    items = [content_from_node(item, fetched_at=fetched_at) for item in root.children("aweme_list")]
    return Page(items=items, cursor=None, has_more=False)


def music_web_url(music_id: str) -> str:
    return f"{WEB_DOMAIN}/music/{music_id}"


def _music_owner(node: Node) -> Author | None:
    """Who published a sound, from the ``owner_*`` fields beside it.

    Not a user object: the detail flattens its publisher into ``sec_uid``,
    ``owner_nickname`` and ``owner_handle`` with the avatar alongside. A
    library track carries neither and has no owner rather than half of one.
    """
    sec_uid = optional_str(node.get("sec_uid"))
    nickname = optional_str(node.get("owner_nickname"))
    if sec_uid is None or nickname is None:
        return None
    return Author(
        platform=_PLATFORM,
        uid=sec_uid,
        sec_uid=sec_uid,
        unique_id=optional_str(node.get("owner_handle")),
        nickname=nickname,
        avatar=_avatar(node),
        web_url=author_web_url(sec_uid),
    )


def parse_music_detail(payload: Mapping[str, Any]) -> MusicDetail:
    """Parse ``/aweme/v1/web/music/detail/``.

    ``is_original`` is read from ``is_original_sound``, not from the field
    called ``is_original``: measured 2026-10-20, a sound lifted from a post
    said ``is_original_sound: true`` and ``is_original: false``, the latter
    being whether a musician released it as their own track.
    """
    node = _guard(payload).child("music_info")
    music_id = optional_id(node.get("id_str")) or node.id("mid")
    base = _music(node) or Music()
    return MusicDetail(
        **base.model_dump(exclude={"music_id"}),
        music_id=music_id,
        platform=_PLATFORM,
        web_url=music_web_url(music_id),
        content_count=optional_int(node.get("user_count")),
        is_original=optional_bool(node.get("is_original_sound")),
        owner=_music_owner(node),
        raw=node.raw(),
    )


def parse_music_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/aweme/v1/web/music/aweme/``: posts, paged by an offset ``cursor``.

    The ``cursor`` key and not ``max_cursor``, the same split as
    :func:`parse_collection_posts`; one function does not serve both.
    """
    return parse_collection_posts(payload, fetched_at=fetched_at)


def hashtag_web_url(hashtag_id: str) -> str:
    return f"{WEB_DOMAIN}/hashtag/{hashtag_id}"


def parse_hashtag_detail(payload: Mapping[str, Any]) -> Hashtag:
    """Parse ``/aweme/v1/web/challenge/detail/``.

    The cover is ``hashtag_profile``, a bare URL rather than the image holder
    every other picture here comes in; ``cover_photo`` was null on the
    hashtags measured.
    """
    node = _guard(payload).child("ch_info")
    hashtag_id = node.id("cid")
    return Hashtag(
        platform=_PLATFORM,
        hashtag_id=hashtag_id,
        name=node.text("cha_name"),
        description=optional_str(node.get("desc")),
        cover=image_from_urls(node.get("hashtag_profile"))
        or image_from_url_list(node.get("cover_photo")),
        web_url=hashtag_web_url(hashtag_id),
        content_count=optional_int(node.get("user_count")),
        view_count=optional_int(node.get("view_count")),
        raw=node.raw(),
    )


def mix_web_url(mix_id: str) -> str:
    return f"{WEB_DOMAIN}/collection/{mix_id}"


def _mix_from_node(node: Node) -> Mix:
    """One ``mix_infos`` entry.

    ``statis.updated_to_episode`` is how many posts the mix holds - eight on a
    mix whose page lists eight - and ``play_vv`` its plays.
    """
    mix_id = node.id("mix_id")
    statis = node.opt_child("statis")
    owner = node.opt_child("author")
    return Mix(
        platform=_PLATFORM,
        mix_id=mix_id,
        name=node.text("mix_name"),
        description=optional_str(node.get("desc")),
        cover=image_from_url_list(node.get("cover_url")),
        item_count=optional_int(statis.get("updated_to_episode")) if statis else None,
        play_count=optional_int(statis.get("play_vv")) if statis else None,
        web_url=mix_web_url(mix_id),
        owner_id=optional_str(owner.get("sec_uid")) if owner else None,
        owner_name=optional_str(owner.get("nickname")) if owner else None,
        created_at=epoch_to_datetime(node.get("create_time")),
        updated_at=epoch_to_datetime(node.get("update_time")),
        raw=node.raw(),
    )


def parse_author_mixes(payload: Mapping[str, Any]) -> Page[Mix]:
    """Parse ``/aweme/v1/web/mix/list/``, paged by an offset ``cursor``."""
    root = _guard(payload)
    items = [_mix_from_node(entry) for entry in root.listed_children("mix_infos")]
    has_more = optional_bool(root.present("has_more"))
    cursor = optional_id(root.get("cursor")) if has_more else None
    if has_more and cursor is None:
        raise root.missing("cursor")
    return Page(items=items, cursor=cursor, has_more=has_more)


LIVE_DOMAIN: Final = "https://live.douyin.com"

#: A room's ``status`` while it broadcasts; 4 is an ended broadcast. Measured
#: 2026-10-20 on one room of each.
_LIVE_STATUS_ON: Final = 2

#: Where each stream format's URLs sit under ``stream_url``, keyed by quality.
_PULL_URLS: Final[tuple[tuple[str, str], ...]] = (
    ("flv_pull_url", "flv"),
    ("hls_pull_url_map", "hls"),
)


def live_web_url(web_rid: str) -> str:
    return f"{LIVE_DOMAIN}/{web_rid}"


def _live_streams(room: Node) -> list[LiveStream]:
    stream = room.opt_child("stream_url")
    if stream is None:
        return []
    streams: list[LiveStream] = []
    for key, fmt in _PULL_URLS:
        urls = stream.opt_child(key)
        if urls is None:
            continue
        for quality, url in urls.data.items():
            if isinstance(url, str) and url:
                streams.append(LiveStream(url=url, format=fmt, quality=str(quality)))
    return streams


def parse_live_room(payload: Mapping[str, Any], *, room_id: str | None = None) -> LiveRoom:
    """Parse ``live.douyin.com/webcast/room/web/enter/``.

    The room is the first of ``data.data``, the broadcaster is ``data.user``.
    The answer never repeats the ``web_rid`` it was asked by, so the caller's
    is what ``room_id`` and the link are built from.

    The viewer count is ``room_view_stats.display_value``: ``user_count_str``
    beside it is the rounded figure the page prints ("4000+" over 4474).
    """
    data = _guard(payload).child("data")
    rooms = data.children("data")
    if not rooms:
        raise data.missing("data[0]")
    room = rooms[0]
    is_live = optional_int(room.get("status")) == _LIVE_STATUS_ON
    user = data.opt_child("user")
    sec_uid = optional_str(user.get("sec_uid")) if user else None
    author = (
        Author(
            platform=_PLATFORM,
            uid=sec_uid,
            sec_uid=sec_uid,
            nickname=user.text("nickname"),
            avatar=image_from_url_list(user.get("avatar_thumb")),
            web_url=author_web_url(sec_uid),
        )
        if user is not None and sec_uid is not None
        else None
    )
    stats = room.opt_child("room_view_stats")
    viewers = optional_int(stats.get("display_value")) if stats else None
    return LiveRoom(
        platform=_PLATFORM,
        room_id=room_id or room.id("id_str"),
        live_id=room.id("id_str") if is_live else None,
        is_live=is_live,
        title=optional_str(room.get("title")),
        cover=image_from_url_list(room.get("cover")),
        web_url=live_web_url(room_id) if room_id else None,
        viewer_count=viewers if is_live else None,
        like_count=optional_int(room.get("like_count")),
        author=author,
        streams=_live_streams(room) if is_live else [],
        raw=data.raw(),
    )


def _live_author(node: Node | None) -> Author | None:
    """A broadcaster or a viewer as the live site describes one."""
    sec_uid = optional_str(node.get("sec_uid")) if node else None
    if node is None or sec_uid is None:
        return None
    return Author(
        platform=_PLATFORM,
        uid=sec_uid,
        sec_uid=sec_uid,
        unique_id=optional_str(node.get("display_id")),
        nickname=node.text("nickname"),
        avatar=image_from_url_list(node.get("avatar_thumb")),
        web_url=author_web_url(sec_uid),
    )


def parse_live_room_by_id(payload: Mapping[str, Any]) -> LiveRoom:
    """Parse ``webcast.amemv.com/webcast/room/reflow/info/``: ``data.room``.

    The room a share link from the app names, by the broadcast's own id. The
    answer carries the room's ``web_rid`` as ``owner.web_rid``, so ``room_id``
    is the number ``/live`` and a live.douyin.com link take, as it is when the
    room is asked for that way.

    Two counts are not where :func:`parse_live_room` finds them. Here
    ``room_view_stats`` counts who has watched - it prints "349K watched" -
    and who is in the room now is ``user_count``: measured 2026-10-26 on one
    room asked both ways, 16,109 here against 17,138 from ``web/enter``
    minutes later, beside a ``display_value`` of 348,959. ``data.user`` is the
    asking session, empty for a guest, and not the broadcaster, who is
    ``room.owner``.

    A room id nobody has is ``status_code: 10011``, "Request params error".
    """
    room = _guard(payload).child("data").child("room")
    owner = room.opt_child("owner")
    is_live = optional_int(room.get("status")) == _LIVE_STATUS_ON
    web_rid = optional_id(owner.get("web_rid")) if owner else None
    return LiveRoom(
        platform=_PLATFORM,
        # A room whose web_rid did not come back is still this room; its own
        # id is the only name left for it.
        room_id=web_rid or room.id("id_str"),
        live_id=room.id("id_str") if is_live else None,
        is_live=is_live,
        title=optional_str(room.get("title")),
        cover=image_from_url_list(room.get("cover")),
        web_url=live_web_url(web_rid) if web_rid else None,
        viewer_count=optional_int(room.get("user_count")) if is_live else None,
        like_count=optional_int(room.get("like_count")),
        author=_live_author(owner),
        streams=_live_streams(room) if is_live else [],
        raw=room.raw(),
    )


def parse_live_status(
    payload: Mapping[str, Any], *, live_ids: Sequence[str] = ()
) -> Page[LiveStatus]:
    """Parse ``webcast.amemv.com/webcast/room/check_alive/``.

    ``data`` is a list with one ``{alive, room_id}`` per broadcast that
    existed, in the order asked. Unlike TikTok's answer there is no
    ``room_id_str`` beside ``room_id`` - Python's JSON reader keeps every
    digit of the 64-bit integer - and while a broadcast that has ended is
    ``alive: false``, an id that was never a broadcast is left out of
    ``data`` rather than returned as ``false`` (an ask of only nonexistent
    ids answers ``data: []``, measured 2026-10-26).

    ``live_ids`` puts those back as ``false``. Left out, the page was one
    entry short, and a caller pairing its list with the page by position -
    which "in the order asked" invites - read every room after the gap off
    its neighbour's answer.
    """
    answered = {entry.id("room_id"): _alive(entry) for entry in _guard(payload).children("data")}
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


def parse_live_ranking(payload: Mapping[str, Any]) -> Page[LiveViewer]:
    """Parse ``live.douyin.com/webcast/ranklist/audience/``: ``data.ranks``.

    Up to 200 viewers, in the order the room's own list puts them, each a
    ``rank`` and a ``user``. Measured 2026-10-26 on seven rooms as a guest:

    - Every ``score`` was 0, and ``total`` beside the list 0 too, so the
      order is given and what it is ordered by is not, and neither is put in
      the answer as though it meant something.
    - A viewer marked ``is_hidden`` - one who chose not to be listed - still
      came back with their account. They are passed on as a place without
      a viewer: the platform's choice to send them is not their choice.
    - One room named all seven of its viewers to a guest only as a masked
      nickname ("Li***"), an ``id_str`` of 111111 and no ``sec_uid``. That is
      nobody this API could be asked about again, so no author either.

    A broadcast nobody has is ``status_code: 10011``. ``has_more`` was false
    on every room, the list being the room's top 200 rather than a page of it.
    """
    data = _guard(payload).child("data")
    viewers = [
        LiveViewer(
            platform=_PLATFORM,
            rank=_place(entry),
            author=None
            if optional_bool(entry.get("is_hidden"))
            else _live_author(entry.opt_child("user")),
        )
        for entry in data.listed_children("ranks")
    ]
    return Page(items=viewers, cursor=None, has_more=False)


def _place(entry: Node) -> int:
    """A ranking place, from 1. Absent or 0 is a changed answer: a list with
    a viewer in no place is not one the room's page could show."""
    place = optional_positive_int(entry.get("rank"))
    if place is None:
        raise entry.missing("rank")
    return place


def hot_web_url(sentence_id: str) -> str:
    """The hot page's own page for one line, by its ``sentence_id``."""
    return f"{WEB_DOMAIN}/hot/{sentence_id}"


def parse_trending(payload: Mapping[str, Any]) -> Page[TrendingTopic]:
    """Parse ``/aweme/v1/web/hot/search/list/``: ``data.word_list``, whole.

    The main board opens with a line that has no ``position`` and a
    ``hot_value`` of 0 - pinned above the ranking - and that is what a rank
    of ``None`` says. ``trending_list`` beside the words is a second, shorter
    list of rising topics the page shows apart, and is left in ``raw``.
    """
    data = _guard(payload).child("data")
    items: list[TrendingTopic] = []
    for line in data.listed_children("word_list"):
        sentence = optional_id(line.get("sentence_id"))
        rank = optional_positive_int(line.get("position"))
        items.append(
            TrendingTopic(
                platform=_PLATFORM,
                word=line.text("word"),
                rank=rank,
                hot_value=optional_int(line.get("hot_value")) if rank else None,
                content_count=optional_int(line.get("video_count")),
                cover=image_from_url_list(line.get("word_cover")),
                web_url=hot_web_url(sentence) if sentence else None,
                raw=line.raw(),
            )
        )
    return Page(items=items, cursor=None, has_more=False)


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
    "LIVE_DOMAIN",
    "RISK_STATUS_CODES",
    "WEB_DOMAIN",
    "author_from_node",
    "author_web_url",
    "comment_from_node",
    "content_from_node",
    "content_web_url",
    "detect_risk_control",
    "hashtag_web_url",
    "hot_web_url",
    "live_web_url",
    "mix_web_url",
    "music_web_url",
    "parse_author",
    "parse_author_mixes",
    "parse_author_posts",
    "parse_comment_replies",
    "parse_comments",
    "parse_content",
    "parse_hashtag_detail",
    "parse_live_ranking",
    "parse_live_room",
    "parse_live_room_by_id",
    "parse_live_status",
    "parse_music_detail",
    "parse_music_posts",
    "parse_related_posts",
    "parse_reply_previews",
    "parse_trending",
]
