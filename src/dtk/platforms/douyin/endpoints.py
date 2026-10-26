"""Douyin web endpoint URLs and the P0 endpoint table.

Ported from V4 ``crawlers/douyin/web/endpoints.py`` (branch ``main``, commit
``8c98fb7``). The URL constants are
the product of long trial and error and are reproduced verbatim; only the
comments were translated to English, per ``docs/design/16-salvage-and-debug.md``.

The full constant table is kept even for endpoints outside the P0 scope: it
costs nothing, and rediscovering a URL later is expensive. Only the endpoints
listed in ``ENDPOINTS`` are wired up; see ``docs/design/11-data-contracts.md``
for the scope split.
"""

from __future__ import annotations

from typing import Final

from dtk.core.types import LoginNeed
from dtk.platforms.base import EndpointSpec, EndpointTable
from dtk.platforms.douyin.params import (
    author_bookmarks_params,
    author_collections_params,
    author_likes_params,
    author_mixes_params,
    author_posts_params,
    author_profile_params,
    collection_posts_params,
    comment_replies_params,
    comments_params,
    content_detail_params,
    hashtag_detail_params,
    live_ranking_params,
    live_room_by_id_params,
    live_room_params,
    live_status_params,
    mix_posts_params,
    music_detail_params,
    music_posts_params,
    related_posts_params,
    session_check_params,
    trending_params,
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

    # Who the platform thinks the caller is. Answers about the client that
    # asked rather than about a user named in the request - but it answers for
    # a guest too, with a device uid of its own, so it identifies the client
    # and does not establish a login. The measurement is in
    # dtk.ops.probes.read_session.
    QUERY_USER: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/query/user/"

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

    # A sound: its title, its publisher and how many posts use it
    MUSIC_DETAIL: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/music/detail/"

    # The posts that use one sound
    MUSIC_AWEME: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/music/aweme/"

    # A hashtag's page. Douyin calls a hashtag a challenge throughout.
    CHALLENGE_DETAIL: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/challenge/detail/"

    # The posts under a hashtag. Answers a guest with no body at all - the
    # hashtag page's own call did the same in a browser that was not signed
    # in (2026-10-20) - so nothing here is registered against it until a
    # signed-in answer has been seen.
    CHALLENGE_AWEME: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/challenge/aweme/"

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

    # The mixes one author has published
    MIX_LIST: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/mix/list/"

    # Watch history
    USER_HISTORY: Final = f"{DOUYIN_DOMAIN}/aweme/v1/web/history/read/"

    # Posts the user bookmarked, across every folder. POST only: a GET is
    # answered 404 "Unsupported path(Janus)", which for a while read as the
    # endpoint having been retired (#790).
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

    # Whether each of many broadcasts is on air. Lives on the webcast host,
    # like reflow/info: live.douyin.com answers this path with a 404 (2026-10-26).
    LIVE_CHECK_ALIVE: Final = f"{LIVE_DOMAIN2}/webcast/room/check_alive/"

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
AUTHOR_LIKES: Final = "douyin.author_likes"
AUTHOR_COLLECTIONS: Final = "douyin.author_collections"
AUTHOR_BOOKMARKS: Final = "douyin.author_bookmarks"
COLLECTION_POSTS: Final = "douyin.collection_posts"
MIX_POSTS: Final = "douyin.mix_posts"
SESSION_CHECK: Final = "douyin.session_check"
RELATED_POSTS: Final = "douyin.related_posts"
MUSIC_DETAIL: Final = "douyin.music_detail"
MUSIC_POSTS: Final = "douyin.music_posts"
HASHTAG_DETAIL: Final = "douyin.hashtag_detail"
AUTHOR_MIXES: Final = "douyin.author_mixes"
LIVE_ROOM: Final = "douyin.live_room"
LIVE_ROOM_BY_ID: Final = "douyin.live_room_by_id"
LIVE_STATUS: Final = "douyin.live_status"
LIVE_RANKING: Final = "douyin.live_ranking"
TRENDING: Final = "douyin.trending"

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
        name=SESSION_CHECK,
        path=DouyinAPIEndpoints.QUERY_USER,
        # No caller arguments at all: the cookies are the whole question.
        required=(),
        build=session_check_params,
        risk_weight=1.0,
        summary="Which uid the platform binds to the caller's own client",
    ),
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
        # Measured 2026-10-17 with a minted guest: the first page answers, a
        # few posts short of the author's newest (#783), and every later page
        # is the 17-byte {"status_code":0} that classify.py reads as a refusal
        # (#782). Before this, both went to whichever identity rotation picked,
        # so an imported logged-in cookie sat idle while guests were cooled
        # for asking. Comments page fine as a guest the same day.
        login=LoginNeed.PREFERRED,
        paging_login=LoginNeed.REQUIRED,
        summary="Author post list, paged by max_cursor",
    ),
    EndpointSpec(
        name=AUTHOR_LIKES,
        path=DouyinAPIEndpoints.USER_FAVORITE_A,
        required=("sec_user_id",),
        build=author_likes_params,
        risk_weight=1.8,
        # Douyin says "these likes are private" by answering 200 with no body at
        # all. Measured 2026-09-10 against two different authors: zero bytes
        # both times, while `author_posts` for the same author on the same
        # identity returned 230KB and 439KB in the request immediately after.
        # So the silence belongs to this endpoint, not to the identity - and
        # without this flag, looking up anyone's likes cooled the identity that
        # asked, which is most of them, because most likes lists are private.
        empty_body_is_normal=True,
        # And the silence is all a guest ever gets. Measured 2026-10-20: one
        # author of fifteen had public likes, and a logged-in identity got 20
        # posts and a next page while three guests got zero bytes - the same
        # answer as a private list. Without this, every unpinned lookup went to
        # a guest and reported every author's likes as not there.
        login=LoginNeed.REQUIRED,
        # What the same login is told about a private list: `aweme_list: null`
        # and no `has_more`, measured on the other fourteen. Read as a page it
        # was a missing field, a 502 asking to be reported as a schema change.
        absent_when_null="aweme_list",
        summary="Posts an author has publicly liked",
    ),
    EndpointSpec(
        name=AUTHOR_COLLECTIONS,
        path=DouyinAPIEndpoints.USER_COLLECTS,
        # Nothing, and that is the point: this endpoint has no subject but the
        # session holding it. See author_collections_params.
        required=(),
        build=author_collections_params,
        risk_weight=1.8,
        session_bound=True,
        summary="The bookmark folders belonging to the signed-in identity",
    ),
    EndpointSpec(
        name=AUTHOR_BOOKMARKS,
        path=DouyinAPIEndpoints.USER_COLLECTION,
        method="POST",
        required=(),
        build=author_bookmarks_params,
        form=("count", "cursor"),
        risk_weight=1.8,
        session_bound=True,
        # The page's own Referer, and the CSRF header its SDK sends on a POST
        # when it holds no token. Measured 2026-10-17 on one signed-in jar:
        # neither is required - the dtk defaults of a bare-domain Referer and
        # an Origin returned the page, and so did dropping the CSRF header -
        # but a POST with neither Referer nor Origin came back as 7 bytes and
        # no list. They are sent because the page sends them.
        headers=(
            (
                "Referer",
                f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}"
                "/user/self?from_tab_name=main&showTab=favorite_collection",
            ),
            ("x-secsdk-csrf-token", "DOWNGRADE"),
        ),
        summary="Every post the signed-in identity has bookmarked, across folders",
    ),
    EndpointSpec(
        name=COLLECTION_POSTS,
        path=DouyinAPIEndpoints.USER_COLLECTS_VIDEO,
        # The builder keyword, not Douyin's spelling: the platform calls it
        # collects_id and collection_posts_params does the translating.
        required=("collection_id",),
        build=collection_posts_params,
        risk_weight=1.5,
        summary="Posts inside one bookmark folder",
    ),
    EndpointSpec(
        name=MIX_POSTS,
        path=DouyinAPIEndpoints.MIX_AWEME,
        required=("mix_id",),
        build=mix_posts_params,
        risk_weight=1.5,
        summary="Posts inside one mix (series)",
    ),
    EndpointSpec(
        name=RELATED_POSTS,
        path=DouyinAPIEndpoints.POST_RELATED,
        required=("aweme_id",),
        build=related_posts_params,
        risk_weight=1.2,
        # Measured 2026-10-20 with a guest: a post id that does not exist is
        # answered with ten posts all the same, so a full answer says nothing
        # about whether the post it names is there.
        summary="Posts the site recommends beside one post",
    ),
    EndpointSpec(
        name=MUSIC_DETAIL,
        path=DouyinAPIEndpoints.MUSIC_DETAIL,
        required=("music_id",),
        build=music_detail_params,
        risk_weight=1.0,
        # A sound that does not exist is `status_code: 0` and `music_info:
        # null` (measured 2026-10-20), which read as a payload with a field
        # missing - a schema change - instead of as not found.
        absent_when_null="music_info",
        summary="One sound: its title, publisher and how many posts use it",
    ),
    EndpointSpec(
        name=MUSIC_POSTS,
        path=DouyinAPIEndpoints.MUSIC_AWEME,
        required=("music_id",),
        build=music_posts_params,
        risk_weight=1.5,
        # Pages for a guest, unlike an author's posts: page two of a sound
        # with 100k posts answered nine more and a next cursor (2026-10-20).
        summary="Posts that use one sound, paged by cursor",
    ),
    EndpointSpec(
        name=HASHTAG_DETAIL,
        path=DouyinAPIEndpoints.CHALLENGE_DETAIL,
        required=("ch_id",),
        build=hashtag_detail_params,
        risk_weight=1.0,
        # A hashtag nobody has is `status_code: 0` and `ch_info: null`
        # (measured 2026-10-20), the same shape as a missing sound.
        absent_when_null="ch_info",
        summary="One hashtag: its name, cover and counts",
    ),
    EndpointSpec(
        name=AUTHOR_MIXES,
        path=DouyinAPIEndpoints.MIX_LIST,
        required=("sec_user_id",),
        build=author_mixes_params,
        risk_weight=1.5,
        # Answers a guest in full: three mixes and has_more 0 for an author
        # with three (2026-10-20).
        summary="The mixes an author has published",
    ),
    EndpointSpec(
        name=LIVE_ROOM,
        path=DouyinAPIEndpoints.LIVE_INFO,
        required=("web_rid",),
        build=live_room_params,
        risk_weight=1.2,
        # The live site's own Referer; the www.douyin.com one the adapter
        # sends by default is not where this page lives.
        headers=(
            ("Referer", f"{DouyinAPIEndpoints.LIVE_DOMAIN}/"),
            ("Origin", DouyinAPIEndpoints.LIVE_DOMAIN),
        ),
        # Answers a guest with the room, its viewers and its pull URLs while
        # it is live, and with the room marked ended when it is not
        # (2026-10-20). A web_rid nobody has is `status_code: 4001038`.
        summary="One live room: whether it is on, and how to play it",
    ),
    EndpointSpec(
        name=LIVE_ROOM_BY_ID,
        path=DouyinAPIEndpoints.LIVE_INFO_ROOM_ID,
        required=("room_id",),
        build=live_room_by_id_params,
        risk_weight=1.2,
        # The live site's headers, the ones it was measured with: the
        # www.douyin.com Referer is no page of this host's either.
        headers=(
            ("Referer", f"{DouyinAPIEndpoints.LIVE_DOMAIN}/"),
            ("Origin", DouyinAPIEndpoints.LIVE_DOMAIN),
        ),
        # Answers a guest with the room, its web_rid, its viewers and its pull
        # URLs; a room_id nobody has is `status_code: 10011`, "Request params
        # error" (2026-10-26).
        summary="One live room, by the broadcast's own id",
    ),
    EndpointSpec(
        name=LIVE_STATUS,
        path=DouyinAPIEndpoints.LIVE_CHECK_ALIVE,
        required=("room_ids",),
        build=live_status_params,
        risk_weight=1.0,
        headers=(
            ("Referer", f"{DouyinAPIEndpoints.LIVE_DOMAIN}/"),
            ("Origin", DouyinAPIEndpoints.LIVE_DOMAIN),
        ),
        # Answers a guest with `{alive, room_id}` for each broadcast that
        # existed, in the order asked, and leaves out an id that never did
        # (2026-10-26).
        summary="Whether each of many broadcasts is on air",
    ),
    EndpointSpec(
        name=LIVE_RANKING,
        path=DouyinAPIEndpoints.LIVE_GIFT_RANK,
        required=("room_id",),
        build=live_ranking_params,
        risk_weight=1.2,
        headers=(
            ("Referer", f"{DouyinAPIEndpoints.LIVE_DOMAIN}/"),
            ("Origin", DouyinAPIEndpoints.LIVE_DOMAIN),
        ),
        # Answers a guest with up to 200 viewers in order, every score 0; a
        # room_id nobody has is `status_code: 10011` (2026-10-26). Whether a
        # signed-in account is told the scores was not measured, so a guest
        # is not marked as getting less.
        summary="A broadcast's audience ranking",
    ),
    EndpointSpec(
        name=TRENDING,
        path=DouyinAPIEndpoints.DOUYIN_HOT_SEARCH,
        required=(),
        build=trending_params,
        risk_weight=1.0,
        # The same lists for everyone, answered to a guest: 51 lines on the
        # main board, 50 on the challenge board (2026-10-20).
        summary="One of the hot boards: what people are searching for",
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
    "AUTHOR_BOOKMARKS",
    "AUTHOR_COLLECTIONS",
    "AUTHOR_LIKES",
    "AUTHOR_MIXES",
    "AUTHOR_POSTS",
    "AUTHOR_PROFILE",
    "COLLECTION_POSTS",
    "COMMENTS",
    "COMMENT_REPLIES",
    "CONTENT_DETAIL",
    "DEFAULT_HEADERS",
    "ENDPOINTS",
    "HASHTAG_DETAIL",
    "LIVE_RANKING",
    "LIVE_ROOM",
    "LIVE_ROOM_BY_ID",
    "LIVE_STATUS",
    "MIX_POSTS",
    "MUSIC_DETAIL",
    "MUSIC_POSTS",
    "RELATED_POSTS",
    "TRENDING",
    "DouyinAPIEndpoints",
]
