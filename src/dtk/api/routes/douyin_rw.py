"""Douyin Live-Streaming and Read/Write REST API routes (``/api/v1/douyin/rw/*``).

Exposes synchronous Pure-API endpoints for Douyin Web Live-Streaming and Read/Write
operations signed natively with ``ABogus`` / ``XBogus``:

* **Douyin Live-Streaming API**:
  - ``POST /api/v1/douyin/rw/live/room`` - fetch live stream room & FLV/HLS pull URLs by ``web_rid``
  - ``POST /api/v1/douyin/rw/live/room-by-id`` - fetch live stream room & FLV/HLS pull URLs by ``room_id``
  - ``POST /api/v1/douyin/rw/live/gift-rank`` - fetch live room gift/audience leaderboard
  - ``POST /api/v1/douyin/rw/live/followed`` - fetch active live rooms of followed accounts
  - ``POST /api/v1/douyin/rw/live/user-me`` - fetch authenticated viewer webcast profile
  - ``POST /api/v1/douyin/rw/live/search`` - search Douyin live rooms by keyword
  - ``POST /api/v1/douyin/rw/live/parse-url`` - extract ``web_rid`` and ``room_id`` from a Douyin live share URL
* **Douyin Social & Content Read/Write**:
  - ``POST /api/v1/douyin/rw/video/digg`` - like / unlike a Douyin post
  - ``POST /api/v1/douyin/rw/video/collect`` - bookmark / unbookmark a Douyin post
  - ``POST /api/v1/douyin/rw/video/bookmarks`` - read bookmarked posts
  - ``POST /api/v1/douyin/rw/video/likes`` - read an account's liked posts
  - ``POST /api/v1/douyin/rw/comment/publish`` - publish a comment or reply
  - ``POST /api/v1/douyin/rw/comment/delete`` - delete a comment
  - ``POST /api/v1/douyin/rw/comment/digg`` - like / unlike a comment
  - ``POST /api/v1/douyin/rw/social/follow`` - follow / unfollow a Douyin user
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from dtk.api.routes.support import ok
from dtk.platforms.douyin.read_write_client import (
    DEFAULT_DOUYIN_UA,
    DouyinReadWriteClient,
)

router = APIRouter(prefix="/api/v1/douyin/rw", tags=["douyin-read-write"])


class DouyinBaseSessionRequest(BaseModel):
    """Base request carrying optional or authenticated Douyin session cookies."""

    model_config = ConfigDict(extra="forbid")

    cookies: str | dict[str, str] = Field(
        default="",
        description="Douyin cookies in Netscape format, Cookie header format, or key-value object.",
    )
    user_agent: str = Field(default=DEFAULT_DOUYIN_UA, max_length=512)

    def build_client(self) -> DouyinReadWriteClient:
        return DouyinReadWriteClient(self.cookies, user_agent=self.user_agent)


class DouyinLiveRoomRequest(DouyinBaseSessionRequest):
    web_rid: str = Field(
        min_length=1,
        max_length=64,
        description="Douyin webcast room number from live.douyin.com/<web_rid>.",
    )
    room_id_str: str = Field(default="", max_length=64)


class DouyinLiveRoomByIdRequest(DouyinBaseSessionRequest):
    room_id: str = Field(
        min_length=1,
        max_length=64,
        description="Internal numeric room_id from webcast.amemv.com reflow link.",
    )
    sec_user_id: str = Field(default="", max_length=256)


class DouyinLiveGiftRankRequest(DouyinBaseSessionRequest):
    room_id: str = Field(min_length=1, max_length=64)
    rank_type: int = Field(default=30)


class DouyinLiveFollowedRequest(DouyinBaseSessionRequest):
    scene: str = Field(default="aweme_pc_follow_top", max_length=64)


class DouyinLiveSearchRequest(DouyinBaseSessionRequest):
    keyword: str = Field(min_length=1, max_length=100)
    offset: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=50)


class DouyinLiveParseUrlRequest(DouyinBaseSessionRequest):
    url: str = Field(min_length=1, max_length=2048)
    resolve_short_links: bool = Field(default=True)


class DouyinDiggVideoRequest(DouyinBaseSessionRequest):
    aweme_id: str = Field(min_length=1, max_length=64)
    like: bool = Field(default=True)


class DouyinCollectVideoRequest(DouyinBaseSessionRequest):
    aweme_id: str = Field(min_length=1, max_length=64)
    collect: bool = Field(default=True)


class DouyinBookmarksRequest(DouyinBaseSessionRequest):
    cursor: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=50)


class DouyinLikesRequest(DouyinBaseSessionRequest):
    sec_user_id: str = Field(min_length=1, max_length=256)
    max_cursor: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=50)


class DouyinPublishCommentRequest(DouyinBaseSessionRequest):
    aweme_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    reply_id: str = Field(default="", max_length=64)


class DouyinDeleteCommentRequest(DouyinBaseSessionRequest):
    cid: str = Field(min_length=1, max_length=64)
    aweme_id: str = Field(default="", max_length=64)


class DouyinDiggCommentRequest(DouyinBaseSessionRequest):
    cid: str = Field(min_length=1, max_length=64)
    aweme_id: str = Field(min_length=1, max_length=64)
    like: bool = Field(default=True)


class DouyinFollowUserRequest(DouyinBaseSessionRequest):
    user_id: str = Field(min_length=1, max_length=64)
    sec_user_id: str = Field(min_length=1, max_length=256)
    follow: bool = Field(default=True)


@router.post(
    "/live/room",
    summary="Fetch Douyin live room and FLV/HLS stream URLs by web_rid (live.douyin.com/webcast/room/web/enter/)",
)
async def route_douyin_live_room(request: Request, body: DouyinLiveRoomRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.get_live_room, body.web_rid, room_id_str=body.room_id_str)
    return ok(request, res)


@router.post(
    "/live/room-by-id",
    summary="Fetch Douyin live room and FLV/HLS stream URLs by room_id (webcast.amemv.com/webcast/room/reflow/info/)",
)
async def route_douyin_live_room_by_id(request: Request, body: DouyinLiveRoomByIdRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_live_room_by_room_id, body.room_id, sec_user_id=body.sec_user_id
    )
    return ok(request, res)


@router.post(
    "/live/gift-rank",
    summary="Fetch Douyin live room gift/audience leaderboard (live.douyin.com/webcast/ranklist/audience/)",
)
@router.post("/live/gift-ranking", include_in_schema=False)
async def route_douyin_live_gift_rank(request: Request, body: DouyinLiveGiftRankRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_live_gift_ranking, body.room_id, rank_type=body.rank_type
    )
    return ok(request, res)


@router.post(
    "/live/followed",
    summary="Fetch active live rooms of followed accounts (/webcast/web/feed/follow/)",
)
async def route_douyin_live_followed(request: Request, body: DouyinLiveFollowedRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.get_followed_live_rooms, scene=body.scene)
    return ok(request, res)


@router.post(
    "/live/user-me",
    summary="Fetch authenticated viewer webcast profile (live.douyin.com/webcast/user/me/)",
)
@router.post("/live/user-info", include_in_schema=False)
async def route_douyin_live_user_me(request: Request, body: DouyinBaseSessionRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.get_live_user_info)
    return ok(request, res)


@router.post(
    "/live/search",
    summary="Search Douyin live rooms by keyword (/aweme/v1/web/live/search/)",
)
async def route_douyin_live_search(request: Request, body: DouyinLiveSearchRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.search_live_rooms, body.keyword, offset=body.offset, count=body.count
    )
    return ok(request, res)


@router.post(
    "/live/parse-url",
    summary="Extract web_rid and room_id from a Douyin live URL or share text",
)
@router.post("/live/extract-id", include_in_schema=False)
async def route_douyin_live_parse_url(request: Request, body: DouyinLiveParseUrlRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.extract_webcast_id, body.url, resolve_short_links=body.resolve_short_links
    )
    return ok(request, res)


@router.post(
    "/video/digg", summary="Like or unlike a Douyin video (/aweme/v1/web/commit/item/digg/)"
)
async def route_douyin_digg_video(request: Request, body: DouyinDiggVideoRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.digg_video, body.aweme_id, like=body.like)
    return ok(request, res)


@router.post(
    "/video/collect",
    summary="Bookmark or unbookmark a Douyin video (/aweme/v1/web/aweme/collect/)",
)
async def route_douyin_collect_video(request: Request, body: DouyinCollectVideoRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.collect_video, body.aweme_id, collect=body.collect)
    return ok(request, res)


@router.post(
    "/video/bookmarks",
    summary="Read authenticated user's bookmarked videos (/aweme/v1/web/aweme/listcollection/)",
)
@router.post("/video/collected", include_in_schema=False)
async def route_douyin_bookmarks(request: Request, body: DouyinBookmarksRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.get_collected_videos, cursor=body.cursor, count=body.count)
    return ok(request, res)


@router.post(
    "/video/likes",
    summary="Read an account's liked videos (/aweme/v1/web/aweme/favorite/)",
)
@router.post("/video/liked", include_in_schema=False)
async def route_douyin_likes(request: Request, body: DouyinLikesRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_liked_videos,
        body.sec_user_id,
        max_cursor=body.max_cursor,
        count=body.count,
    )
    return ok(request, res)


@router.post(
    "/comment/publish",
    summary="Publish a comment or reply on a Douyin video (/aweme/v1/web/comment/publish)",
)
async def route_douyin_publish_comment(request: Request, body: DouyinPublishCommentRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.publish_comment, body.aweme_id, body.text, reply_id=body.reply_id
    )
    return ok(request, res)


@router.post(
    "/comment/delete",
    summary="Delete a comment on a Douyin video (/aweme/v1/web/comment/delete/)",
)
async def route_douyin_delete_comment(request: Request, body: DouyinDeleteCommentRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.delete_comment, body.cid, aweme_id=body.aweme_id)
    return ok(request, res)


@router.post(
    "/comment/digg",
    summary="Like or unlike a comment on a Douyin video (/aweme/v1/web/comment/digg)",
)
async def route_douyin_digg_comment(request: Request, body: DouyinDiggCommentRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.digg_comment, body.cid, body.aweme_id, like=body.like)
    return ok(request, res)


@router.post(
    "/social/follow",
    summary="Follow or unfollow a Douyin user (/aweme/v1/web/commit/follow/user/)",
)
@router.post("/user/follow", include_in_schema=False)
async def route_douyin_follow_user(request: Request, body: DouyinFollowUserRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.follow_user, body.user_id, body.sec_user_id, follow=body.follow
    )
    return ok(request, res)


__all__ = ["router"]
