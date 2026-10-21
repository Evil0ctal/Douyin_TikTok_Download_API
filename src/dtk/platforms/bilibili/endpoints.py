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
    author_mixes_params,
    author_posts_params,
    author_profile_params,
    collection_posts_params,
    comment_replies_params,
    comments_params,
    content_detail_params,
    content_parts_params,
    content_streams_params,
    mix_posts_params,
    popular_params,
    popular_precious_params,
    popular_weekly_issue_params,
    popular_weekly_params,
    ranking_params,
    related_posts_params,
    search_authors_params,
    search_posts_params,
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
    # The parts of a video uploaded in several, unsigned. The detail
    # carries the same list as `pages`; this answers without the rest.
    VIDEO_PARTS: Final = f"{API_DOMAIN}/x/player/pagelist"
    # What is popular now, paged by number. An empty page past the end still
    # says `no_more: false` (pn=30 on 2026-10-19), so emptiness is the end.
    POPULAR: Final = f"{API_DOMAIN}/x/web-interface/popular"
    # Every issue of the weekly selection, newest first, and one issue's 50.
    # With no `number` the issue endpoint answers issue 1, the oldest, so the
    # newest has to be read off the index first.
    POPULAR_WEEKLY_INDEX: Final = f"{API_DOMAIN}/x/web-interface/popular/series/list"
    POPULAR_WEEKLY_ISSUE: Final = f"{API_DOMAIN}/x/web-interface/popular/series/one"
    # The editors' all-time selection: 98 videos, all of them in one answer
    # whatever page is asked for.
    POPULAR_PRECIOUS: Final = f"{API_DOMAIN}/x/web-interface/popular/precious"
    # The site-wide top 100. Without the rank page's dm report it answers a
    # guest -352, signed or not; with it, 100 videos, signed or not (measured
    # 2026-10-19). So it is sent unsigned, as the folder endpoints are.
    RANKING: Final = f"{API_DOMAIN}/x/web-interface/ranking/v2"
    # The videos the player lists beside one. Unsigned; an id that names no
    # video still answers 40 (measured 2026-10-20).
    VIDEO_RELATED: Final = f"{API_DOMAIN}/x/web-interface/archive/related"
    # The seasons and the series on an uploader's space, paged together:
    # page one of an author with 24 seasons and 8 series held 20 seasons, page
    # two the other 4 and the 8 series (2026-10-20). Unsigned answers.
    SEASONS_SERIES: Final = f"{API_DOMAIN}/x/polymer/web-space/seasons_series_list"
    # One season's videos, each with its uploader named - which the space's
    # own seasons_archives_list does not do - and all of them in one answer.
    SEASON_ITEMS: Final = f"{API_DOMAIN}/x/space/fav/season/list"
    # One tab of the search page: `search_type` says which. Signed, and
    # answers a guest; the HTML `<em>` around each hit is in the title.
    SEARCH_TYPE: Final = f"{API_DOMAIN}/x/web-interface/wbi/search/type"
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
CONTENT_PARTS: Final = "bilibili.content_parts"
POPULAR: Final = "bilibili.popular"
POPULAR_WEEKLY: Final = "bilibili.popular_weekly"
POPULAR_WEEKLY_ISSUE: Final = "bilibili.popular_weekly_issue"
POPULAR_PRECIOUS: Final = "bilibili.popular_precious"
RANKING: Final = "bilibili.ranking"
RELATED_POSTS: Final = "bilibili.related_posts"
SEARCH_POSTS: Final = "bilibili.search_posts"
SEARCH_AUTHORS: Final = "bilibili.search_authors"
AUTHOR_MIXES: Final = "bilibili.author_mixes"
MIX_POSTS: Final = "bilibili.mix_posts"
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


#: What the ranking page sends; the endpoint answers -352 without it.
_RANK_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("Referer", f"{BilibiliAPIEndpoints.WWW_DOMAIN}/v/popular/rank/all"),
    ("Origin", BilibiliAPIEndpoints.WWW_DOMAIN),
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
        name=CONTENT_PARTS,
        path=BilibiliAPIEndpoints.VIDEO_PARTS,
        required=("bvid",),
        build=content_parts_params,
        risk_weight=1.0,
        signed=False,
        # An id that is no video is `-400` here rather than `-404`; either
        # way the classifier calls it a business error, which is NOT_FOUND.
        summary="List the parts of a video",
    ),
    EndpointSpec(
        name=POPULAR,
        path=BilibiliAPIEndpoints.POPULAR,
        required=(),
        build=popular_params,
        risk_weight=1.0,
        signed=False,
        summary="List what is popular now",
    ),
    EndpointSpec(
        name=POPULAR_WEEKLY,
        path=BilibiliAPIEndpoints.POPULAR_WEEKLY_INDEX,
        required=(),
        build=popular_weekly_params,
        risk_weight=1.0,
        signed=False,
        # The index is read first, for the newest issue's number, and the
        # issue itself is the follow-up - see BilibiliAdapter.follow_up.
        summary="List one issue of the weekly selection",
    ),
    EndpointSpec(
        name=POPULAR_WEEKLY_ISSUE,
        path=BilibiliAPIEndpoints.POPULAR_WEEKLY_ISSUE,
        required=("number",),
        build=popular_weekly_issue_params,
        risk_weight=1.0,
        signed=False,
        summary="Fetch one issue of the weekly selection",
    ),
    EndpointSpec(
        name=POPULAR_PRECIOUS,
        path=BilibiliAPIEndpoints.POPULAR_PRECIOUS,
        required=(),
        build=popular_precious_params,
        risk_weight=1.0,
        signed=False,
        summary="List the editors' all-time selection",
    ),
    EndpointSpec(
        name=RANKING,
        path=BilibiliAPIEndpoints.RANKING,
        required=(),
        build=ranking_params,
        risk_weight=1.2,
        signed=False,
        headers=_RANK_HEADERS,
        summary="List the site-wide top 100",
    ),
    EndpointSpec(
        name=RELATED_POSTS,
        path=BilibiliAPIEndpoints.VIDEO_RELATED,
        required=("bvid",),
        build=related_posts_params,
        risk_weight=1.2,
        signed=False,
        summary="List the videos recommended beside one",
    ),
    EndpointSpec(
        name=AUTHOR_MIXES,
        path=BilibiliAPIEndpoints.SEASONS_SERIES,
        required=("mid",),
        build=author_mixes_params,
        risk_weight=1.2,
        signed=False,
        headers=_SPACE_HEADERS,
        summary="List an uploader's seasons",
    ),
    EndpointSpec(
        name=MIX_POSTS,
        path=BilibiliAPIEndpoints.SEASON_ITEMS,
        required=("season_id",),
        build=mix_posts_params,
        risk_weight=1.2,
        signed=False,
        # A season nobody has is `-404` and "nothing here", a business answer.
        summary="List the videos in a season",
    ),
    EndpointSpec(
        name=SEARCH_POSTS,
        path=BilibiliAPIEndpoints.SEARCH_TYPE,
        required=("keyword",),
        build=search_posts_params,
        risk_weight=1.5,
        summary="Search videos by keyword",
    ),
    EndpointSpec(
        name=SEARCH_AUTHORS,
        path=BilibiliAPIEndpoints.SEARCH_TYPE,
        required=("keyword",),
        build=search_authors_params,
        risk_weight=1.5,
        summary="Search uploaders by keyword",
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
    "AUTHOR_MIXES",
    "AUTHOR_POSTS",
    "AUTHOR_PROFILE",
    "COLLECTION_POSTS",
    "COMMENTS",
    "COMMENT_REPLIES",
    "CONTENT_DETAIL",
    "CONTENT_PARTS",
    "CONTENT_STREAMS",
    "DEFAULT_HEADERS",
    "ENDPOINTS",
    "MIX_POSTS",
    "POPULAR",
    "POPULAR_PRECIOUS",
    "POPULAR_WEEKLY",
    "POPULAR_WEEKLY_ISSUE",
    "RANKING",
    "RELATED_POSTS",
    "SEARCH_AUTHORS",
    "SEARCH_POSTS",
    "SESSION_CHECK",
    "BilibiliAPIEndpoints",
]
