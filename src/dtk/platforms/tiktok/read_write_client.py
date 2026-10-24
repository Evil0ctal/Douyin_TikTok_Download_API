"""Unified Pure-API Read/Write Client for TikTok Web & Direct Messages.

Combines:
* :mod:`dtk.signing.native.tiktok_sign` (``X-Dynosaur``, ``X-Gnarly``, ``X-Bogus``)
* :mod:`dtk.signing.native.ticket_guard` (``tt-ticket-guard-*`` ECDSA P-256 request signing)
* ``x-secsdk-csrf-token`` one-time CSRF handshake via ``HEAD``
* :class:`~dtk.platforms.tiktok.im_protobuf.TikTokIMClient` (Protobuf IM v1/v2 & Streaks)
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from dtk.platforms.tiktok.im_protobuf import (
    DEFAULT_DEVICE_ID,
    DEFAULT_WEB_UA,
    IMMessage,
    StreakChatInfo,
    TikTokIMClient,
)
from dtk.signing.native import tiktok_sign
from dtk.signing.native.ticket_guard import TicketGuardCredentials, sign_ticket_guard


def normalize_cookie_jar(
    cookies: str | Mapping[str, str] | Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Converts Netscape, Cookie header, JSON list, or dict cookies into a ``{name: value}`` map."""
    if isinstance(cookies, Mapping):
        return {str(k): str(v) for k, v in cookies.items()}
    if isinstance(cookies, str):
        text = cookies.strip()
        if text.startswith(("{", "[")):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, Mapping):
                    return {str(k): str(v) for k, v in loaded.items()}
                if isinstance(loaded, Sequence):
                    return {
                        str(c["name"]): str(c["value"])
                        for c in loaded
                        if isinstance(c, Mapping) and c.get("name") and c.get("value") is not None
                    }
            except Exception:
                pass
        out: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain = parts[0].strip()
                    key = parts[5].strip()
                    val = parts[6].strip()
                    # Prefer domain-wide .tiktok.com msToken over host-only www.tiktok.com duplicates
                    if key == "msToken" and key in out and not domain.startswith("."):
                        continue
                    out[key] = val
                    continue
            if line.lower().startswith("cookie:"):
                line = line.split(":", 1)[1].strip()
            for pair in line.split(";"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k.strip():
                        out[k.strip()] = v.strip()
        return out
    return {
        str(c["name"]): str(c["value"])
        for c in cookies
        if isinstance(c, Mapping) and c.get("name") and c.get("value") is not None
    }


class TikTokReadWriteClient:
    """High-level Read/Write client for TikTok Web & Protobuf Direct Messages."""

    def __init__(
        self,
        cookies: str | Mapping[str, str] | Sequence[Mapping[str, Any]],
        *,
        ticket_guard: TicketGuardCredentials | None = None,
        user_agent: str = DEFAULT_WEB_UA,
        device_id: str = DEFAULT_DEVICE_ID,
    ) -> None:
        self.cookie_map = normalize_cookie_jar(cookies)
        self.ticket_guard = ticket_guard
        self.im = TikTokIMClient(self.cookie_map, user_agent=user_agent, device_id=device_id)

    @property
    def my_uid(self) -> int:
        return self.im.my_uid

    def _base_web_params(
        self, extra: Sequence[tuple[str, str]] = (), *, from_page: str = "user"
    ) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = [
            ("WebIdLastTime", str(int(time.time()) - 86400)),
            ("aid", "1988"),
            ("app_language", "en-US"),
            ("app_name", "tiktok_web"),
            ("browser_language", "en-US"),
            ("browser_name", "Mozilla"),
            ("browser_online", "true"),
            ("browser_platform", "Win32"),
            ("browser_version", "5.0 (Windows)"),
            ("channel", "tiktok_web"),
            ("cookie_enabled", "true"),
            ("data_collection_enabled", "true"),
            ("device_id", self.im.device_id),
            ("device_platform", "web_pc"),
            ("focus_state", "true"),
            ("from_page", from_page),
            ("history_len", "4"),
            ("is_fullscreen", "false"),
            ("is_page_visible", "true"),
            ("language", "en"),
            ("odinId", str(self.my_uid or "")),
            ("os", "windows"),
            ("priority_region", self.im.region),
            ("referer", ""),
            ("region", self.im.region),
            ("root_referer", "https://www.tiktok.com/"),
            ("screen_height", "1080"),
            ("screen_width", "1920"),
            ("tz_name", "UTC"),
            ("user_is_login", "true"),
            ("webcast_language", "en-US"),
        ]
        if self.im.verify_fp:
            params.append(("verifyFp", self.im.verify_fp))
        params.extend(extra)
        return params

    @staticmethod
    def _can_use_curl_cffi() -> bool:
        if getattr(urllib.request.urlopen, "__module__", "") != "urllib.request":
            return False
        try:
            import curl_cffi.requests  # noqa: F401

            return True
        except ImportError:
            return False

    def _fetch_secsdk_csrf_token(self, path: str) -> str:
        url = f"https://www.tiktok.com{path}"
        hdrs = {
            "Cookie": self.im.cookie_header,
            "User-Agent": self.im.user_agent,
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
            "x-secsdk-csrf-request": "1",
            "x-secsdk-csrf-version": "1.2.22",
        }
        try:
            if self._can_use_curl_cffi():
                from curl_cffi import requests as creq

                resp = creq.head(url, headers=hdrs, impersonate="chrome", timeout=15)
                ware = resp.headers.get("x-ware-csrf-token") or ""
                if "," in ware:
                    return ware.split(",")[1]
                return ware
            req = urllib.request.Request(url, headers=hdrs, method="HEAD")
            with urllib.request.urlopen(req, context=self.im._ssl_ctx, timeout=15) as resp:
                ware = resp.headers.get("x-ware-csrf-token") or ""
                if "," in ware:
                    return ware.split(",")[1]
        except Exception:
            pass
        return ""

    def _request_web_json(
        self,
        method: str,
        path: str,
        params: Sequence[tuple[str, str]],
        *,
        body: bytes = b"",
        use_ticket_guard: bool = False,
        use_secsdk_csrf: bool = False,
        ms_token_override: str | None = None,
    ) -> dict[str, Any]:
        ms_val = self.im.ms_token if ms_token_override is None else ms_token_override
        q_str, _ = tiktok_sign.sign(
            params,
            self.im.user_agent,
            ms_token=ms_val,
            body=body,
            sequence=self.im._next_sign_seq(),
        )
        cookie_hdr = self.im.cookie_header
        if ms_token_override == "":
            no_ms = {k: v for k, v in self.cookie_map.items() if k != "msToken"}
            cookie_hdr = "; ".join(f"{k}={v}" for k, v in no_ms.items())
        headers: dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie_hdr,
            "Origin": "https://www.tiktok.com",
            "Referer": "https://www.tiktok.com/",
            "User-Agent": self.im.user_agent,
        }
        if method.upper() == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            csrf = self.cookie_map.get("tt_csrf_token", "")
            if csrf:
                headers["tt-csrf-token"] = csrf
        if use_secsdk_csrf:
            secsdk = self._fetch_secsdk_csrf_token(path)
            if secsdk:
                headers["x-secsdk-csrf-token"] = secsdk
        if use_ticket_guard and self.ticket_guard is not None:
            headers.update(sign_ticket_guard(path, self.ticket_guard))

        url = f"https://www.tiktok.com{path}?{q_str}"
        if self._can_use_curl_cffi():
            from curl_cffi import requests as creq

            resp = creq.request(
                method.upper(),
                url,
                headers=headers,
                data=body if method.upper() == "POST" else None,
                impersonate="chrome",
                timeout=15,
            )
            raw = resp.text
            orcas_res = resp.headers.get("tt_orcas_res")
            guard_res = resp.headers.get("tt-ticket-guard-result")
            if (
                not raw.strip()
                and orcas_res == "1"
                and ms_token_override is None
                and method.upper() == "GET"
            ):
                return self._request_web_json(
                    method,
                    path,
                    params,
                    body=body,
                    use_ticket_guard=use_ticket_guard,
                    use_secsdk_csrf=use_secsdk_csrf,
                    ms_token_override="",
                )
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                data["_guard_result"] = guard_res
                data["_orcas_result"] = orcas_res
            return data

        req = urllib.request.Request(
            url,
            data=body if method.upper() == "POST" else None,
            headers=headers,
            method=method.upper(),
        )
        with urllib.request.urlopen(req, context=self.im._ssl_ctx, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                data["_guard_result"] = resp.headers.get("tt-ticket-guard-result")
                data["_orcas_result"] = resp.headers.get("tt_orcas_res")
            return data

    # -----------------------------------------------------------------------
    # Read Operations
    # -----------------------------------------------------------------------

    def get_foryou_feed(self, count: int = 15) -> dict[str, Any]:
        """Fetches the ``/api/recommend/item_list/`` feed and builds ready-to-send DM video cards (``aweType=800``)."""
        params = self._base_web_params([("count", str(count))], from_page="fyp")
        data = self._request_web_json("GET", "/api/recommend/item_list/", params)
        cards: list[dict[str, Any]] = []
        for item in data.get("itemList") or []:
            item_id = str(item.get("id") or "")
            author = item.get("author") or {}
            video = item.get("video") or {}
            uid = str(author.get("id") or "")
            sec_uid = str(author.get("secUid") or "")
            thumb = str(author.get("avatarThumb") or "")
            cover = str(video.get("cover") or video.get("originCover") or "")
            if not (item_id and uid and cover):
                continue
            cards.append(
                {
                    "aweType": 800,
                    "itemId": item_id,
                    "uid": uid,
                    "secUID": sec_uid,
                    "content_thumb": {"url_list": [thumb], "uri": thumb},
                    "content_name": str(
                        author.get("nickname") or author.get("uniqueId") or "TikTok"
                    ),
                    "content_title": str(item.get("desc") or "")[:120],
                    "cover_url": {"url_list": [cover], "uri": cover},
                    "cover_width": int(video.get("width") or 1080),
                    "cover_height": int(video.get("height") or 1920),
                }
            )
        return {"items": data.get("itemList") or [], "dm_video_cards": cards}

    def get_liked_videos(
        self, sec_uid: str, *, count: int = 30, cursor: str = "0"
    ) -> dict[str, Any]:
        params = self._base_web_params(
            [("secUid", sec_uid), ("count", str(count)), ("cursor", cursor)]
        )
        return self._request_web_json("GET", "/api/favorite/item_list/", params)

    def get_collected_videos(
        self, sec_uid: str, *, count: int = 30, cursor: str = "0"
    ) -> dict[str, Any]:
        params = self._base_web_params(
            [("secUid", sec_uid), ("count", str(count)), ("cursor", cursor)]
        )
        return self._request_web_json("GET", "/api/user/collect/item_list/", params)

    def get_reposted_videos(
        self, sec_uid: str, *, count: int = 30, cursor: str = "0"
    ) -> dict[str, Any]:
        params = self._base_web_params(
            [("secUid", sec_uid), ("count", str(count)), ("cursor", cursor)]
        )
        return self._request_web_json("GET", "/api/repost/item_list/", params)

    def get_followers_list(
        self, sec_uid: str, *, count: int = 30, min_cursor: str = "0"
    ) -> dict[str, Any]:
        """Fetches an account's followers via ``GET /api/user/list/`` (``scene=67``)."""
        params = self._base_web_params(
            [
                ("secUid", sec_uid),
                ("count", str(count)),
                ("minCursor", min_cursor),
                ("maxCursor", "0"),
                ("scene", "67"),
            ]
        )
        return self._request_web_json("GET", "/api/user/list/", params)

    def get_following_list(
        self, sec_uid: str, *, count: int = 30, min_cursor: str = "0"
    ) -> dict[str, Any]:
        """Fetches accounts an author follows via ``GET /api/user/list/`` (``scene=21``)."""
        params = self._base_web_params(
            [
                ("secUid", sec_uid),
                ("count", str(count)),
                ("minCursor", min_cursor),
                ("maxCursor", "0"),
                ("scene", "21"),
            ]
        )
        return self._request_web_json("GET", "/api/user/list/", params)

    def get_unfollowed_followers(
        self, *, count: int = 30, sec_uid: str | None = None
    ) -> list[dict[str, str]]:
        """Returns followers who follow this account (``relation == 6`` or ``follower_status == 1, follow_status == 0``)."""
        unfollowed: dict[str, dict[str, str]] = {}
        if sec_uid:
            try:
                flist = self.get_followers_list(sec_uid, count=count)
                for entry in flist.get("userList") or []:
                    u = entry.get("user") or {}
                    if int(u.get("relation") or 0) == 6:
                        uid = str(u.get("id") or "")
                        s_uid = str(u.get("secUid") or "")
                        if uid and s_uid:
                            unfollowed[uid] = {
                                "uid": uid,
                                "sec_uid": s_uid,
                                "unique_id": str(u.get("uniqueId") or ""),
                                "nickname": str(u.get("nickname") or ""),
                            }
            except Exception:
                pass
        group_list = json.dumps(
            [{"count": count, "group": 7, "is_mark_read": 0, "max_time": 0, "min_time": 0}],
            separators=(",", ":"),
        )
        params = self._base_web_params([("group_list", group_list)], from_page="fyp")
        data = self._request_web_json("GET", "/api/notice/multi/", params)
        for nlist in data.get("notice_lists") or []:
            for item in nlist.get("notice_list") or []:
                u = (item.get("follow") or {}).get("from_user") or (item.get("at") or {}).get(
                    "user"
                )
                if not isinstance(u, Mapping):
                    continue
                uid = str(u.get("uid") or "")
                s_uid = str(u.get("sec_uid") or "")
                follow_st = int(u.get("follow_status") or 0)
                follower_st = int(u.get("follower_status") or 0)
                if uid and s_uid and follow_st == 0 and (follower_st == 1 or "follow" in item):
                    unfollowed[uid] = {
                        "uid": uid,
                        "sec_uid": s_uid,
                        "unique_id": str(u.get("unique_id") or ""),
                        "nickname": str(u.get("nickname") or ""),
                    }
        return list(unfollowed.values())

    # -----------------------------------------------------------------------
    # Write Operations
    # -----------------------------------------------------------------------

    def digg_video(self, aweme_id: str, *, like: bool = True) -> dict[str, Any]:
        """Likes (``type=1``) or unlikes (``type=0``) a TikTok video via ``POST /api/commit/item/digg/``."""
        params = self._base_web_params(
            [
                ("aweme_id", aweme_id),
                ("type", "1" if like else "0"),
            ]
        )
        return self._request_web_json(
            "POST",
            "/api/commit/item/digg/",
            params,
            use_ticket_guard=True,
            use_secsdk_csrf=True,
        )

    def collect_video(self, item_id: str, sec_uid: str, *, collect: bool = True) -> dict[str, Any]:
        """Bookmarks (``action=1``) or unbookmarks (``action=2``) a video via ``POST /api/item/collect/``."""
        params = self._base_web_params(
            [
                ("action", "1" if collect else "2"),
                ("itemId", item_id),
                ("secUid", sec_uid),
            ]
        )
        return self._request_web_json(
            "POST",
            "/api/item/collect/",
            params,
            use_ticket_guard=True,
            use_secsdk_csrf=True,
        )

    def follow_user(
        self,
        user_id: str,
        sec_user_id: str,
        *,
        follow: bool = True,
        from_type: str = "18",
    ) -> dict[str, Any]:
        """Follows (``type=1``) or unfollows (``type=0``) a user via ``POST /api/follow/``."""
        act = "1" if follow else "0"
        params = self._base_web_params(
            [
                ("action_type", act),
                ("channel_id", "0"),
                ("from", from_type),
                ("fromWeb", "1"),
                ("from_pre", "0"),
                ("sec_user_id", sec_user_id),
                ("type", act),
                ("user_id", user_id),
            ]
        )
        return self._request_web_json(
            "POST",
            "/api/follow/",
            params,
            use_ticket_guard=True,
            use_secsdk_csrf=True,
        )

    def follow_back_all_followers(self, *, max_follows: int = 15) -> dict[str, Any]:
        """Finds all followers not yet followed back and follows them."""
        targets = self.get_unfollowed_followers()
        results: list[dict[str, Any]] = []
        followed_count = 0
        for u in targets[:max_follows]:
            resp = self.follow_user(u["uid"], u["sec_uid"], follow=True)
            status_msg = str(resp.get("status_msg") or "")
            ok = resp.get("status_code") == 0 and "ghosted" not in status_msg.lower()
            if ok:
                followed_count += 1
            results.append({"user": u, "ok": ok, "response": resp})
            if "ghosted" in status_msg.lower():
                break
        return {
            "unfollowed_found": len(targets),
            "followed_count": followed_count,
            "results": results,
        }

    def maintain_streaks(
        self,
        *,
        like_incoming: bool = True,
        send_video_if_needed: bool = True,
        force_send: bool = False,
        max_likes_per_chat: int = 10,
    ) -> dict[str, Any]:
        """Runs automated DM streak maintenance and incoming message liking across all active streak chats."""
        streaks = self.im.get_active_streaks()
        self.im.init_user_messages(max_pages=2)
        feed_cards: list[dict[str, Any]] = []

        now_local = datetime.now().astimezone()
        today_start_ms = int(
            now_local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        )

        chat_reports: list[dict[str, Any]] = []
        total_liked = 0
        total_sent = 0

        for streak in streaks:
            msgs = self.im.get_conversation_messages(
                streak.conversation_id,
                peer_uid=streak.peer_uid,
                limit=25,
            )
            liked_in_chat = 0
            if like_incoming and msgs:
                unliked = [
                    m
                    for m in msgs
                    if m.sender == streak.peer_uid
                    and not m.is_system_message
                    and self.my_uid not in m.liked_by_uids
                ]
                for m in unliked[-max_likes_per_chat:]:
                    if self.im.react_message(
                        conversation_id=m.conversation_id,
                        conversation_short_id=m.conversation_short_id,
                        server_message_id=m.server_message_id,
                        emoji="❤️",
                        client_message_id=m.client_message_id,
                    ):
                        liked_in_chat += 1
                        total_liked += 1

            sent_today_im = any(
                m.sender == self.my_uid
                and not m.is_system_message
                and m.create_time_ms >= today_start_ms
                for m in msgs
            )
            already_maintained = streak.did_i_contribute_today() or sent_today_im
            sent_server_msg_id = 0

            if send_video_if_needed and (force_send or not already_maintained):
                if not feed_cards:
                    feed_cards = self.get_foryou_feed(count=12).get("dm_video_cards") or []
                if feed_cards:
                    existing_ids = {m.shared_video_id for m in msgs if m.shared_video_id}
                    chosen = next(
                        (c for c in feed_cards if str(c.get("itemId")) not in existing_ids),
                        feed_cards[0],
                    )
                    sent_server_msg_id = self.im.send_video_card(
                        streak.conversation_id,
                        chosen,
                        peer_uid=streak.peer_uid,
                    )
                    if sent_server_msg_id > 0:
                        total_sent += 1

            chat_reports.append(
                {
                    "streak": streak.to_dict(),
                    "messages_checked": len(msgs),
                    "liked_incoming": liked_in_chat,
                    "already_maintained_today": already_maintained,
                    "sent_server_message_id": sent_server_msg_id,
                }
            )

        return {
            "active_streaks_count": len(streaks),
            "total_liked_messages": total_liked,
            "total_videos_sent": total_sent,
            "chats": chat_reports,
        }

    def cleanup_account(
        self,
        sec_uid: str,
        *,
        unlike_all: bool = True,
        uncollect_all: bool = True,
        delete_all_messages: bool = True,
    ) -> dict[str, Any]:
        """Performs a comprehensive cleanup of liked videos, collected bookmarks, and DM conversations/messages while preserving friends & streaks."""
        unliked_ids: list[str] = []
        uncollected_ids: list[str] = []
        deleted_messages = 0
        deleted_conversations = 0

        if unlike_all and sec_uid:
            fav = self.get_liked_videos(sec_uid, count=30, cursor="0")
            for it in fav.get("itemList") or []:
                vid = str(it.get("id") or "")
                if vid:
                    res = self.digg_video(vid, like=False)
                    if res.get("status_code") == 0:
                        unliked_ids.append(vid)

        if uncollect_all and sec_uid:
            col = self.get_collected_videos(sec_uid, count=30, cursor="0")
            for it in col.get("itemList") or []:
                vid = str(it.get("id") or "")
                if vid:
                    res = self.collect_video(vid, sec_uid, collect=False)
                    if res.get("status_code") == 0:
                        uncollected_ids.append(vid)

        if delete_all_messages:
            self.im.init_user_messages(max_pages=5)
            for conv_id, short_id in list(self.im.conv_short_ids.items()):
                msgs = self.im.get_conversation_messages(conv_id, limit=50)
                max_idx = 0
                for m in msgs:
                    if m.index_in_conversation > max_idx:
                        max_idx = m.index_in_conversation
                    if self.im.delete_message(
                        conversation_id=conv_id,
                        conversation_short_id=short_id,
                        server_message_id=m.server_message_id,
                    ):
                        deleted_messages += 1
                if self.im.delete_conversation(
                    conversation_id=conv_id,
                    conversation_short_id=short_id,
                    last_message_index=max_idx,
                ):
                    deleted_conversations += 1
            self.im.delete_stranger_conversations()

        return {
            "unliked_videos": unliked_ids,
            "uncollected_videos": uncollected_ids,
            "deleted_messages_count": deleted_messages,
            "deleted_conversations_count": deleted_conversations,
        }


__all__ = [
    "IMMessage",
    "StreakChatInfo",
    "TikTokReadWriteClient",
    "normalize_cookie_jar",
]
