"""TikTok Full Read/Write REST API routes (``/api/v1/tiktok/rw/*``).

Exposes synchronous Pure-API endpoints for both reading and writing on TikTok Web
and ByteDance Protobuf Direct Messages (IM v1/v2) without launching a browser:

* **Direct Messages & Streaks (Protobuf IM v1/v2)**:
  - ``POST /api/v1/tiktok/rw/streaks`` - list active DM streaks (🔥)
  - ``POST /api/v1/tiktok/rw/streaks/maintain`` - auto-like incoming DMs & send video card to keep streaks alive
  - ``POST /api/v1/tiktok/rw/im/conversations`` - list conversations & recent messages (``cmd=203``)
  - ``POST /api/v1/tiktok/rw/im/messages`` - read conversation message history (``cmd=301``)
  - ``POST /api/v1/tiktok/rw/im/send-text`` - send text message (``cmd=100``, ``msg_type=7``)
  - ``POST /api/v1/tiktok/rw/im/send-video`` - send native TikTok video card (``cmd=100``, ``msg_type=8``)
  - ``POST /api/v1/tiktok/rw/im/react`` - add or remove ❤️ / emoji reaction on a message (``cmd=705``)
  - ``POST /api/v1/tiktok/rw/im/delete-message`` - delete a message (``cmd=701``)
  - ``POST /api/v1/tiktok/rw/im/delete-conversation`` - delete a conversation (``cmd=603``)
* **Social & Content Read/Write**:
  - ``POST /api/v1/tiktok/rw/video/digg`` - like / unlike a video (``/api/commit/item/digg/``)
  - ``POST /api/v1/tiktok/rw/video/collect`` - bookmark / unbookmark a video (``/api/item/collect/``)
  - ``POST /api/v1/tiktok/rw/video/likes`` - read liked videos (``/api/favorite/item_list/``)
  - ``POST /api/v1/tiktok/rw/video/bookmarks`` - read bookmarked videos (``/api/user/collect/item_list/``)
  - ``POST /api/v1/tiktok/rw/social/follow`` - follow / unfollow a user (``/api/follow/``)
  - ``POST /api/v1/tiktok/rw/social/followers/unfollowed`` - list followers not yet followed back
  - ``POST /api/v1/tiktok/rw/social/follow-back`` - automatically follow back all followers
  - ``POST /api/v1/tiktok/rw/feed/foryou`` - fetch For You feed + ready-to-send DM video share cards
  - ``POST /api/v1/tiktok/rw/account/cleanup`` - batch cleanup of likes, bookmarks & DM history
  - ``POST /api/v1/tiktok/rw/sign-ticket-guard`` - generate ``tt-ticket-guard`` ECDSA P-256 headers
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from dtk.api.routes.support import ok
from dtk.platforms.tiktok.im_protobuf import DEFAULT_DEVICE_ID, DEFAULT_WEB_UA
from dtk.platforms.tiktok.read_write_client import TikTokReadWriteClient
from dtk.signing.native.ticket_guard import TicketGuardCredentials, sign_ticket_guard

router = APIRouter(prefix="/api/v1/tiktok/rw", tags=["tiktok-read-write"])


class TicketGuardSpec(BaseModel):
    """Optional ``tt-ticket-guard`` ECDSA P-256 credentials for state-mutating web endpoints."""

    model_config = ConfigDict(extra="forbid")

    private_key_b64: str = Field(
        description="PKCS#8 DER/PEM private key in base64 from s_sdk_crypt_sdk."
    )
    ticket: str = Field(default="", description="Decrypted hex ticket (if already decrypted).")
    encrypt_ticket_b64: str = Field(
        default="",
        description="AES-GCM encrypted ticket from s_sdk_sign_data_key/tt_fetch (used if ticket is omitted).",
    )
    ts_sign: str = Field(description="ts_sign string from s_sdk_sign_data_key/tt_fetch.")
    public_key_raw_b64: str = Field(
        default="",
        description="Optional uncompressed X9.62 point in base64 (auto-derived if omitted).",
    )

    def to_credentials(self) -> TicketGuardCredentials:
        if self.ticket:
            return TicketGuardCredentials(
                private_key_b64=self.private_key_b64,
                ticket=self.ticket,
                ts_sign=self.ts_sign,
                public_key_raw_b64=self.public_key_raw_b64,
            )
        return TicketGuardCredentials.from_local_storage(
            {"ec_privateKey": self.private_key_b64},
            {"ts_sign": self.ts_sign, "encrypt_ticket": self.encrypt_ticket_b64},
        )


class BaseSessionRequest(BaseModel):
    """Base request carrying TikTok session cookies and optional client fingerprint."""

    model_config = ConfigDict(extra="forbid")

    cookies: str | dict[str, str] = Field(
        description="TikTok cookies in Netscape format, Cookie header format, or key-value object."
    )
    user_agent: str = Field(default=DEFAULT_WEB_UA, max_length=512)
    device_id: str = Field(default=DEFAULT_DEVICE_ID, max_length=64)
    ticket_guard: TicketGuardSpec | None = Field(default=None)

    def build_client(self) -> TikTokReadWriteClient:
        tg = self.ticket_guard.to_credentials() if self.ticket_guard else None
        return TikTokReadWriteClient(
            self.cookies,
            ticket_guard=tg,
            user_agent=self.user_agent,
            device_id=self.device_id,
        )


class SignTicketGuardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="API path to sign, e.g. /api/commit/item/digg/")
    ticket_guard: TicketGuardSpec
    timestamp: int | None = Field(default=None)


class MaintainStreaksRequest(BaseSessionRequest):
    like_incoming: bool = Field(
        default=True, description="Automatically react ❤️ to unliked incoming messages."
    )
    send_video_if_needed: bool = Field(
        default=True,
        description="Send a fresh TikTok video card if no message was sent by us today.",
    )
    force_send: bool = Field(
        default=False, description="Send a video card even if already maintained today."
    )
    max_likes_per_chat: int = Field(default=10, ge=1, le=50)


class ConversationMessagesRequest(BaseSessionRequest):
    conversation_id: str = Field(
        description="Conversation ID, e.g. 0:1:1000000000000000001:1000000000000000002"
    )
    peer_uid: int | None = Field(default=None)
    limit: int = Field(default=25, ge=1, le=100)


class SendTextRequest(BaseSessionRequest):
    conversation_id: str
    text: str = Field(min_length=1, max_length=2000)
    peer_uid: int | None = Field(default=None)


class SendVideoCardRequest(BaseSessionRequest):
    conversation_id: str
    card: dict[str, Any] | str = Field(description="Native aweType=800 video card JSON or object.")
    peer_uid: int | None = Field(default=None)


class ReactMessageRequest(BaseSessionRequest):
    conversation_id: str
    conversation_short_id: int
    server_message_id: int
    emoji: str = Field(default="❤️", max_length=16)
    remove: bool = Field(default=False)
    client_message_id: str = Field(default="")


class DeleteMessageRequest(BaseSessionRequest):
    conversation_id: str
    conversation_short_id: int
    server_message_id: int


class DeleteConversationRequest(BaseSessionRequest):
    conversation_id: str
    conversation_short_id: int
    last_message_index: int = Field(default=0)


class DiggVideoRequest(BaseSessionRequest):
    aweme_id: str = Field(min_length=1, max_length=64, description="TikTok video ID.")
    like: bool = Field(default=True, description="True to like (type=1), False to unlike (type=0).")


class CollectVideoRequest(BaseSessionRequest):
    item_id: str = Field(min_length=1, max_length=64, description="TikTok video ID.")
    sec_uid: str = Field(min_length=1, max_length=256, description="Account secUid.")
    collect: bool = Field(
        default=True, description="True to bookmark (action=1), False to unbookmark (action=2)."
    )


class PagedSecUidRequest(BaseSessionRequest):
    sec_uid: str = Field(min_length=1, max_length=256)
    count: int = Field(default=30, ge=1, le=50)
    cursor: str = Field(default="0")


class FollowUserRequest(BaseSessionRequest):
    user_id: str = Field(min_length=1, max_length=64)
    sec_user_id: str = Field(min_length=1, max_length=256)
    follow: bool = Field(
        default=True, description="True to follow (type=1), False to unfollow (type=0)."
    )
    from_type: str = Field(default="18")


class FollowBackRequest(BaseSessionRequest):
    max_follows: int = Field(default=15, ge=1, le=50)


class ForYouFeedRequest(BaseSessionRequest):
    count: int = Field(default=15, ge=1, le=35)


class CleanupAccountRequest(BaseSessionRequest):
    sec_uid: str = Field(min_length=1, max_length=256)
    unlike_all: bool = Field(default=True)
    uncollect_all: bool = Field(default=True)
    delete_all_messages: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/sign-ticket-guard", summary="Generate TikTok Web tt-ticket-guard ECDSA P-256 headers"
)
async def route_sign_ticket_guard(request: Request, body: SignTicketGuardRequest) -> Any:
    creds = body.ticket_guard.to_credentials()
    headers = sign_ticket_guard(body.path, creds, timestamp=body.timestamp)
    return ok(request, {"path": body.path, "headers": headers})


@router.post("/streaks", summary="List active TikTok DM streaks (🔥)")
async def route_list_streaks(request: Request, body: BaseSessionRequest) -> Any:
    client = body.build_client()
    streaks = await asyncio.to_thread(client.im.get_active_streaks)
    return ok(request, {"count": len(streaks), "streaks": [s.to_dict() for s in streaks]})


@router.post(
    "/streaks/maintain", summary="Auto-like incoming DMs and send video card to maintain streaks"
)
async def route_maintain_streaks(request: Request, body: MaintainStreaksRequest) -> Any:
    client = body.build_client()
    report = await asyncio.to_thread(
        client.maintain_streaks,
        like_incoming=body.like_incoming,
        send_video_if_needed=body.send_video_if_needed,
        force_send=body.force_send,
        max_likes_per_chat=body.max_likes_per_chat,
    )
    return ok(request, report)


@router.post("/im/conversations", summary="List DM conversations and recent messages (cmd=203)")
async def route_list_conversations(request: Request, body: BaseSessionRequest) -> Any:
    client = body.build_client()
    conv_map = await asyncio.to_thread(client.im.init_user_messages)
    conversations = [
        {
            "conversation_id": cid,
            "conversation_short_id": client.im.conv_short_ids.get(cid, 0),
            "messages": [m.to_dict() for m in msgs],
        }
        for cid, msgs in conv_map.items()
    ]
    return ok(request, {"count": len(conversations), "conversations": conversations})


@router.post("/im/messages", summary="Read message history in a conversation (cmd=301)")
async def route_get_messages(request: Request, body: ConversationMessagesRequest) -> Any:
    client = body.build_client()
    await asyncio.to_thread(client.im.init_user_messages, 1)
    msgs = await asyncio.to_thread(
        client.im.get_conversation_messages,
        body.conversation_id,
        peer_uid=body.peer_uid,
        limit=body.limit,
    )
    return ok(
        request,
        {
            "conversation_id": body.conversation_id,
            "conversation_short_id": client.im.conv_short_ids.get(body.conversation_id, 0),
            "count": len(msgs),
            "messages": [m.to_dict() for m in msgs],
        },
    )


@router.post("/im/send-text", summary="Send a text message to a TikTok DM conversation (cmd=100)")
async def route_send_text(request: Request, body: SendTextRequest) -> Any:
    client = body.build_client()
    server_msg_id = await asyncio.to_thread(
        client.im.send_text_message,
        body.conversation_id,
        body.text,
        peer_uid=body.peer_uid,
    )
    return ok(request, {"sent": server_msg_id > 0, "server_message_id": server_msg_id})


@router.post(
    "/im/send-video",
    summary="Send a native TikTok video card to a DM conversation (cmd=100, msg_type=8)",
)
async def route_send_video(request: Request, body: SendVideoCardRequest) -> Any:
    client = body.build_client()
    server_msg_id = await asyncio.to_thread(
        client.im.send_video_card,
        body.conversation_id,
        body.card,
        peer_uid=body.peer_uid,
    )
    return ok(request, {"sent": server_msg_id > 0, "server_message_id": server_msg_id})


@router.post("/im/react", summary="Add or remove an emoji reaction (❤️) on a DM message (cmd=705)")
async def route_react_message(request: Request, body: ReactMessageRequest) -> Any:
    client = body.build_client()
    success = await asyncio.to_thread(
        client.im.react_message,
        conversation_id=body.conversation_id,
        conversation_short_id=body.conversation_short_id,
        server_message_id=body.server_message_id,
        emoji=body.emoji,
        remove=body.remove,
        client_message_id=body.client_message_id,
    )
    return ok(request, {"reacted": success, "emoji": body.emoji, "removed": body.remove})


@router.post("/im/delete-message", summary="Delete a single DM message (cmd=701)")
async def route_delete_message(request: Request, body: DeleteMessageRequest) -> Any:
    client = body.build_client()
    deleted = await asyncio.to_thread(
        client.im.delete_message,
        conversation_id=body.conversation_id,
        conversation_short_id=body.conversation_short_id,
        server_message_id=body.server_message_id,
    )
    return ok(request, {"deleted": deleted, "server_message_id": body.server_message_id})


@router.post("/im/delete-conversation", summary="Delete an entire DM conversation (cmd=603)")
async def route_delete_conversation(request: Request, body: DeleteConversationRequest) -> Any:
    client = body.build_client()
    deleted = await asyncio.to_thread(
        client.im.delete_conversation,
        conversation_id=body.conversation_id,
        conversation_short_id=body.conversation_short_id,
        last_message_index=body.last_message_index,
    )
    return ok(request, {"deleted": deleted, "conversation_id": body.conversation_id})


@router.post("/video/digg", summary="Like or unlike a TikTok video (/api/commit/item/digg/)")
async def route_digg_video(request: Request, body: DiggVideoRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.digg_video, body.aweme_id, like=body.like)
    return ok(request, res)


@router.post("/video/collect", summary="Bookmark or unbookmark a TikTok video (/api/item/collect/)")
async def route_collect_video(request: Request, body: CollectVideoRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.collect_video, body.item_id, body.sec_uid, collect=body.collect
    )
    return ok(request, res)


@router.post("/video/likes", summary="Read an account's liked videos (/api/favorite/item_list/)")
async def route_get_likes(request: Request, body: PagedSecUidRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_liked_videos, body.sec_uid, count=body.count, cursor=body.cursor
    )
    return ok(request, res)


@router.post(
    "/video/bookmarks", summary="Read an account's bookmarked videos (/api/user/collect/item_list/)"
)
async def route_get_bookmarks(request: Request, body: PagedSecUidRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_collected_videos, body.sec_uid, count=body.count, cursor=body.cursor
    )
    return ok(request, res)


@router.post("/social/follow", summary="Follow or unfollow a TikTok user (/api/follow/)")
async def route_follow_user(request: Request, body: FollowUserRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.follow_user,
        body.user_id,
        body.sec_user_id,
        follow=body.follow,
        from_type=body.from_type,
    )
    return ok(request, res)


@router.post(
    "/social/followers/list", summary="Read an account's followers (/api/user/list/ scene=67)"
)
async def route_followers_list(request: Request, body: PagedSecUidRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_followers_list, body.sec_uid, count=body.count, min_cursor=body.cursor
    )
    return ok(request, res)


@router.post(
    "/social/following/list", summary="Read accounts an author follows (/api/user/list/ scene=21)"
)
async def route_following_list(request: Request, body: PagedSecUidRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.get_following_list, body.sec_uid, count=body.count, min_cursor=body.cursor
    )
    return ok(request, res)


@router.post("/social/followers/unfollowed", summary="List followers who are not yet followed back")
async def route_unfollowed_followers(request: Request, body: BaseSessionRequest) -> Any:
    client = body.build_client()
    users = await asyncio.to_thread(client.get_unfollowed_followers)
    return ok(request, {"count": len(users), "followers": users})


@router.post("/social/follow-back", summary="Automatically follow back all followers")
async def route_follow_back(request: Request, body: FollowBackRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.follow_back_all_followers, max_follows=body.max_follows)
    return ok(request, res)


@router.post("/feed/foryou", summary="Fetch For You feed and build native DM video share cards")
async def route_foryou_feed(request: Request, body: ForYouFeedRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(client.get_foryou_feed, count=body.count)
    return ok(request, res)


@router.post(
    "/account/cleanup",
    summary="Batch cleanup of liked videos, bookmarks, and DM conversations/messages",
)
async def route_cleanup_account(request: Request, body: CleanupAccountRequest) -> Any:
    client = body.build_client()
    res = await asyncio.to_thread(
        client.cleanup_account,
        body.sec_uid,
        unlike_all=body.unlike_all,
        uncollect_all=body.uncollect_all,
        delete_all_messages=body.delete_all_messages,
    )
    return ok(request, res)


__all__ = ["router"]
