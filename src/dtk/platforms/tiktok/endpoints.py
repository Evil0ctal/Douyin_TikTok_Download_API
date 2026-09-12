"""TikTok web endpoint URLs and the P0 endpoint table.

Ported from V4 ``crawlers/tiktok/web/endpoints.py`` (branch ``main``, commit
``8c98fb7``). URL constants are
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
    author_collections_params,
    author_followers_params,
    author_following_params,
    author_likes_params,
    author_posts_params,
    author_profile_params,
    comment_replies_params,
    comments_params,
    content_detail_params,
    mix_posts_params,
    session_check_params,
)


class TikTokAPIEndpoints:
    """TikTok web API endpoint URLs."""

    # TikTok domain
    TIKTOK_DOMAIN: Final = "https://www.tiktok.com"

    # Live domain
    WEBCAST_DOMAIN: Final = "https://webcast.tiktok.com"

    # Login
    LOGIN_ENDPOINT: Final = f"{TIKTOK_DOMAIN}/login/"

    # Whether the caller's own session is still alive. The passport service
    # answers about the cookies that asked, so nothing needs naming.
    PASSPORT_BEAT: Final = f"{TIKTOK_DOMAIN}/passport/token/beat/web/"

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

    # The folders a user has organized bookmarked posts into
    USER_COLLECTION_LIST: Final = f"{TIKTOK_DOMAIN}/api/user/collection_list/"

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
AUTHOR_LIKES: Final = "tiktok.author_likes"
AUTHOR_COLLECTIONS: Final = "tiktok.author_collections"
MIX_POSTS: Final = "tiktok.mix_posts"
AUTHOR_FOLLOWERS: Final = "tiktok.author_followers"
AUTHOR_FOLLOWING: Final = "tiktok.author_following"
SESSION_CHECK: Final = "tiktok.session_check"

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
        name=SESSION_CHECK,
        path=TikTokAPIEndpoints.PASSPORT_BEAT,
        required=(),
        build=session_check_params,
        # Unsigned on purpose. The passport service takes the cookies and
        # nothing else - a captured call carries no msToken, no X-Bogus and no
        # _signature - and appending them would make this the one probe whose
        # failure could mean either "the session is dead" or "the signer is".
        signed=False,
        risk_weight=1.0,
        summary="Whether the caller's own session is still alive",
    ),
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
        name=AUTHOR_LIKES,
        path=TikTokAPIEndpoints.USER_LIKE,
        required=("sec_uid",),
        build=author_likes_params,
        risk_weight=1.8,
        summary="Posts an author has publicly liked",
    ),
    EndpointSpec(
        name=AUTHOR_COLLECTIONS,
        path=TikTokAPIEndpoints.USER_COLLECTION_LIST,
        required=("sec_uid",),
        build=author_collections_params,
        risk_weight=1.8,
        # No empty_body_is_normal: TikTok answers a hidden list with a real
        # envelope carrying cursor/hasMore and simply omitting the list key
        # (see tests/unit/test_absence_matrix.py, "a private likes list is an
        # empty page not a silence") rather than the zero-byte body that is
        # Douyin author_likes's one measured exception. parse_author_collections
        # treats an absent collectionList the same way parse_author_list treats
        # an absent userList.
        #
        # The request side (secUid/cursor/count/coverFormat/needPinnedItemIds/
        # publicOnly) matches this project's own V4 crawler for this same path
        # (`fetch_user_collection_list`, added 2026-08-27), which needed a
        # caller-supplied cookie to get anything back at all. The response
        # envelope and field names below were never captured by that crawler
        # and are inferred by analogy to author_posts/author_likes - flagged
        # here the same way RISK_STATUS_CODES is flagged in parser.py, and
        # must be confirmed against a live capture before release.
        summary="The folders a user has organized bookmarked posts into",
    ),
    EndpointSpec(
        name=MIX_POSTS,
        path=TikTokAPIEndpoints.USER_MIX,
        required=("mix_id",),
        build=mix_posts_params,
        risk_weight=1.5,
        summary="Posts inside one playlist",
    ),
    EndpointSpec(
        name=AUTHOR_FOLLOWERS,
        path=TikTokAPIEndpoints.USER_FANS,
        required=("sec_uid",),
        build=author_followers_params,
        risk_weight=1.8,
        summary="Accounts that follow an author",
    ),
    EndpointSpec(
        name=AUTHOR_FOLLOWING,
        path=TikTokAPIEndpoints.USER_FOLLOW,
        required=("sec_uid",),
        build=author_following_params,
        risk_weight=1.8,
        summary="Accounts an author follows",
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
    "AUTHOR_COLLECTIONS",
    "AUTHOR_FOLLOWERS",
    "AUTHOR_FOLLOWING",
    "AUTHOR_LIKES",
    "AUTHOR_POSTS",
    "AUTHOR_PROFILE",
    "COMMENTS",
    "COMMENT_REPLIES",
    "CONTENT_DETAIL",
    "DEFAULT_HEADERS",
    "ENDPOINTS",
    "MIX_POSTS",
    "TikTokAPIEndpoints",
]
