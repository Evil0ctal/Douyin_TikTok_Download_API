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
    author_bookmarks_params,
    author_collections_params,
    author_followers_params,
    author_following_params,
    author_likes_params,
    author_posts_params,
    author_profile_params,
    author_reposts_params,
    collection_detail_params,
    collection_posts_params,
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

    # Other people's posts the user reposted onto their own profile
    USER_REPOST: Final = f"{TIKTOK_DOMAIN}/api/repost/item_list/"

    # The folders a user has organized bookmarked posts into
    USER_COLLECTION_LIST: Final = f"{TIKTOK_DOMAIN}/api/user/collection_list/"

    # Posts inside one of those folders. A different path from USER_COLLECT,
    # not the same one with an extra parameter: USER_COLLECT is keyed by the
    # account and this is keyed by the folder.
    COLLECTION_ITEM_LIST: Final = f"{TIKTOK_DOMAIN}/api/collection/item_list/"

    # One folder's own metadata, by id and without naming its owner
    COLLECTION_DETAIL: Final = f"{TIKTOK_DOMAIN}/api/collection/detail/"

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
AUTHOR_BOOKMARKS: Final = "tiktok.author_bookmarks"
COLLECTION_POSTS: Final = "tiktok.collection_posts"
AUTHOR_REPOSTS: Final = "tiktok.author_reposts"
COLLECTION_DETAIL: Final = "tiktok.collection_detail"
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
        name=AUTHOR_REPOSTS,
        path=TikTokAPIEndpoints.USER_REPOST,
        required=("sec_uid",),
        build=author_reposts_params,
        risk_weight=1.5,
        # Request side is from a capture; the notable thing about that capture
        # is whose profile it was. It was taken from a signed-in browser
        # looking at a DIFFERENT account, which is what suggested this is a
        # public tab rather than an owner-only one like author_bookmarks -
        # confirmed 2026-09-13 by reading it with a minted guest identity.
        #
        # Response side is the itemList/hasMore/cursor envelope, so
        # parse_author_posts answers for it; measured, not assumed.
        summary="Other people's posts an author reposted",
    ),
    EndpointSpec(
        name=AUTHOR_BOOKMARKS,
        path=TikTokAPIEndpoints.USER_COLLECT,
        required=("sec_uid",),
        build=author_bookmarks_params,
        risk_weight=1.8,
        # The request side is from a capture of the signed-in page; the response
        # side is the itemList/hasMore/cursor envelope every other item_list
        # endpoint on this host returns, and the items are ordinary posts rather
        # than a new object - which is why parse_author_posts is reused instead
        # of a parser of its own. Weaker than the assumption that put cover: null
        # into #753, but still an assumption: what would settle it is one
        # response from an account that has saved something.
        summary="Posts an author has saved, across every folder",
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
        # (`fetch_user_collection_list`, added 2026-08-27). The response
        # envelope and field names below were confirmed against a live
        # capture (PR #753): cover is a {"urlList": [...]} container, not a
        # bare string, and total arrives as a numeric string.
        #
        # #753 landed saying this endpoint "only ever answers its owner" and
        # that a guest gets nothing. That is wrong, and it was wrong in the way
        # that matters: it would have stopped anyone from trying. Measured
        # 2026-09-13 with a minted guest identity and no session cookie, against
        # an account holding one public folder and one private one, this
        # returned the public folder with its name, item count and cover, and
        # omitted the private one. A folder is public or private individually;
        # a guest sees the public ones. The V4 crawler needed a cookie because
        # it was asked about private folders, not because the path is gated.
        summary="The folders a user has organized bookmarked posts into",
    ),
    EndpointSpec(
        name=COLLECTION_DETAIL,
        path=TikTokAPIEndpoints.COLLECTION_DETAIL,
        required=("collection_id",),
        build=collection_detail_params,
        risk_weight=1.2,
        summary="One saved folder's own name, cover and size",
    ),
    EndpointSpec(
        name=COLLECTION_POSTS,
        path=TikTokAPIEndpoints.COLLECTION_ITEM_LIST,
        required=("collection_id",),
        build=collection_posts_params,
        risk_weight=1.5,
        # The third and last of the Saved tab's endpoints, and the one that
        # closes #754: author_collections lists the folders, author_bookmarks
        # returns every saved post across all of them, and this returns the
        # posts in one named folder. They are three distinct paths; the
        # evidence that they are not one path with different parameters is
        # BennoCrafter's V4 PR #738, which had already found all four.
        #
        # Response side is the itemList/hasMore/cursor envelope shared by every
        # item_list path on this host, so parse_author_posts is reused. Unlike
        # author_bookmarks that is measured rather than assumed: verified
        # 2026-09-13 with a guest identity against a public folder.
        summary="Posts inside one of an author's saved folders",
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
    "AUTHOR_BOOKMARKS",
    "AUTHOR_COLLECTIONS",
    "AUTHOR_FOLLOWERS",
    "AUTHOR_FOLLOWING",
    "AUTHOR_LIKES",
    "AUTHOR_POSTS",
    "AUTHOR_PROFILE",
    "AUTHOR_REPOSTS",
    "COLLECTION_DETAIL",
    "COLLECTION_POSTS",
    "COMMENTS",
    "COMMENT_REPLIES",
    "CONTENT_DETAIL",
    "DEFAULT_HEADERS",
    "ENDPOINTS",
    "MIX_POSTS",
    "TikTokAPIEndpoints",
]
