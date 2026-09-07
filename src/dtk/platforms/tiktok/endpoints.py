"""TikTok web endpoint URLs and the P0 endpoint table.

Ported from V4 ``crawlers/tiktok/web/endpoints.py`` (branch ``main``, file
``vendor_salvage/tiktok_web_endpoints.py`` in this tree). URL constants are
reproduced verbatim; the comments were translated to English per
``docs/design/16-salvage-and-debug.md``.

Only the P0 endpoints from ``docs/design/11-data-contracts.md`` are wired into
``ENDPOINTS``; the remaining constants are kept because rediscovering a URL is
far more expensive than storing one.
"""

from __future__ import annotations

from typing import Final

from dtk.platforms.base import EndpointSpec, EndpointTable
from dtk.platforms.tiktok.params import (
    author_posts_params,
    author_profile_params,
    comment_replies_params,
    comments_params,
    content_detail_params,
)


class TikTokAPIEndpoints:
    """TikTok web API endpoint URLs."""

    # TikTok domain
    TIKTOK_DOMAIN: Final = "https://www.tiktok.com"

    # Live domain
    WEBCAST_DOMAIN: Final = "https://webcast.tiktok.com"

    # Login
    LOGIN_ENDPOINT: Final = f"{TIKTOK_DOMAIN}/login/"

    # Home recommendation feed
    HOME_RECOMMEND: Final = f"{TIKTOK_DOMAIN}/api/recommend/item_list/"

    # User detail info
    USER_DETAIL: Final = f"{TIKTOK_DOMAIN}/api/user/detail/"

    # User posts
    USER_POST: Final = f"{TIKTOK_DOMAIN}/api/post/item_list/"

    # Posts the user liked
    USER_LIKE: Final = f"{TIKTOK_DOMAIN}/api/favorite/item_list/"

    # Posts the user bookmarked
    USER_COLLECT: Final = f"{TIKTOK_DOMAIN}/api/user/collect/item_list/"

    # User playlists
    USER_PLAY_LIST: Final = f"{TIKTOK_DOMAIN}/api/user/playlist/"

    # Posts inside one playlist or mix
    USER_MIX: Final = f"{TIKTOK_DOMAIN}/api/mix/item_list/"

    # Related recommendations for a post
    GUESS_YOU_LIKE: Final = f"{TIKTOK_DOMAIN}/api/related/item_list/"

    # Accounts a user follows
    USER_FOLLOW: Final = f"{TIKTOK_DOMAIN}/api/user/list/"

    # A user's followers. Same path as USER_FOLLOW; the ``scene`` parameter
    # decides which side of the relation is returned.
    USER_FANS: Final = f"{TIKTOK_DOMAIN}/api/user/list/"

    # Post detail
    POST_DETAIL: Final = f"{TIKTOK_DOMAIN}/api/item/detail/"

    # Comments on a post
    POST_COMMENT: Final = f"{TIKTOK_DOMAIN}/api/comment/list/"

    # Replies to a comment
    POST_COMMENT_REPLY: Final = f"{TIKTOK_DOMAIN}/api/comment/list/reply/"


# Stable endpoint keys, used verbatim by the scheduler for token buckets and
# circuit breakers and by the console's endpoint health board.
CONTENT_DETAIL: Final = "tiktok.content_detail"
AUTHOR_PROFILE: Final = "tiktok.author_profile"
AUTHOR_POSTS: Final = "tiktok.author_posts"
COMMENTS: Final = "tiktok.comments"
COMMENT_REPLIES: Final = "tiktok.comment_replies"

#: Cookies and User-Agent come from the identity; only the platform-specific
#: referer belongs here.
DEFAULT_HEADERS: Final[dict[str, str]] = {
    "Referer": f"{TikTokAPIEndpoints.TIKTOK_DOMAIN}/",
    "Origin": TikTokAPIEndpoints.TIKTOK_DOMAIN,
}


#: The P0 endpoint set. Risk weights mirror the Douyin table so the scheduler
#: treats equivalent capabilities equivalently across platforms.
ENDPOINTS: Final = EndpointTable.of(
    EndpointSpec(
        name=CONTENT_DETAIL,
        path=TikTokAPIEndpoints.POST_DETAIL,
        required=("item_id",),
        build=content_detail_params,
        risk_weight=1.0,
        summary="Single post detail, video or photo mode",
    ),
    EndpointSpec(
        name=AUTHOR_PROFILE,
        path=TikTokAPIEndpoints.USER_DETAIL,
        # Either secUid or uniqueId is accepted, so the 'at least one' rule
        # lives in the builder rather than in this all-of list.
        required=(),
        build=author_profile_params,
        risk_weight=1.2,
        summary="Author profile by secUid or uniqueId",
    ),
    EndpointSpec(
        name=AUTHOR_POSTS,
        path=TikTokAPIEndpoints.USER_POST,
        required=("sec_uid",),
        build=author_posts_params,
        risk_weight=1.8,
        summary="Author post list, paged by cursor",
    ),
    EndpointSpec(
        name=COMMENTS,
        path=TikTokAPIEndpoints.POST_COMMENT,
        required=("aweme_id",),
        build=comments_params,
        risk_weight=1.5,
        summary="Top level comments on a post",
    ),
    EndpointSpec(
        name=COMMENT_REPLIES,
        path=TikTokAPIEndpoints.POST_COMMENT_REPLY,
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
    "TikTokAPIEndpoints",
]
