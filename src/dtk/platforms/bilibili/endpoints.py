"""Bilibili web endpoint URLs and the endpoint table.

v4's ``crawlers/bilibili/web/endpoints.py`` named most of these; the paths
below are the ones the site's own pages call today, measured from a guest
identity on 2026-10-18. Where v4's path no longer answers, the comment says
so - an unsigned path that now returns an HTML 412 page is the kind of thing
that otherwise gets rediscovered the expensive way.
"""

from __future__ import annotations

from typing import Final

from dtk.core.types import LoginNeed
from dtk.platforms.base import EndpointSpec, EndpointTable
from dtk.platforms.bilibili.params import (
    author_collections_params,
    author_posts_params,
    author_profile_params,
    collection_posts_params,
    comment_replies_params,
    comments_params,
    content_detail_params,
    content_streams_params,
)


class BilibiliAPIEndpoints:
    """Bilibili web API endpoint URLs."""

    WWW_DOMAIN: Final = "https://www.bilibili.com"
    SPACE_DOMAIN: Final = "https://space.bilibili.com"
    API_DOMAIN: Final = "https://api.bilibili.com"

    # A video. v4 called the unsigned /x/web-interface/view, which now answers
    # an HTML 412 page; the page itself calls this signed path.
    VIDEO_DETAIL: Final = f"{API_DOMAIN}/x/web-interface/wbi/view"
    # The playable file of one part (cid) of a video.
    VIDEO_PLAYURL: Final = f"{API_DOMAIN}/x/player/wbi/playurl"
    # An uploader's card: profile and counts in one answer, unsigned.
    USER_CARD: Final = f"{API_DOMAIN}/x/web-interface/card"
    # An uploader's videos, newest first. Signed, and wants the dm_img report.
    USER_POSTS: Final = f"{API_DOMAIN}/x/space/wbi/arc/search"
    # Top-level comments on a video, by opaque offset.
    COMMENTS: Final = f"{API_DOMAIN}/x/v2/reply/wbi/main"
    # The replies under one top-level comment, by page number.
    COMMENT_REPLIES: Final = f"{API_DOMAIN}/x/v2/reply/reply"
    # The public folders ("favourites") an author created.
    USER_FOLDERS: Final = f"{API_DOMAIN}/x/v3/fav/folder/created/list-all"
    # What is in one folder.
    FOLDER_ITEMS: Final = f"{API_DOMAIN}/x/v3/fav/resource/list"
    # Whose session is this. A guest is told `-101` and `isLogin: false`.
    NAV: Final = f"{API_DOMAIN}/x/web-interface/nav"
    # Mints a guest's `buvid3` and `buvid4`; see dtk.identity.minting.native.
    FINGER_SPI: Final = f"{API_DOMAIN}/x/frontend/finger/spi"


CONTENT_DETAIL: Final = "bilibili.content_detail"
CONTENT_STREAMS: Final = "bilibili.content_streams"
AUTHOR_PROFILE: Final = "bilibili.author_profile"
AUTHOR_POSTS: Final = "bilibili.author_posts"
COMMENTS: Final = "bilibili.comments"
COMMENT_REPLIES: Final = "bilibili.comment_replies"
AUTHOR_COLLECTIONS: Final = "bilibili.author_collections"
COLLECTION_POSTS: Final = "bilibili.collection_posts"
SESSION_CHECK: Final = "bilibili.session_check"

#: Cookies and User-Agent come from the identity.
DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Referer": f"{BilibiliAPIEndpoints.WWW_DOMAIN}/",
    "Origin": BilibiliAPIEndpoints.WWW_DOMAIN,
}

#: What an uploader's space page sends. Measured 2026-10-18: the space path
#: without a mid answers the same as the mid's own page, so one static header
#: serves every author.
_SPACE_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("Referer", f"{BilibiliAPIEndpoints.SPACE_DOMAIN}/"),
    ("Origin", BilibiliAPIEndpoints.SPACE_DOMAIN),
)


def _session_check_params(**_: object) -> dict[str, str]:
    return {}


ENDPOINTS: Final = EndpointTable.of(
    EndpointSpec(
        name=CONTENT_DETAIL,
        path=BilibiliAPIEndpoints.VIDEO_DETAIL,
        required=("bvid",),
        build=content_detail_params,
        risk_weight=1.0,
        summary="Fetch one video",
    ),
    EndpointSpec(
        name=CONTENT_STREAMS,
        path=BilibiliAPIEndpoints.VIDEO_PLAYURL,
        required=("bvid", "cid"),
        build=content_streams_params,
        risk_weight=1.0,
        summary="Fetch the playable file of one video part",
    ),
    EndpointSpec(
        name=AUTHOR_PROFILE,
        path=BilibiliAPIEndpoints.USER_CARD,
        required=("mid",),
        build=author_profile_params,
        risk_weight=1.0,
        signed=False,
        summary="Fetch an uploader's profile",
    ),
    EndpointSpec(
        name=AUTHOR_POSTS,
        path=BilibiliAPIEndpoints.USER_POSTS,
        required=("mid",),
        build=author_posts_params,
        # The endpoint Bilibili guards hardest. A burst of probes from one
        # egress on 2026-10-18 turned its answers into HTML 412 pages for that
        # egress, whatever the headers; the same request answered normally a
        # few minutes later. Hence the weight, and the slow bucket.
        risk_weight=1.8,
        headers=_SPACE_HEADERS,
        summary="List an uploader's videos",
    ),
    EndpointSpec(
        name=COMMENTS,
        path=BilibiliAPIEndpoints.COMMENTS,
        required=("bvid",),
        build=comments_params,
        risk_weight=1.2,
        # A guest is served the three hottest comments and told that is the
        # end (measured 2026-10-18), so a signed-in identity is preferred for
        # the first page and needed for every later one.
        login=LoginNeed.PREFERRED,
        paging_login=LoginNeed.REQUIRED,
        summary="List a video's comments",
    ),
    EndpointSpec(
        name=COMMENT_REPLIES,
        path=BilibiliAPIEndpoints.COMMENT_REPLIES,
        required=("bvid", "root"),
        build=comment_replies_params,
        risk_weight=1.2,
        signed=False,
        # A guest is served the first page of replies, up to 20, and every
        # later page empty with `page.num: 0` - while page one says how many
        # there are (measured 2026-10-18 on a comment with 24). Without this
        # a guest followed the cursor page one handed out to an empty page
        # that looked like the end of the thread.
        paging_login=LoginNeed.REQUIRED,
        summary="List the replies to a comment",
    ),
    EndpointSpec(
        name=AUTHOR_COLLECTIONS,
        path=BilibiliAPIEndpoints.USER_FOLDERS,
        required=("mid",),
        build=author_collections_params,
        risk_weight=1.2,
        signed=False,
        summary="List an uploader's public folders",
    ),
    EndpointSpec(
        name=COLLECTION_POSTS,
        path=BilibiliAPIEndpoints.FOLDER_ITEMS,
        required=("media_id",),
        build=collection_posts_params,
        risk_weight=1.2,
        signed=False,
        # A folder that does not exist is `{"code": 0, "data": null}`; an empty
        # one still carries its `info` (measured 2026-10-18).
        absent_when_null="data",
        summary="List the videos in a folder",
    ),
    EndpointSpec(
        name=SESSION_CHECK,
        path=BilibiliAPIEndpoints.NAV,
        required=(),
        build=_session_check_params,
        signed=False,
        summary="Check whose session an identity holds",
    ),
)


__all__ = [
    "AUTHOR_COLLECTIONS",
    "AUTHOR_POSTS",
    "AUTHOR_PROFILE",
    "COLLECTION_POSTS",
    "COMMENTS",
    "COMMENT_REPLIES",
    "CONTENT_DETAIL",
    "CONTENT_STREAMS",
    "DEFAULT_HEADERS",
    "ENDPOINTS",
    "SESSION_CHECK",
    "BilibiliAPIEndpoints",
]
