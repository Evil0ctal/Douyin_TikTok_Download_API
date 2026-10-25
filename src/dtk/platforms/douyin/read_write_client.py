"""Unified Pure-API Live-Streaming & Read/Write Client for Douyin Web.

Combines:
* :class:`~dtk.signing.native.abogus.ABogus` (Douyin ``bdms.js`` ``a_bogus`` SM3+RC4 signer)
* :class:`~dtk.signing.native.xbogus.XBogus` (``X-Bogus`` signer for live reflow & webcast endpoints)
* :mod:`dtk.signing.native.tokens` (``verifyFp`` / ``s_v_web_id`` / ``msToken`` generation)
* ``x-secsdk-csrf-token`` one-time CSRF handshake via ``HEAD`` for write endpoints
* Normalized FLV / HLS live stream extraction across both ``live.douyin.com`` and ``webcast.amemv.com``
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import parse_qs, quote, urlencode, urlparse

from dtk.platforms.douyin.endpoints import DouyinAPIEndpoints
from dtk.platforms.douyin.params import DEFAULT_PROFILE, base_params, live_room_params
from dtk.platforms.douyin.parser import parse_live_room
from dtk.platforms.tiktok.read_write_client import normalize_cookie_jar
from dtk.signing.native.abogus import DEFAULT_BROWSER_INFO, ABogus
from dtk.signing.native.tokens import (
    DOUYIN_MS_TOKEN_LENGTH,
    gen_false_ms_token,
    gen_verify_fp,
)
from dtk.signing.native.xbogus import DEFAULT_USER_AGENT, XBogus

DEFAULT_DOUYIN_UA: Final = DEFAULT_USER_AGENT

_URL_EXTRACT_RE: Final = re.compile(r"https?://[^\s\"'<>]+")
_LIVE_DOMAINS: Final = ("live.douyin.com", "webcast.amemv.com")


def extract_douyin_live_streams(room_node: Mapping[str, Any]) -> list[dict[str, str]]:
    """Extracts normalized FLV and HLS stream pull URLs from a Douyin ``room`` object."""
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    stream_url = room_node.get("stream_url")
    if not isinstance(stream_url, Mapping):
        return streams

    flv_map = stream_url.get("flv_pull_url")
    if isinstance(flv_map, Mapping):
        for quality, url in flv_map.items():
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                streams.append({"quality": str(quality), "format": "flv", "url": url})

    hls_map = stream_url.get("hls_pull_url_map")
    if isinstance(hls_map, Mapping):
        for quality, url in hls_map.items():
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                streams.append({"quality": str(quality), "format": "hls", "url": url})

    default_hls = stream_url.get("hls_pull_url")
    if isinstance(default_hls, str) and default_hls and default_hls not in seen:
        seen.add(default_hls)
        streams.append({"quality": "default", "format": "hls", "url": default_hls})

    return streams


class DouyinReadWriteClient:
    """High-level Live-Streaming and Read/Write client for Douyin Web."""

    def __init__(
        self,
        cookies: str | Mapping[str, str] | Sequence[Mapping[str, Any]] = "",
        *,
        user_agent: str = DEFAULT_DOUYIN_UA,
    ) -> None:
        self.cookie_map = normalize_cookie_jar(cookies) if cookies else {}
        self.user_agent = user_agent or DEFAULT_DOUYIN_UA
        self._ssl_ctx = ssl.create_default_context()
        self._abogus = ABogus(self.user_agent, browser_info=DEFAULT_BROWSER_INFO)
        self._xbogus = XBogus(self.user_agent)

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookie_map.items())

    @property
    def ms_token(self) -> str:
        return self.cookie_map.get("msToken") or gen_false_ms_token(DOUYIN_MS_TOKEN_LENGTH)

    @property
    def verify_fp(self) -> str:
        return (
            self.cookie_map.get("s_v_web_id") or self.cookie_map.get("verifyFp") or gen_verify_fp()
        )

    @staticmethod
    def _can_use_curl_cffi() -> bool:
        if getattr(urllib.request.urlopen, "__module__", "") != "urllib.request":
            return False
        try:
            import curl_cffi.requests  # noqa: F401

            return True
        except ImportError:
            return False

    def _base_web_params(
        self, extra: Mapping[str, str] | Sequence[tuple[str, str]] = ()
    ) -> dict[str, str]:
        params = base_params(DEFAULT_PROFILE)
        params["msToken"] = self.ms_token
        if isinstance(extra, Mapping):
            params.update({str(k): str(v) for k, v in extra.items()})
        else:
            for k, v in extra:
                params[str(k)] = str(v)
        return params

    def _fetch_secsdk_csrf_token(self, url: str, referer: str) -> str:
        hdrs: dict[str, str] = {
            "User-Agent": self.user_agent,
            "Referer": referer,
            "Origin": DouyinAPIEndpoints.DOUYIN_DOMAIN,
            "x-secsdk-csrf-request": "1",
            "x-secsdk-csrf-version": "1.2.22",
        }
        if self.cookie_header:
            hdrs["Cookie"] = self.cookie_header
        try:
            if self._can_use_curl_cffi():
                from curl_cffi import requests as creq

                resp = creq.head(url, headers=hdrs, impersonate="chrome", timeout=15)
                ware = resp.headers.get("x-ware-csrf-token") or ""
                if "," in ware:
                    return ware.split(",")[1]
                return ware
            req = urllib.request.Request(url, headers=hdrs, method="HEAD")
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
                ware = resp.headers.get("x-ware-csrf-token") or ""
                if "," in ware:
                    return ware.split(",")[1]
                return ware
        except Exception:
            return "DOWNGRADE"

    def _request_json(
        self,
        method: str,
        url_base: str,
        params: Mapping[str, str],
        *,
        body: bytes = b"",
        sign_mode: str = "abogus",
        referer: str = f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}/",
        origin: str = DouyinAPIEndpoints.DOUYIN_DOMAIN,
        use_secsdk_csrf: bool = False,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        query = urlencode(params)
        body_text = body.decode("utf-8", errors="replace") if body else ""
        content_type = (
            "application/x-www-form-urlencoded; charset=UTF-8" if method.upper() == "POST" else ""
        )

        if sign_mode == "abogus":
            ab = self._abogus.get_value(query, body=body_text, content_type=content_type)
            signed_query = f"{query}&a_bogus={quote(ab, safe='')}"
        elif sign_mode == "xbogus":
            xb = self._xbogus.sign(query)
            signed_query = f"{query}&X-Bogus={xb}"
        else:
            signed_query = query

        full_url = f"{url_base}?{signed_query}" if signed_query else url_base
        headers: dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": self.user_agent,
            "Referer": referer,
            "Origin": origin,
        }
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        if method.upper() == "POST":
            headers["Content-Type"] = content_type
        if use_secsdk_csrf:
            headers["x-secsdk-csrf-token"] = self._fetch_secsdk_csrf_token(url_base, referer)
        if extra_headers:
            headers.update(extra_headers)

        if self._can_use_curl_cffi():
            from curl_cffi import requests as creq

            resp = creq.request(
                method.upper(),
                full_url,
                headers=headers,
                data=body if method.upper() == "POST" else None,
                impersonate="chrome",
                timeout=15,
            )
            raw = resp.text
            return json.loads(raw) if raw.strip() else {}

        req = urllib.request.Request(
            full_url,
            data=body if method.upper() == "POST" else None,
            headers=headers,
            method=method.upper(),
        )
        with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

    # ------------------------------------------------------------------
    # Douyin Live-Streaming API
    # ------------------------------------------------------------------

    def get_live_room(self, web_rid: str, *, room_id_str: str = "") -> dict[str, Any]:
        """Fetches a Douyin live room by ``web_rid`` via ``live.douyin.com/webcast/room/web/enter/``.

        Returns the raw platform payload plus a normalized ``parsed_room`` object
        containing ``is_live``, ``room_id``, ``title``, ``viewer_count``, and FLV/HLS ``streams``.
        """
        rid = str(web_rid).strip()
        params = live_room_params(web_rid=rid, profile=DEFAULT_PROFILE)
        if room_id_str:
            params["room_id_str"] = str(room_id_str).strip()
        params["msToken"] = self.ms_token

        payload = self._request_json(
            "GET",
            DouyinAPIEndpoints.LIVE_INFO,
            params,
            sign_mode="abogus",
            referer=f"{DouyinAPIEndpoints.LIVE_DOMAIN}/",
            origin=DouyinAPIEndpoints.LIVE_DOMAIN,
        )
        try:
            room_model = parse_live_room(payload, room_id=rid)
            payload["parsed_room"] = room_model.model_dump(mode="json")
        except Exception:
            data = payload.get("data")
            if isinstance(data, Mapping):
                rooms = data.get("data")
                if isinstance(rooms, Sequence) and rooms and isinstance(rooms[0], Mapping):
                    r0 = rooms[0]
                    payload["parsed_room"] = {
                        "platform": "douyin",
                        "web_rid": rid,
                        "room_id": str(r0.get("id_str") or ""),
                        "is_live": r0.get("status") == 2,
                        "title": r0.get("title"),
                        "streams": extract_douyin_live_streams(r0),
                    }
        return payload

    def get_live_room_by_room_id(self, room_id: str, *, sec_user_id: str = "") -> dict[str, Any]:
        """Fetches a Douyin live room by internal ``room_id`` via ``webcast.amemv.com/webcast/room/reflow/info/``."""
        rid = str(room_id).strip()
        params = {
            "verifyFp": self.verify_fp,
            "type_id": "0",
            "live_id": "1",
            "room_id": rid,
            "sec_user_id": sec_user_id,
            "version_code": "99.99.99",
            "app_id": "1128",
            "msToken": self.ms_token,
        }
        payload = self._request_json(
            "GET",
            DouyinAPIEndpoints.LIVE_INFO_ROOM_ID,
            params,
            sign_mode="xbogus",
            referer=f"{DouyinAPIEndpoints.LIVE_DOMAIN}/",
            origin=DouyinAPIEndpoints.LIVE_DOMAIN,
        )
        data = payload.get("data")
        if isinstance(data, Mapping):
            room = data.get("room")
            if isinstance(room, Mapping):
                raw_owner = room.get("owner")
                owner: Mapping[str, Any] = raw_owner if isinstance(raw_owner, Mapping) else {}
                payload["parsed_room"] = {
                    "platform": "douyin",
                    "room_id": str(room.get("id_str") or rid),
                    "web_rid": str(owner.get("web_rid") or ""),
                    "is_live": room.get("status") == 2,
                    "title": room.get("title"),
                    "user_count": room.get("user_count"),
                    "like_count": room.get("like_count"),
                    "owner_nickname": owner.get("nickname"),
                    "owner_sec_uid": owner.get("sec_uid"),
                    "streams": extract_douyin_live_streams(room),
                }
        return payload

    def get_live_gift_ranking(self, room_id: str, *, rank_type: int = 30) -> dict[str, Any]:
        """Fetches the gift/audience ranking leaderboard of a Douyin live room."""
        params = self._base_web_params(
            {
                "webcast_sdk_version": "2450",
                "room_id": str(room_id).strip(),
                "rank_type": str(rank_type),
            }
        )
        return self._request_json(
            "GET",
            DouyinAPIEndpoints.LIVE_GIFT_RANK,
            params,
            sign_mode="xbogus",
            referer=f"{DouyinAPIEndpoints.LIVE_DOMAIN}/",
            origin=DouyinAPIEndpoints.LIVE_DOMAIN,
        )

    def get_followed_live_rooms(self, *, scene: str = "aweme_pc_follow_top") -> dict[str, Any]:
        """Fetches active live rooms of accounts the authenticated user follows."""
        params = self._base_web_params({"scene": scene})
        return self._request_json(
            "GET",
            DouyinAPIEndpoints.FOLLOW_USER_LIVE,
            params,
            sign_mode="abogus",
        )

    def get_live_user_info(self) -> dict[str, Any]:
        """Fetches the authenticated viewer's live webcast profile (``live.douyin.com/webcast/user/me/``)."""
        params = {
            "aid": "6383",
            "app_name": "douyin_web",
            "live_id": "1",
            "device_platform": "web",
            "language": "zh-CN",
            "msToken": self.ms_token,
        }
        return self._request_json(
            "GET",
            DouyinAPIEndpoints.LIVE_USER_INFO,
            params,
            sign_mode="abogus",
            referer=f"{DouyinAPIEndpoints.LIVE_DOMAIN}/",
            origin=DouyinAPIEndpoints.LIVE_DOMAIN,
        )

    def search_live_rooms(
        self, keyword: str, *, offset: int = 0, count: int = 20
    ) -> dict[str, Any]:
        """Searches Douyin live rooms by keyword via ``/aweme/v1/web/live/search/``."""
        params = self._base_web_params(
            {
                "keyword": keyword.strip(),
                "offset": str(offset),
                "count": str(count),
                "search_channel": "aweme_live",
                "search_source": "normal_search",
                "query_correct_type": "1",
                "is_filter_search": "0",
            }
        )
        return self._request_json(
            "GET",
            DouyinAPIEndpoints.LIVE_SEARCH,
            params,
            sign_mode="abogus",
        )

    def extract_webcast_id(
        self, url_or_text: str, *, resolve_short_links: bool = True
    ) -> dict[str, str | None]:
        """Extracts ``web_rid`` and/or ``room_id`` from a Douyin live URL or share text."""
        text = url_or_text.strip()
        if text.isdigit():
            return {"web_rid": text, "room_id": None, "resolved_url": None}

        match = _URL_EXTRACT_RE.search(text)
        target_url = match.group(0) if match else text
        parsed = urlparse(target_url)

        if resolve_short_links and parsed.netloc.endswith("v.douyin.com"):
            try:
                req = urllib.request.Request(
                    target_url,
                    headers={"User-Agent": self.user_agent},
                    method="GET",
                )
                with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=10) as resp:
                    target_url = resp.geturl()
                    parsed = urlparse(target_url)
            except Exception:
                pass

        qs = parse_qs(parsed.query)
        room_id_list = qs.get("room_id") or qs.get("roomId")
        room_id: str | None = room_id_list[0] if room_id_list else None
        web_rid: str | None = None

        parts = [p for p in parsed.path.split("/") if p]
        if parsed.netloc.endswith("live.douyin.com") and parts:
            candidate = parts[-1]
            if candidate.isdigit():
                web_rid = candidate
        elif "reflow" in parts:
            idx = parts.index("reflow")
            if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                room_id = room_id or parts[idx + 1]

        return {"web_rid": web_rid, "room_id": room_id, "resolved_url": target_url}

    # ------------------------------------------------------------------
    # Douyin Social & Content Read/Write API
    # ------------------------------------------------------------------

    def digg_video(self, aweme_id: str, *, like: bool = True) -> dict[str, Any]:
        """Likes (``type=1``) or unlikes (``type=0``) a Douyin post."""
        params = self._base_web_params()
        body = urlencode(
            {
                "aweme_id": str(aweme_id).strip(),
                "item_type": "0",
                "type": "1" if like else "0",
            }
        ).encode("utf-8")
        return self._request_json(
            "POST",
            f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}/aweme/v1/web/commit/item/digg/",
            params,
            body=body,
            sign_mode="abogus",
            use_secsdk_csrf=True,
        )

    def collect_video(self, aweme_id: str, *, collect: bool = True) -> dict[str, Any]:
        """Bookmarks (``action=1``) or unbookmarks (``action=0``) a Douyin post."""
        params = self._base_web_params()
        body = urlencode(
            {
                "aweme_id": str(aweme_id).strip(),
                "aweme_type": "0",
                "action": "1" if collect else "0",
            }
        ).encode("utf-8")
        return self._request_json(
            "POST",
            f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}/aweme/v1/web/aweme/collect/",
            params,
            body=body,
            sign_mode="abogus",
            use_secsdk_csrf=True,
        )

    def get_collected_videos(self, *, cursor: int = 0, count: int = 20) -> dict[str, Any]:
        """Fetches the authenticated user's bookmarked videos via ``POST /aweme/v1/web/aweme/listcollection/``."""
        params = self._base_web_params(
            {
                "publish_video_strategy_type": "2",
                "pc_libra_divert": "Windows",
            }
        )
        body = urlencode({"count": str(count), "cursor": str(cursor)}).encode("utf-8")
        return self._request_json(
            "POST",
            DouyinAPIEndpoints.USER_COLLECTION,
            params,
            body=body,
            sign_mode="abogus",
            referer=f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}/user/self?from_tab_name=main&showTab=favorite_collection",
            extra_headers={"x-secsdk-csrf-token": "DOWNGRADE"},
        )

    def get_liked_videos(
        self, sec_user_id: str, *, max_cursor: int = 0, count: int = 20
    ) -> dict[str, Any]:
        """Fetches an account's liked videos via ``GET /aweme/v1/web/aweme/favorite/``."""
        params = self._base_web_params(
            {
                "sec_user_id": sec_user_id.strip(),
                "max_cursor": str(max_cursor),
                "count": str(count),
                "publish_video_strategy_type": "2",
            }
        )
        return self._request_json(
            "GET",
            DouyinAPIEndpoints.USER_FAVORITE_A,
            params,
            sign_mode="abogus",
        )

    def publish_comment(self, aweme_id: str, text: str, *, reply_id: str = "") -> dict[str, Any]:
        """Publishes a comment (or reply) on a Douyin post via ``POST /aweme/v1/web/comment/publish``."""
        params = self._base_web_params()
        form_data: dict[str, str] = {
            "aweme_id": str(aweme_id).strip(),
            "comment_send_celltime": "1200",
            "comment_video_celltime": "3500",
            "one_level_comment_rank": "-1",
            "paste_edit_method": "non_paste",
            "text": text,
            "text_extra": "[]",
        }
        if reply_id:
            form_data["reply_id"] = str(reply_id).strip()
        body = urlencode(form_data).encode("utf-8")
        return self._request_json(
            "POST",
            DouyinAPIEndpoints.POST_COMMENT_PUBLISH,
            params,
            body=body,
            sign_mode="abogus",
            use_secsdk_csrf=True,
        )

    def delete_comment(self, cid: str, *, aweme_id: str = "") -> dict[str, Any]:
        """Deletes a comment on a Douyin post via ``POST /aweme/v1/web/comment/delete/``."""
        params = self._base_web_params()
        form_data: dict[str, str] = {"cid": str(cid).strip()}
        if aweme_id:
            form_data["item_id"] = str(aweme_id).strip()
        body = urlencode(form_data).encode("utf-8")
        return self._request_json(
            "POST",
            DouyinAPIEndpoints.POST_COMMENT_DELETE,
            params,
            body=body,
            sign_mode="abogus",
            use_secsdk_csrf=True,
        )

    def digg_comment(self, cid: str, aweme_id: str, *, like: bool = True) -> dict[str, Any]:
        """Likes (``digg_type=1``) or unlikes (``digg_type=2``) a comment on a Douyin post."""
        params = self._base_web_params()
        body = urlencode(
            {
                "aweme_id": str(aweme_id).strip(),
                "cid": str(cid).strip(),
                "digg_type": "1" if like else "2",
            }
        ).encode("utf-8")
        return self._request_json(
            "POST",
            DouyinAPIEndpoints.POST_COMMENT_DIGG,
            params,
            body=body,
            sign_mode="abogus",
            use_secsdk_csrf=True,
        )

    def follow_user(self, user_id: str, sec_user_id: str, *, follow: bool = True) -> dict[str, Any]:
        """Follows (``type=1``) or unfollows (``type=0``) a Douyin user."""
        params = self._base_web_params()
        body = urlencode(
            {
                "user_id": str(user_id).strip(),
                "sec_user_id": str(sec_user_id).strip(),
                "type": "1" if follow else "0",
                "channel_id": "3",
                "from": "19",
                "from_pre": "13",
            }
        ).encode("utf-8")
        return self._request_json(
            "POST",
            f"{DouyinAPIEndpoints.DOUYIN_DOMAIN}/aweme/v1/web/commit/follow/user/",
            params,
            body=body,
            sign_mode="abogus",
            use_secsdk_csrf=True,
        )


__all__ = [
    "DEFAULT_DOUYIN_UA",
    "DouyinAPIEndpoints",
    "DouyinReadWriteClient",
    "extract_douyin_live_streams",
]
