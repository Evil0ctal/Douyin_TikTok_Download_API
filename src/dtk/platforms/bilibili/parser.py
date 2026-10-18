"""Pure parsers turning Bilibili web responses into the normalized models.

Same contract as the Douyin and TikTok parsers: decoded JSON in, models out,
no IO. Every Bilibili answer is one envelope - ``code``, ``message``, ``data``
- and the classifier has already turned a non-zero ``code`` into a business
error or a refusal before anything here runs, so these read ``data``.

Where Bilibili differs from the other two, and how it lands in the models:

* **A video is keyed by its bvid**, which every link shows; the numeric aid
  stays in ``raw``. ``uid`` is the uploader's ``mid``, the one id Bilibili
  has for a user, and ``sec_uid`` repeats it as Douyin's parser does: a caller
  reading ``sec_uid`` gets the id the author endpoints accept on every
  platform without knowing which one it is looking at. ``unique_id`` is
  ``None``; Bilibili has no handle.
* **Counters.** ``view`` is ``play_count``, ``like`` is ``digg_count``,
  ``favorite`` is ``collect_count``. Coins and danmaku have no place in the
  shared model and are kept in ``raw``.
* **The playable file arrives separately.** The detail answer has no stream
  in it; the fetch layer asks ``playurl`` for one (see
  :meth:`dtk.platforms.bilibili.adapter.BilibiliAdapter.follow_up`) and folds
  its ``data`` in under :data:`STREAMS_KEY`. A detail without it parses into a
  video with no stream rather than failing - the rest of the answer is real.
* **There are no image albums.** Every item is a video.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from dtk.core.errors import UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.models import (
    Author,
    AuthorStats,
    Collection,
    Comment,
    Content,
    ContentStats,
    Image,
    Media,
    Page,
    VideoStream,
)
from dtk.platforms.bilibili.ids import av_to_bv
from dtk.platforms.common import (
    Node,
    clean_urls,
    epoch_to_datetime,
    optional_bool,
    optional_int,
    optional_str,
    seconds_to_ms,
)

_PLATFORM: Final = Platform.BILIBILI
_NAME: Final = _PLATFORM.value

WEB_DOMAIN: Final = "https://www.bilibili.com"
SPACE_DOMAIN: Final = "https://space.bilibili.com"

#: Where the fetch layer puts the ``playurl`` answer inside a detail payload.
#: Prefixed so it can never collide with a field Bilibili adds to ``data``.
STREAMS_KEY: Final = "__dtk_streams"

#: Envelope codes that are a refusal of the caller rather than an answer.
#: -352 "risk check failed" and -412 "request intercepted" were both seen on
#: 2026-10-18; -509 and -799 are Bilibili's "too frequent". The classifier
#: holds the same table, and this is the guard for a payload that reaches a
#: parser some other way.
RISK_CODES: Final[frozenset[int]] = frozenset({-352, -412, -509, -799})


def detect_risk_control(payload: Mapping[str, Any]) -> str | None:
    """Name the risk-control signature in ``payload``, or ``None``."""
    if not payload:
        return "empty_body"
    code = optional_int(payload.get("code"))
    if code is not None and code in RISK_CODES:
        return f"code:{code}"
    data = payload.get("data")
    if isinstance(data, Mapping):
        # `arc/search` answers a scored-as-risky request with code 0 and this.
        if data.get("is_risk") is True:
            return "data.is_risk"
        if data.get("v_voucher"):
            return "data.v_voucher"
    return None


def _guard(payload: Mapping[str, Any]) -> Node:
    signal = detect_risk_control(payload)
    if signal is not None:
        raise UpstreamRiskControl(
            f"bilibili risk control response: {signal}",
            details={"platform": _NAME, "signal": signal},
        )
    return Node(payload, "", _NAME)


def content_web_url(bvid: str) -> str:
    return f"{WEB_DOMAIN}/video/{bvid}"


def author_web_url(mid: str) -> str:
    return f"{SPACE_DOMAIN}/{mid}"


def _https(url: Any) -> str | None:
    """Bilibili still hands out ``http://`` image links; the hosts serve HTTPS."""
    text = optional_str(url)
    if text is None:
        return None
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://"):
        return "https://" + text[len("http://") :]
    return text


def _prose(value: Any) -> str:
    """Text the uploader wrote, exactly as written - or empty.

    Not :func:`optional_str`, which trims: a description is the uploader's,
    trailing spaces and blank lines included, and it is stored as such.
    """
    return value if isinstance(value, str) else ""


def _image(url: Any) -> Image | None:
    link = _https(url)
    return Image(url=link, urls=[link]) if link else None


def _author(node: Node, *, name_key: str = "name", face_key: str | None = "face") -> Author:
    """An uploader as a list or a detail names them. ``face_key`` is None where
    the list carries no avatar at all, which the space list does not."""
    mid = node.id("mid")
    return Author(
        platform=_PLATFORM,
        uid=mid,
        sec_uid=mid,
        nickname=node.text(name_key),
        avatar=_image(node.get(face_key)) if face_key else None,
        web_url=author_web_url(mid),
    )


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------


def _stream(data: Node) -> VideoStream | None:
    """The progressive MP4 in a ``playurl`` answer, mirrors included.

    Only a single-part MP4 is a stream here. Bilibili answers some requests
    with the file cut into several ``durl`` segments, and handing the first one
    on as "the video" would store a third of it under the full name.
    """
    streams = data.opt_child(STREAMS_KEY)
    if streams is None:
        return None
    parts = streams.opt_children("durl")
    if len(parts) != 1:
        return None
    part = parts[0]
    fmt = optional_str(streams.get("format")) or ""
    if not fmt.startswith("mp4"):
        return None
    urls = clean_urls([part.get("url"), *(part.get("backup_url") or [])])
    if not urls:
        return None
    width, height = _dimension(data.opt_child("dimension"))
    return VideoStream(
        url=urls[0],
        urls=urls,
        width=width,
        height=height,
        format="mp4",
        size_bytes=optional_int(part.get("size")),
        watermark=False,
    )


def _dimension(node: Node | None) -> tuple[int | None, int | None]:
    if node is None:
        return None, None
    width, height = optional_int(node.get("width")), optional_int(node.get("height"))
    # A portrait upload is stored landscape with `rotate: 1`.
    if optional_int(node.get("rotate")) == 1:
        return height, width
    return width, height


def _state(node: Node) -> tuple[bool, bool]:
    """``(is_deleted, is_private)`` from an archive ``state``.

    0 is open. Bilibili answers a deleted video with ``-404`` rather than a
    payload, so a negative state reaching a parser is one still shown to
    somebody - under review, locked, or visible to its uploader only.
    """
    state = optional_int(node.get("state"))
    return False, state is not None and state < 0


def parse_content(payload: Mapping[str, Any], *, fetched_at: datetime) -> Content:
    """Parse ``/x/web-interface/wbi/view``, with ``playurl`` folded in if it came.

    A video in several parts is one post here; the stream is the first part's,
    since that is what its link plays.
    """
    data = _guard(payload).child("data")
    bvid = data.id("bvid")
    stat = data.child("stat")
    is_deleted, is_private = _state(data)
    cover = _image(data.get("pic"))
    return Content(
        platform=_PLATFORM,
        content_id=bvid,
        kind=ContentKind.VIDEO,
        web_url=content_web_url(bvid),
        title=data.text("title"),
        description=_prose(data.get("desc")),
        created_at=epoch_to_datetime(data.get("pubdate")),
        duration_ms=seconds_to_ms(data.get("duration")),
        is_deleted=is_deleted,
        is_private=is_private,
        author=_author(data.child("owner")),
        stats=ContentStats(
            play_count=optional_int(stat.get("view")),
            digg_count=optional_int(stat.get("like")),
            comment_count=optional_int(stat.get("reply")),
            share_count=optional_int(stat.get("share")),
            collect_count=optional_int(stat.get("favorite")),
        ),
        media=Media(
            covers=[cover] if cover else [],
            video=_stream(data),
        ),
        fetched_at=fetched_at,
        raw=data.raw(),
    )


def first_part_cid(payload: Mapping[str, Any]) -> str | None:
    """The part a detail's link plays, for the follow-up ``playurl`` request."""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    cid = optional_int(data.get("cid"))
    return str(cid) if cid else None


def merge_streams(detail: Mapping[str, Any], streams: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``detail`` with ``streams``'s ``data`` under :data:`STREAMS_KEY`."""
    merged = dict(detail)
    data = dict(detail.get("data") or {})
    if isinstance(streams.get("data"), Mapping):
        data[STREAMS_KEY] = dict(streams["data"])
    merged["data"] = data
    return merged


_CLOCK = re.compile(r"(?:(\d+):)?(\d+):(\d{1,2})")


def _length_ms(value: Any) -> int | None:
    """``"06:24"`` or ``"1:02:03"``, as the space list writes a duration."""
    text = optional_str(value)
    match = _CLOCK.fullmatch(text) if text else None
    if match is None:
        return None
    hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000


def _listed_video(
    item: Node,
    *,
    fetched_at: datetime,
    author: Author,
    title_key: str,
    description_key: str,
    cover_key: str,
    created_key: str,
    duration_ms: int | None,
    stats: ContentStats,
) -> Content:
    bvid = item.id("bvid")
    cover = _image(item.get(cover_key))
    return Content(
        platform=_PLATFORM,
        content_id=bvid,
        kind=ContentKind.VIDEO,
        web_url=content_web_url(bvid),
        title=item.text(title_key),
        description=_prose(item.get(description_key)),
        created_at=epoch_to_datetime(item.get(created_key)),
        duration_ms=duration_ms,
        author=author,
        stats=stats,
        media=Media(covers=[cover] if cover else []),
        fetched_at=fetched_at,
        raw=item.raw(),
    )


def _numbered_page(
    page: Node | None, *, number: str, size: str, count: str
) -> tuple[bool, str | None]:
    """``(has_more, cursor)`` for an endpoint that pages by number."""
    if page is None:
        return False, None
    current = optional_int(page.get(number)) or 1
    per_page = optional_int(page.get(size)) or 0
    total = optional_int(page.get(count)) or 0
    more = per_page > 0 and current * per_page < total
    return more, str(current + 1) if more else None


def parse_author_posts(payload: Mapping[str, Any], *, fetched_at: datetime) -> Page[Content]:
    """Parse ``/x/space/wbi/arc/search``.

    An author nobody has answers ``code: 0`` with an empty list and a count of
    zero (measured 2026-10-18), which is an empty page, not an error.
    """
    data = _guard(payload).child("data")
    videos = data.child("list").listed_children("vlist")
    items = [
        _listed_video(
            video,
            fetched_at=fetched_at,
            author=_author(video, name_key="author", face_key=None),
            title_key="title",
            description_key="description",
            cover_key="pic",
            created_key="created",
            duration_ms=_length_ms(video.get("length")),
            stats=ContentStats(
                play_count=optional_int(video.get("play")),
                comment_count=optional_int(video.get("comment")),
            ),
        )
        for video in videos
    ]
    has_more, cursor = _numbered_page(data.opt_child("page"), number="pn", size="ps", count="count")
    return Page(items=items, cursor=cursor, has_more=has_more)


def parse_collection_posts(
    payload: Mapping[str, Any], *, fetched_at: datetime, cursor: str | None = None
) -> Page[Content]:
    """Parse ``/x/v3/fav/resource/list``.

    This answer carries ``has_more`` and nothing about which page it is, so the
    next cursor is computed from the one that asked for it. A folder can hold
    audio and other non-video entries; only the videos - those with a bvid -
    are posts here.
    """
    data = _guard(payload).opt_child("data")
    if data is None:
        return Page(items=[], cursor=None, has_more=False)
    items = []
    for media in data.listed_children("medias"):
        if not optional_str(media.get("bvid")):
            continue
        counters = media.opt_child("cnt_info")
        items.append(
            _listed_video(
                media,
                fetched_at=fetched_at,
                author=_author(media.child("upper")),
                title_key="title",
                description_key="intro",
                cover_key="cover",
                created_key="pubtime",
                duration_ms=seconds_to_ms(media.get("duration")),
                stats=ContentStats(
                    play_count=optional_int(counters.get("play")) if counters else None,
                    collect_count=optional_int(counters.get("collect")) if counters else None,
                ),
            )
        )
    has_more = optional_bool(data.get("has_more"))
    page = int(cursor) if cursor and str(cursor).isdigit() and int(cursor) > 0 else 1
    return Page(items=items, cursor=str(page + 1) if has_more else None, has_more=has_more)


# --------------------------------------------------------------------------
# Authors
# --------------------------------------------------------------------------


def parse_author(payload: Mapping[str, Any]) -> Author:
    """Parse ``/x/web-interface/card``: the profile, and its counters beside it."""
    data = _guard(payload).child("data")
    card = data.child("card")
    mid = card.id("mid")
    official = card.opt_child("Official")
    official_type = optional_int(official.get("type")) if official else None
    return Author(
        platform=_PLATFORM,
        uid=mid,
        sec_uid=mid,
        nickname=card.text("name"),
        signature=optional_str(card.get("sign")),
        avatar=_image(card.get("face")),
        web_url=author_web_url(mid),
        # -1 is "not verified"; 0 a person, 1 an organisation.
        verified=official_type is not None and official_type >= 0,
        stats=AuthorStats(
            follower_count=optional_int(data.get("follower")),
            following_count=optional_int(card.get("attention")),
            content_count=optional_int(data.get("archive_count")),
            total_digg=optional_int(data.get("like_num")),
        ),
        raw=data.raw(),
    )


def parse_author_collections(payload: Mapping[str, Any]) -> Page[Collection]:
    """Parse ``/x/v3/fav/folder/created/list-all``: every public folder, unpaged.

    An author with none answers ``data: null`` (measured 2026-10-18).
    """
    data = _guard(payload).opt_child("data")
    folders = data.listed_children("list") if data is not None else []
    items = [_folder(folder) for folder in folders]
    return Page(items=items, cursor=None, has_more=False)


def _folder(folder: Node) -> Collection:
    attr = optional_int(folder.get("attr"))
    return Collection(
        platform=_PLATFORM,
        collection_id=folder.id("id"),
        name=folder.text("title"),
        item_count=optional_int(folder.get("media_count")),
        # Bit 0 of `attr` is the private flag.
        is_public=None if attr is None else not attr & 1,
        owner_id=optional_str(folder.get("mid")),
        raw=folder.raw(),
    )


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------


def _comment(node: Node, *, content_id: str, parent_id: str | None, pinned: bool) -> Comment:
    member = node.child("member")
    up_action = node.opt_child("up_action")
    return Comment(
        platform=_PLATFORM,
        comment_id=node.id("rpid"),
        content_id=content_id,
        parent_id=parent_id,
        text=node.child("content").text("message"),
        created_at=epoch_to_datetime(node.get("ctime")),
        digg_count=optional_int(node.get("like")),
        reply_count=optional_int(node.get("rcount")),
        author=Author(
            platform=_PLATFORM,
            uid=member.id("mid"),
            sec_uid=member.id("mid"),
            nickname=member.text("uname"),
            signature=optional_str(member.get("sign")),
            avatar=_image(member.get("avatar")),
            web_url=author_web_url(member.id("mid")),
        ),
        is_author_liked=optional_bool(up_action.get("like")) if up_action else False,
        is_pinned=pinned,
        raw=node.raw(),
    )


def _bvid_of(node: Node, fallback: str | None) -> str:
    oid = optional_int(node.get("oid"))
    return av_to_bv(oid) if oid else (fallback or "")


def parse_comments(payload: Mapping[str, Any], *, content_id: str | None = None) -> Page[Comment]:
    """Parse ``/x/v2/reply/wbi/main``: pinned comments first, then the page.

    The cursor is the opaque ``next_offset`` the endpoint hands back, sent back
    unchanged. A guest is given three comments and ``is_end`` (measured
    2026-10-18), which is an honest last page rather than a refusal.
    """
    data = _guard(payload).child("data")
    seen: set[str] = set()
    items: list[Comment] = []
    for nodes, pinned in (
        (data.listed_children("top_replies"), True),
        (data.listed_children("replies"), False),
    ):
        for node in nodes:
            rpid = node.id("rpid")
            if rpid in seen:
                continue
            seen.add(rpid)
            items.append(
                _comment(node, content_id=_bvid_of(node, content_id), parent_id=None, pinned=pinned)
            )
    cursor_node = data.opt_child("cursor")
    is_end = optional_bool(cursor_node.get("is_end"), default=True) if cursor_node else True
    pagination = cursor_node.opt_child("pagination_reply") if cursor_node else None
    offset = optional_str(pagination.get("next_offset")) if pagination else None
    has_more = not is_end and offset is not None
    return Page(items=items, cursor=offset if has_more else None, has_more=has_more)


def parse_comment_replies(
    payload: Mapping[str, Any],
    *,
    content_id: str | None = None,
    parent_id: str | None = None,
) -> Page[Comment]:
    """Parse ``/x/v2/reply/reply``, the replies under one top-level comment.

    ``parent_id`` is that top-level comment, as on the other platforms -
    Bilibili's own ``parent`` names the reply being answered inside the thread,
    which the shared model has no slot for and ``raw`` keeps.
    """
    data = _guard(payload).child("data")
    root = data.opt_child("root")
    thread = parent_id or (optional_str(root.get("rpid")) if root else None)
    items = [
        _comment(
            node,
            content_id=_bvid_of(node, content_id),
            parent_id=thread or optional_str(node.get("root")),
            pinned=False,
        )
        for node in data.listed_children("replies")
    ]
    has_more, cursor = _numbered_page(
        data.opt_child("page"), number="num", size="size", count="count"
    )
    return Page(items=items, cursor=cursor, has_more=has_more)


__all__ = [
    "RISK_CODES",
    "STREAMS_KEY",
    "author_web_url",
    "content_web_url",
    "detect_risk_control",
    "first_part_cid",
    "merge_streams",
    "parse_author",
    "parse_author_collections",
    "parse_author_posts",
    "parse_collection_posts",
    "parse_comment_replies",
    "parse_comments",
    "parse_content",
]
