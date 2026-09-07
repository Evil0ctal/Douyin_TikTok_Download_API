"""Douyin web endpoint URLs and the P0 endpoint table.

Ported from V4 ``crawlers/douyin/web/endpoints.py`` (branch ``main``, file
``vendor_salvage/douyin_web_endpoints.py`` in this tree). The URL constants are
the product of long trial and error and are reproduced verbatim; only the
comments were translated to English, per ``docs/design/16-salvage-and-debug.md``.

The full constant table is kept even for endpoints outside the P0 scope: it
costs nothing, and rediscovering a URL later is expensive. Only the endpoints
listed in ``ENDPOINTS`` are wired up; see ``docs/design/11-data-contracts.md``
for the scope split.
"""

from __future__ import annotations

from typing import Final

from dtk.platforms.base import EndpointSpec, EndpointTable
from dtk.platforms.douyin.params import (
    author_posts_params,
    author_profile_params,
    comment_replies_params,
    comments_params,
    content_detail_params,
)


class DouyinAPIEndpoints:
    """Douyin web API endpoint URLs."""

    # Douyin domain
    DOUYIN_DOMAIN: Final = "https://www.douyin.com"

    # Short-link domain
    IESDOUYIN_DOMAIN: Final = "https://www.iesdouyin.com"

    # Live domain
    LIVE_DOMAIN: Final = "https://live.douyin.com"

    # Live domain, alternate
    LIVE_DOMAIN2: Final = "https://webcast.amemv.com"

    # Single sign-on domain
    SSO_DOMAIN: Final = "https://sso.douyin.com"

    # Live websocket domain
    WEBCAST_WSS_DOMAIN: Final = "wss://webcast5-ws-web-lf.douyin.com"

    # Home feed
    TAB_FEED: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/tab/feed/"

    # User short info
    USER_SHORT_INFO: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/im/user/info/"

    # User detail info
    USER_DETAIL: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/profile/other/"

    # Post base
    BASE_AWEME: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/"

    # User posts
    USER_POST: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/post/"

    # Locate a post inside a user's timeline
    LOCATE_POST: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/locate/post/"

    # General search
    GENERAL_SEARCH: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/general/search/single/"

    # Video search
    VIDEO_SEARCH: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/search/item/"

    # User search
    USER_SEARCH: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/discover/search/"

    # Live room search
    LIVE_SEARCH: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/live/search/"

    # Post detail
    POST_DETAIL: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/detail/"

    # Danmaku (on-video comments) for a single post
    POST_DANMAKU: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/danmaku/get_v2/"

    # User likes, variant A
    USER_FAVORITE_A: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/favorite/"

    # User likes, variant B
    USER_FAVORITE_B: Final = f"{IESDOUYIN_DOMAIN}/web/api/v2/aweme/like/"

    # Accounts a user follows
    USER_FOLLOWING: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/following/list/"

    # A user's followers
    USER_FOLLOWER: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/user/follower/list/"

    # Posts in a mix (collection)
    MIX_AWEME: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/mix/aweme/"

    # Watch history
    USER_HISTORY: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/history/read/"

    # Posts the user bookmarked
    USER_COLLECTION: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/listcollection/"

    # The user's bookmark folders
    USER_COLLECTS: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/collects/list/"

    # Posts inside one bookmark folder
    USER_COLLECTS_VIDEO: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/collects/video/list/"

    # Music the user bookmarked
    USER_MUSIC_COLLECTION: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/music/listcollection/"

    # Feed of posts by mutual friends
    FRIEND_FEED: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/familiar/feed/"

    # Feed of posts by followed accounts
    FOLLOW_FEED: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/follow/feed/"

    # Related recommendations for a post
    POST_RELATED: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/related/"

    # Live rooms of followed accounts
    FOLLOW_USER_LIVE: Final = f"{DOUYIN_DOMAIN}/webcast/web/feed/follow/"

    # Live room info
    LIVE_INFO: Final = f"{LIVE_DOMAIN}/webcast/room/web/enter/"

    # Live room info by room id
    LIVE_INFO_ROOM_ID: Final = f"{LIVE_DOMAIN2}/webcast/room/reflow/info/"

    # Gift leaderboard of a live room
    LIVE_GIFT_RANK: Final = f"{LIVE_DOMAIN}/webcast/ranklist/audience/"

    # Live viewer info
    LIVE_USER_INFO: Final = f"{LIVE_DOMAIN}/webcast/user/me/"

    # Suggested search terms
    SUGGEST_WORDS: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/api/suggest_words/"

    # SSO login: fetch QR code
    SSO_LOGIN_GET_QR: Final = f"{SSO_DOMAIN}/get_qrcode/"

    # SSO login: poll QR connect state
    SSO_LOGIN_CHECK_QR: Final = f"{SSO_DOMAIN}/check_qrconnect/"

    # SSO login: confirm login
    SSO_LOGIN_CHECK_LOGIN: Final = f"{SSO_DOMAIN}/check_login/"

    # SSO login: redirect target
    SSO_LOGIN_REDIRECT: Final = f"{DOUYIN_DOMAIN}/login/"

    # SSO login: callback
    SSO_LOGIN_CALLBACK: Final = f"{DOUYIN_DOMAIN}/passport/sso/login/callback/"

    # Comments on a post
    POST_COMMENT: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/list/"

    # Replies to a comment
    POST_COMMENT_REPLY: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/list/reply/"

    # Publish a comment
    POST_COMMENT_PUBLISH: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/publish"

    # Delete a comment
    POST_COMMENT_DELETE: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/delete/"

    # Like a comment
    POST_COMMENT_DIGG: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/comment/digg"

    # Trending board
    DOUYIN_HOT_SEARCH: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/hot/search/list/"

    # Video channel feed
    DOUYIN_VIDEO_CHANNEL: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/channel/feed/"


# Stable endpoint keys. The scheduler uses these verbatim as Redis token bucket
# and circuit breaker keys, and the console shows them on the endpoint health
# board, so they are part of the operational contract.
CONTENT_DETAIL: Final = "douyin.content_detail"
AUTHOR_PROFILE: Final = "douyin.author_profile"
AUTHOR_POSTS: Final = "douyin.author_posts"
COMMENTS: Final = "douyin.comments"
COMMENT_REPLIES: Final = "douyin.comment_replies"

#: Sent on every Douyin request. Cookies and User-Agent are injected by the
#: transport from the identity; only the platform-specific referer belongs here.
DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Referer": f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}/",
    "Origin": DouyinAPIEndpoints.DOUYIN_DOMAIN,
}


#: The P0 endpoint set from docs/design/11-data-contracts.md.
#:
#: Risk weights follow the scheduler guidance: a single detail lookup is the
#: cheapest call a real user makes, while paging through someone's timeline or
#: comment tree is the pattern platforms watch for.
ENDPOINTS: Final = EndpointTable.of(
    EndpointSpec(
        name=CONTENT_DETAIL,
        path=DouyinAPIEndpoints.POST_DETAIL,
        required=("aweme_id",),
        build=content_detail_params,
        risk_weight=1.0,
        summary="Single post detail, video or image album",
    ),
    EndpointSpec(
        name=AUTHOR_PROFILE,
        path=DouyinAPIEndpoints.USER_DETAIL,
        required=("sec_user_id",),
        build=author_profile_params,
        risk_weight=1.2,
        summary="Author profile by sec_user_id",
    ),
    EndpointSpec(
        name=AUTHOR_POSTS,
        path=DouyinAPIEndpoints.USER_POST,
        required=("sec_user_id",),
        build=author_posts_params,
        risk_weight=1.8,
        summary="Author post list, paged by max_cursor",
    ),
    EndpointSpec(
        name=COMMENTS,
        path=DouyinAPIEndpoints.POST_COMMENT,
        required=("aweme_id",),
        build=comments_params,
        risk_weight=1.5,
        summary="Top level comments on a post",
    ),
    EndpointSpec(
        name=COMMENT_REPLIES,
        path=DouyinAPIEndpoints.POST_COMMENT_REPLY,
        required=("item_id", "comment_id"),
        build=comment_replies_params,
        risk_weight=1.5,
        summary="Replies under one comment",
    ),
)


__all__ = [
    "AUTHOR_POSTS",
    "AUTHOR_PROFILE",
    "COMMENTS",
    "COMMENT_REPLIES",
    "CONTENT_DETAIL",
    "DEFAULT_HEADERS",
    "ENDPOINTS",
    "DouyinAPIEndpoints",
]
