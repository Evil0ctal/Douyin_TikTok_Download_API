"""Query parameters for the Bilibili web endpoints.

Measured against the live API from a guest identity on 2026-10-18; v4's
``crawlers/bilibili/web/`` named the endpoints, and most of what it sent no
longer works as it did. What changed, and what this module does about it:

* **The detail endpoint is signed now.** ``/x/web-interface/view`` answered
  this egress with an HTML 412 page whatever the headers; the page's own
  ``/x/web-interface/wbi/view`` answered ``code: 0``. WBI itself is the
  signing layer's job (:mod:`dtk.signing.native.wbi`); this module only picks
  the signed path.
* **An uploader's space wants a browser report.** ``/x/space/wbi/arc/search``
  and ``acc/info`` answer ``-352`` ("risk check failed") without the
  ``dm_img_*`` parameters the space page sends - a canvas and WebGL summary.
  With them, ``code: 0``. :func:`dm_params` derives the WebGL strings from
  the identity's operating system, so they agree with its User-Agent.
* **Comments page by an opaque offset.** ``/x/v2/reply/wbi/main`` takes a JSON
  ``pagination_str``; the offset it carries is handed back as the cursor and
  sent back unchanged.

Ids are validated here (:mod:`dtk.platforms.bilibili.ids`), so a malformed one
is a 400 naming the field rather than a ``-400`` from upstream. Every builder
accepts the identity's ``profile`` because the fetch layer always passes one.
"""

from __future__ import annotations

import base64
import json
from typing import Final

from dtk.core.errors import InvalidParam
from dtk.core.types import CommentSort
from dtk.platforms.base import ClientProfile, ProfileSource
from dtk.platforms.bilibili.ids import bv_to_av, content_bvid, numeric_id

#: The web client's own default: a Chinese-locale desktop browser.
DEFAULT_PROFILE: Final = ClientProfile(
    browser_language="zh-CN",
    language="zh-CN",
    timezone="Asia/Shanghai",
    region="CN",
)

DEFAULT_PAGE_SIZE: Final = 20

#: ``playurl`` asked for one progressive MP4 rather than DASH, at the best
#: quality a guest is offered. Measured 2026-10-18: ``platform=html5`` with
#: ``high_quality=1`` served a guest a single 1080p MP4 on an old upload and
#: 720p on a current one, where the DASH answer topped out at 480p and would
#: have needed its audio and video muxed. One file is what the downloader
#: expects to fetch.
STREAM_PARAMS: Final[dict[str, str]] = {
    "qn": "80",
    "fnval": "1",
    "fnver": "0",
    "fourk": "0",
    "platform": "html5",
    "high_quality": "1",
}

#: The WebGL strings a space page reports, by operating system. The renderer
#: is what a real browser on that system says; an identity whose User-Agent
#: says macOS and whose WebGL says Direct3D is a contradiction offered free.
_WEBGL_VERSION: Final = "WebGL 1.0 (OpenGL ES 2.0 Chromium)"
_RENDERERS: Final[dict[str, str]] = {
    "Mac OS X": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)Google Inc. (Apple)",
    "Mac OS": "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)Google Inc. (Apple)",
    "Linux": "ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)Google Inc. (Intel)",
}
_DEFAULT_RENDERER: Final = (
    "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 (0x00001F82) Direct3D11 vs_5_0 ps_5_0, D3D11)"
    "Google Inc. (NVIDIA)"
)


def profile_for(source: ProfileSource) -> ClientProfile:
    return DEFAULT_PROFILE.with_fingerprint(source)


def _b64(text: str) -> str:
    # The page drops the last two characters of each base64 string; so does this.
    return base64.b64encode(text.encode()).decode()[:-2]


def dm_params(profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    """The canvas and WebGL report an uploader's space page sends.

    The interaction fields are empty, as they are for a page that has not been
    moved over yet - which a request for the first page of a space is.
    """
    renderer = _RENDERERS.get(profile.os_name, _DEFAULT_RENDERER)
    return {
        "dm_img_list": "[]",
        "dm_img_str": _b64(_WEBGL_VERSION),
        "dm_cover_img_str": _b64(renderer),
        "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
    }


def _page(cursor: str | None) -> str:
    """A page-numbered endpoint's cursor: the page to fetch, from 1."""
    if cursor is None or not str(cursor).strip() or str(cursor).strip() == "0":
        return "1"
    text = str(cursor).strip()
    if not text.isdigit():
        raise InvalidParam(
            "cursor must be the page number this endpoint handed back",
            details={"field": "cursor", "reason": "malformed_cursor"},
        )
    return text


def part_number(value: object) -> int:
    """A part's 1-based position, as the site's own ``?p=`` links count it."""
    text = str(value).strip()
    if not text.isdigit() or int(text) < 1:
        raise InvalidParam(
            "part must be a positive whole number, counted from 1",
            details={"field": "part", "value": text[:16]},
        )
    return int(text)


def content_detail_params(
    *, bvid: str, part: int | None = None, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    """The detail of one video.

    ``part`` is not sent - the detail is the whole upload's, whichever part
    is wanted. It is accepted here so the fetch layer hands it on to the
    ``playurl`` follow-up, which is where it chooses the file; validating it
    now refuses a bad one before any request is spent.
    """
    if part is not None:
        part_number(part)
    return {"bvid": content_bvid(bvid)}


def content_parts_params(*, bvid: str, profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    return {"bvid": content_bvid(bvid)}


def content_streams_params(
    *, bvid: str, cid: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    return {"bvid": content_bvid(bvid), "cid": numeric_id(cid, field="cid"), **STREAM_PARAMS}


def related_posts_params(
    *,
    bvid: str,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """The videos the player lists beside one, unsigned and unpaged.

    The answer is 40 videos whatever is asked (measured 2026-10-20), so
    ``count`` is accepted - the route sends it for every platform - and not
    sent.
    """
    return {"bvid": content_bvid(bvid)}


def _search(search_type: str, keyword: str, cursor: str | None, count: int) -> dict[str, str]:
    text = str(keyword).strip()
    if not text:
        raise InvalidParam("a search needs a keyword", details={"field": "keyword"})
    return {
        "search_type": search_type,
        "keyword": text,
        "page": _page(cursor),
        "page_size": str(count),
    }


def search_posts_params(
    *,
    keyword: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """The search page's video tab, by page number. A guest pages it: page two
    answered twenty more on 2026-10-20."""
    return _search("video", keyword, cursor, count)


def search_authors_params(
    *,
    keyword: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """The search page's user tab, which Bilibili calls ``bili_user``."""
    return _search("bili_user", keyword, cursor, count)


def author_mixes_params(
    *,
    mid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """The seasons on an uploader's space, by page number."""
    return {
        "mid": numeric_id(mid, field="author_id"),
        "page_num": _page(cursor),
        "page_size": str(count),
    }


def mix_posts_params(
    *,
    season_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Every video in one season.

    The page number and size are sent because the site sends them, and they
    change nothing: measured 2026-10-20 on a season of 50, ``pn=2&ps=20``
    answered the same 50 as ``pn=1``. So it is one page, and ``cursor`` and
    ``count`` are accepted - the route sends them for every platform - and
    dropped.
    """
    return {"season_id": numeric_id(season_id, field="mix_id"), "pn": "1", "ps": "20"}


def author_profile_params(*, mid: str, profile: ClientProfile = DEFAULT_PROFILE) -> dict[str, str]:
    return {"mid": numeric_id(mid, field="author_id"), "photo": "true"}


def author_posts_params(
    *,
    mid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    return {
        "mid": numeric_id(mid, field="author_id"),
        "pn": _page(cursor),
        "ps": str(count),
        "tid": "0",
        "keyword": "",
        "order": "pubdate",
        "platform": "web",
        "web_location": "1550101",
        "order_avoided": "true",
        **dm_params(profile),
    }


#: ``sort`` -> the ``mode`` the comment endpoint takes. Both measured
#: 2026-10-18: the answer's ``cursor.support_mode`` is ``[2, 3]``, and mode 2
#: came back newest first with no likes yet where mode 3 came back by likes.
_COMMENT_MODES: Final[dict[str, str]] = {CommentSort.HOT: "3", CommentSort.NEW: "2"}


def comments_params(
    *,
    bvid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    sort: str | None = None,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Top-level comments, hottest first unless ``sort`` says newest.

    ``count`` is accepted and not sent: this endpoint takes no page size, and a
    page is whatever it decides. A guest is given three comments and no next
    page in either order (measured 2026-10-18), which is why the endpoint
    prefers a signed-in identity - see ``dtk.platforms.bilibili.endpoints``.

    The offset a page hands back belongs to the order it was read in, so a
    caller paging on sends the same ``sort`` with it.
    """
    mode = _COMMENT_MODES.get(sort or CommentSort.HOT)
    if mode is None:
        raise InvalidParam(
            f"sort must be one of {', '.join(_COMMENT_MODES)}",
            details={"field": "sort", "allowed": list(_COMMENT_MODES)},
        )
    offset = "" if cursor is None or str(cursor).strip() in ("", "0") else str(cursor)
    return {
        "oid": str(bv_to_av(content_bvid(bvid))),
        "type": "1",
        "mode": mode,
        "pagination_str": json.dumps({"offset": offset}, separators=(",", ":")),
        "plat": "1",
        "seek_rpid": "",
        "web_location": "1315875",
    }


def comment_replies_params(
    *,
    bvid: str,
    root: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    return {
        "oid": str(bv_to_av(content_bvid(bvid))),
        "type": "1",
        "root": numeric_id(root, field="comment_id"),
        "pn": _page(cursor),
        "ps": str(count),
        "web_location": "333.788",
    }


def author_collections_params(
    *,
    mid: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Every public folder an author made, in one answer.

    It does not page, so ``cursor`` and ``count`` are accepted - the route
    sends them for every platform - and not sent. The answer says it is the
    last page.
    """
    return {"up_mid": numeric_id(mid, field="author_id")}


def collection_posts_params(
    *,
    media_id: str,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    return {
        "media_id": numeric_id(media_id, field="collection_id"),
        "pn": _page(cursor),
        "ps": str(count),
        "keyword": "",
        "order": "mtime",
        "type": "0",
        "tid": "0",
        "platform": "web",
    }


def popular_params(
    *,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    return {"pn": _page(cursor), "ps": str(count)}


def popular_weekly_params(
    *,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """The index of issues, which takes nothing.

    The cursor is an issue number and is used by the follow-up that fetches
    the issue. It is checked here so a malformed one is refused before the
    index is read; ``count`` is accepted and not sent, an issue being 50.
    """
    if cursor is not None and str(cursor).strip() not in ("", "0"):
        _page(cursor)
    return {}


def popular_weekly_issue_params(
    *, number: str, profile: ClientProfile = DEFAULT_PROFILE
) -> dict[str, str]:
    return {"number": _page(number)}


def popular_precious_params(
    *,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """All 98 at once; the endpoint answers the same list for every page."""
    return {"page_size": "100", "page": "1"}


def ranking_params(
    *,
    cursor: str | None = None,
    count: int = DEFAULT_PAGE_SIZE,
    profile: ClientProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """The whole site's top 100, in one answer.

    ``rid=0`` is every category. The dm report is the rank page's: without it
    a guest is answered -352, as the uploader space is, whether or not the
    request is signed.
    """
    return {"rid": "0", "type": "all", "web_location": "333.934", **dm_params(profile)}


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_PROFILE",
    "STREAM_PARAMS",
    "author_collections_params",
    "author_mixes_params",
    "author_posts_params",
    "author_profile_params",
    "collection_posts_params",
    "comment_replies_params",
    "comments_params",
    "content_detail_params",
    "content_parts_params",
    "content_streams_params",
    "dm_params",
    "mix_posts_params",
    "part_number",
    "popular_params",
    "popular_precious_params",
    "popular_weekly_issue_params",
    "popular_weekly_params",
    "profile_for",
    "ranking_params",
    "related_posts_params",
    "search_authors_params",
    "search_posts_params",
]
