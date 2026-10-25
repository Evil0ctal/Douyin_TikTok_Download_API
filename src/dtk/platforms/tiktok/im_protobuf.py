"""TikTok Direct Messages & Streaks Protobuf IM v1/v2 Read/Write Client.

Implements ByteDance's binary Protobuf IM protocol over ``im-api-sg.tiktok.com``
and ``im-api-va.tiktok.com`` as well as the ``/api/im/spotlight/relation/``
streak relation decoder, signed natively with :mod:`dtk.signing.native.tiktok_sign`.

Supported commands:
* ``cmd=203`` (``/v2/message/get_by_user_init``): list conversations, short IDs & recent messages
* ``cmd=301`` (``/v1/message/get_by_conversation``): read conversation message history
* ``cmd=609`` (``/v2/conversation/create``): resolve or create a 1:1 conversation short ID
* ``cmd=100`` (``/v1/message/send``): send text messages (``msg_type=7``) or native shared video cards (``msg_type=8``, ``aweType=800``)
* ``cmd=705`` (``/v1/message/set_property``): add or remove emoji reactions (e.g. ``e:❤️``) on a message
* ``cmd=701`` (``/v1/message/delete``): delete a message from a conversation
* ``cmd=603`` (``/v2/conversation/delete``): delete an entire conversation
* ``cmd=1005`` (``/v1/stranger/delete_all_conversations``): clear message request / stranger inbox
"""

from __future__ import annotations

import base64
import contextlib
import json
import ssl
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Final

from dtk.signing.native import tiktok_sign

IM_BASE_URLS: Final[tuple[str, ...]] = (
    "https://im-api-sg.tiktok.com",
    "https://im-api-va.tiktok.com",
)
SDK_VERSION: Final = "1.8.3"
BUILD_NUMBER: Final = "0120411:master"
DEFAULT_DEVICE_ID: Final = "7700208528229615105"
DEFAULT_WEB_UA: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Zero-Dependency Protobuf Wire Format Encoder & Decoder
# ---------------------------------------------------------------------------


def encode_varint(val: int) -> bytes:
    if val < 0:
        val += 1 << 64
    out = bytearray()
    while True:
        byte = val & 0x7F
        val >>= 7
        if val:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def encode_tag(field_num: int, wire_type: int) -> bytes:
    return encode_varint((field_num << 3) | wire_type)


def encode_uint(field_num: int, val: int) -> bytes:
    return encode_tag(field_num, 0) + encode_varint(val)


def encode_bytes(field_num: int, data: bytes) -> bytes:
    return encode_tag(field_num, 2) + encode_varint(len(data)) + data


def encode_str(field_num: int, text: str) -> bytes:
    return encode_bytes(field_num, text.encode("utf-8"))


def encode_kv(field_num: int, key: str, value: str) -> bytes:
    inner = encode_str(1, key) + encode_str(2, value)
    return encode_bytes(field_num, inner)


def _decode_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            raise ValueError("Varint overflow")
    raise ValueError("Unexpected EOF in varint")


def parse_raw_fields(buf: bytes) -> list[tuple[int, int, Any]]:
    """Decodes raw Protobuf wire fields into ``(field_number, wire_type, value)``."""
    pos = 0
    out: list[tuple[int, int, Any]] = []
    while pos < len(buf):
        tag, pos = _decode_varint(buf, pos)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if field_num <= 0:
            break
        if wire_type == 0:
            val, pos = _decode_varint(buf, pos)
            out.append((field_num, wire_type, val))
        elif wire_type == 1:
            if pos + 8 > len(buf):
                break
            out.append((field_num, wire_type, buf[pos : pos + 8]))
            pos += 8
        elif wire_type == 2:
            length, pos = _decode_varint(buf, pos)
            if length < 0 or pos + length > len(buf):
                break
            out.append((field_num, wire_type, buf[pos : pos + length]))
            pos += length
        elif wire_type == 5:
            if pos + 4 > len(buf):
                break
            out.append((field_num, wire_type, buf[pos : pos + 4]))
            pos += 4
        else:
            break
    return out


# ---------------------------------------------------------------------------
# Streak & IM Models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StreakChatInfo:
    """Represents an active TikTok DM Streak (🔥) decoded from ``spotlight/relation``."""

    conversation_id: str
    conversation_type: int
    streak_days: int
    username: str = ""
    nickname: str = ""
    peer_uid: int = 0
    sec_uid: str = ""
    start_ts: int = 0
    last_ts: int = 0
    peer_last_ts: int = 0
    my_last_ts: int = 0
    expire_ts: int = 0
    streak_state: int = 1

    def did_i_contribute_today(self) -> bool:
        if not self.my_last_ts:
            return False
        now_local = datetime.now().astimezone()
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.my_last_ts >= int(today_start.timestamp())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["contributed_today"] = self.did_i_contribute_today()
        return d


@dataclass(slots=True)
class IMMessage:
    """Represents a decoded TikTok Direct Message from Protobuf ``MessageBody``."""

    conversation_id: str
    conversation_type: int
    conversation_short_id: int
    server_message_id: int
    index_in_conversation: int
    message_type: int
    sender: int
    create_time_ms: int
    content: str
    client_message_id: str
    liked_by_uids: set[int] = field(default_factory=set)

    @property
    def is_system_message(self) -> bool:
        return self.message_type in (1, 1001, 1002, 70002)

    @property
    def shared_video_id(self) -> str:
        if not self.content:
            return ""
        try:
            obj = json.loads(self.content)
            if isinstance(obj, dict) and (obj.get("aweType") == 800 or self.message_type == 8):
                return str(obj.get("itemId") or "")
        except Exception:
            pass
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "conversation_type": self.conversation_type,
            "conversation_short_id": self.conversation_short_id,
            "server_message_id": self.server_message_id,
            "index_in_conversation": self.index_in_conversation,
            "message_type": self.message_type,
            "sender": self.sender,
            "create_time_ms": self.create_time_ms,
            "content": self.content,
            "client_message_id": self.client_message_id,
            "liked_by_uids": sorted(self.liked_by_uids),
            "shared_video_id": self.shared_video_id,
            "is_system_message": self.is_system_message,
        }


def _decode_streak_biz_ext(b64_str: str, my_uid: int = 0) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(b64_str)
        top_fields = parse_raw_fields(raw)
    except Exception:
        return None

    for fn, wt, val in top_fields:
        if fn == 2 and wt == 2 and isinstance(val, bytes):
            try:
                inner = parse_raw_fields(val)
            except Exception:
                continue
            fmap: dict[int, list[Any]] = {}
            for ifn, _iwt, ival in inner:
                fmap.setdefault(ifn, []).append(ival)

            streak_days = int(fmap.get(1, [0])[0])
            if streak_days <= 0:
                return None
            conv_id_bytes = fmap.get(5, [b""])[0]
            conv_id = (
                conv_id_bytes.decode("utf-8", errors="ignore")
                if isinstance(conv_id_bytes, bytes)
                else ""
            )
            start_ts = int(fmap.get(2, [0])[0])
            last_ts = int(fmap.get(3, [0])[0])
            expire_ts = int(fmap.get(9, [0])[0])
            streak_state = int(fmap.get(10, [1])[0])

            peer_last_ts = 0
            my_last_ts = 0
            for entry_bytes in fmap.get(8, []):
                if not isinstance(entry_bytes, bytes):
                    continue
                try:
                    ef = {k: v for k, _, v in parse_raw_fields(entry_bytes)}
                    uid_val = int(ef.get(1, 0))
                    ts_val = int(ef.get(2, 0))
                    if my_uid and uid_val == my_uid:
                        my_last_ts = ts_val
                    else:
                        peer_last_ts = ts_val
                except Exception:
                    pass

            return {
                "streak_days": streak_days,
                "conv_id": conv_id,
                "start_ts": start_ts,
                "last_ts": last_ts,
                "peer_last_ts": peer_last_ts,
                "my_last_ts": my_last_ts,
                "expire_ts": expire_ts,
                "streak_state": streak_state,
            }
    return None


def parse_spotlight_streaks(payload: Mapping[str, Any], *, my_uid: int = 0) -> list[StreakChatInfo]:
    """Extracts active streaks (🔥) from the JSON response of ``/api/im/spotlight/relation/``."""
    infos = payload.get("check_infos")
    if not isinstance(infos, list):
        return []

    streaks: list[StreakChatInfo] = []
    for item in infos:
        if not isinstance(item, Mapping):
            continue
        biz_ext = item.get("biz_ext")
        if not isinstance(biz_ext, str) or not biz_ext:
            continue
        decoded = _decode_streak_biz_ext(biz_ext, my_uid=my_uid)
        if not decoded or decoded["streak_days"] <= 0:
            continue

        conv_id = str(item.get("conv_id") or decoded.get("conv_id") or "")
        peer_uid = 0
        if ":" in conv_id:
            for part in conv_id.split(":")[2:]:
                if part.isdigit() and int(part) != my_uid:
                    peer_uid = int(part)
                    break

        streaks.append(
            StreakChatInfo(
                conversation_id=conv_id,
                conversation_type=int(item.get("conv_type") or 1),
                streak_days=decoded["streak_days"],
                username=str(item.get(" unique_id") or item.get("unique_id") or ""),
                nickname=str(item.get("nick_name") or ""),
                peer_uid=peer_uid,
                sec_uid=str(item.get("sec_uid") or ""),
                start_ts=decoded["start_ts"],
                last_ts=decoded["last_ts"],
                peer_last_ts=decoded["peer_last_ts"],
                my_last_ts=decoded["my_last_ts"],
                expire_ts=decoded["expire_ts"],
                streak_state=decoded["streak_state"],
            )
        )
    streaks.sort(key=lambda s: s.streak_days, reverse=True)
    return streaks


def _parse_reaction_uids(prop_item_bytes: bytes) -> tuple[str, set[int]]:
    prop_key = ""
    uids: set[int] = set()
    for fn, wt, val in parse_raw_fields(prop_item_bytes):
        if fn == 1 and wt == 2:
            prop_key = val.decode("utf-8", errors="replace")
        elif fn == 2 and wt == 2:
            for fn2, wt2, val2 in parse_raw_fields(val):
                if fn2 == 1 and wt2 == 2:
                    for fn3, wt3, val3 in parse_raw_fields(val2):
                        if fn3 == 1 and wt3 == 0:
                            uids.add(int(val3))
                        elif fn3 == 4 and wt3 == 2:
                            with contextlib.suppress(Exception):
                                uids.add(int(val3.decode("utf-8").strip()))
    return prop_key, uids


def parse_message_body(data: bytes) -> IMMessage | None:
    conv_id = ""
    conv_type = 1
    server_msg_id = 0
    idx_in_conv = 0
    conv_short_id = 0
    msg_type = 0
    sender = 0
    content = ""
    client_msg_id = ""
    create_time_ms = 0
    liked_uids: set[int] = set()

    try:
        fields = parse_raw_fields(data)
    except Exception:
        return None

    for fn, wt, val in fields:
        if fn == 1 and wt == 2:
            conv_id = val.decode("utf-8", errors="replace")
        elif fn == 2 and wt == 0:
            conv_type = int(val)
        elif fn == 3 and wt == 0:
            server_msg_id = int(val)
        elif fn == 4 and wt == 0:
            idx_in_conv = int(val)
        elif fn == 5 and wt == 0:
            conv_short_id = int(val)
        elif fn == 6 and wt == 0:
            msg_type = int(val)
        elif fn == 7 and wt == 0:
            sender = int(val)
        elif fn == 8 and wt == 2:
            try:
                content = val.decode("utf-8")
            except Exception:
                content = ""
        elif fn == 9 and wt == 2:
            try:
                k, v = "", ""
                for efn, ewt, eval_ in parse_raw_fields(val):
                    if efn == 1 and ewt == 2:
                        k = eval_.decode("utf-8", errors="replace")
                    elif efn == 2 and ewt == 2:
                        v = eval_.decode("utf-8", errors="replace")
                if k == "s:client_message_id" and v:
                    client_msg_id = v
            except Exception:
                pass
        elif fn == 10 and wt == 0:
            create_time_ms = int(val)
        elif fn == 15 and wt == 2:
            try:
                pkey, uids = _parse_reaction_uids(val)
                if "❤️" in pkey or "❤" in pkey or pkey in ("e:love", "e:heart"):
                    liked_uids.update(uids)
            except Exception:
                pass

    if not conv_id or not server_msg_id:
        return None

    return IMMessage(
        conversation_id=conv_id,
        conversation_type=conv_type,
        conversation_short_id=conv_short_id,
        server_message_id=server_msg_id,
        index_in_conversation=idx_in_conv,
        message_type=msg_type,
        sender=sender,
        create_time_ms=create_time_ms,
        content=content,
        client_message_id=client_msg_id,
        liked_by_uids=liked_uids,
    )


# ---------------------------------------------------------------------------
# TikTok Protobuf IM v1/v2 Client
# ---------------------------------------------------------------------------


class TikTokIMClient:
    """Pure-API Protobuf client for TikTok Direct Messages and Streaks."""

    def __init__(
        self,
        cookies: Mapping[str, str],
        *,
        user_agent: str = DEFAULT_WEB_UA,
        device_id: str = DEFAULT_DEVICE_ID,
    ) -> None:
        self.cookie_map: dict[str, str] = {str(k): str(v) for k, v in cookies.items()}
        self.cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookie_map.items())
        self.user_agent = user_agent
        self.ms_token = self.cookie_map.get("msToken", "")
        self.verify_fp = self.cookie_map.get("s_v_web_id", "")
        self.region = (self.cookie_map.get("store-country-code") or "US").upper()
        self.device_id = device_id
        self.seq_id = 10001
        self.sign_seq = 1
        self.my_uid: int = self._extract_uid_from_cookies()

        self.conv_short_ids: dict[str, int] = {}
        self.init_messages_by_conv: dict[str, list[IMMessage]] = {}
        self._ssl_ctx = ssl.create_default_context()

    def _extract_uid_from_cookies(self) -> int:
        multi = self.cookie_map.get("multi_sids", "")
        if "%3A" in multi or ":" in multi:
            raw = multi.replace("%3A", ":").split(":")[0]
            if raw.isdigit():
                return int(raw)
        return 0

    def _next_seq_id(self) -> int:
        val = self.seq_id
        self.seq_id += 1
        return val

    def _next_sign_seq(self) -> int:
        val = self.sign_seq
        self.sign_seq += 1
        return val

    def _build_envelope(self, cmd: int, body_inner: bytes, inbox_type: int = 0) -> bytes:
        seq = self._next_seq_id()
        body_wrapped = encode_bytes(cmd, body_inner)
        ua_ver = self.user_agent.removeprefix("Mozilla/")
        header_pairs = [
            ("aid", "1988"),
            ("app_name", "tiktok_web"),
            ("channel", "web"),
            ("device_platform", "web_pc"),
            ("device_id", self.device_id),
            ("region", self.region),
            ("priority_region", self.region),
            ("os", "windows"),
            ("referer", "https://www.tiktok.com/messages"),
            ("root_referer", ""),
            ("cookie_enabled", "true"),
            ("screen_width", "1920"),
            ("screen_height", "1080"),
            ("browser_language", "en-US"),
            ("browser_platform", "Win32"),
            ("browser_name", "Mozilla"),
            ("browser_version", ua_ver),
            ("browser_online", "true"),
            ("verifyFp", self.verify_fp),
            ("app_language", "en"),
            ("webcast_language", "en"),
            ("tz_name", "UTC"),
            ("is_page_visible", "true"),
            ("focus_state", "true"),
            ("is_fullscreen", "false"),
            ("history_len", "2"),
            ("user_is_login", "true"),
            ("data_collection_enabled", "true"),
            ("from_appID", "1988"),
            ("locale", "en"),
            ("user_agent", self.user_agent),
            ("Web-Sdk-Ms-Token", self.ms_token),
        ]
        parts = [
            encode_uint(1, cmd),
            encode_uint(2, seq),
            encode_str(3, SDK_VERSION),
            encode_str(4, ""),
            encode_uint(5, 3),
            encode_uint(6, inbox_type),
            encode_str(7, BUILD_NUMBER),
            encode_bytes(8, body_wrapped),
            encode_str(9, self.device_id),
            encode_str(11, "web"),
        ]
        for hk, hv in header_pairs:
            parts.append(encode_kv(15, hk, hv))
        parts.append(encode_uint(18, 1))
        return b"".join(parts)

    def _post_im(
        self,
        path: str,
        pb_body: bytes,
        *,
        sign_request: bool = False,
        query_pairs: Sequence[tuple[str, str]] = (),
    ) -> bytes:
        query_str = ""
        if sign_request:
            query_str, _ = tiktok_sign.sign(
                query_pairs,
                self.user_agent,
                ms_token=self.ms_token,
                body=pb_body,
                sequence=self._next_sign_seq(),
            )
        elif query_pairs:
            query_str = "&".join(f"{k}={v}" for k, v in query_pairs)

        headers = {
            "Accept": "application/x-protobuf",
            "Content-Type": "application/x-protobuf",
            "Cookie": self.cookie_header,
            "Origin": "https://www.tiktok.com",
            "Referer": "https://www.tiktok.com/",
            "User-Agent": self.user_agent,
        }
        last_err: Exception | None = None
        for base in IM_BASE_URLS:
            url = f"{base}{path}" + (f"?{query_str}" if query_str else "")
            req = urllib.request.Request(url, data=pb_body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
                    return resp.read()
            except Exception as exc:
                last_err = exc
                continue
        raise RuntimeError(f"IM POST {path} failed: {last_err}")

    @staticmethod
    def _extract_response_body(resp_bytes: bytes, cmd: int) -> tuple[int, str, bytes]:
        status_code = -1
        desc = ""
        cmd_payload = b""
        for fn, wt, val in parse_raw_fields(resp_bytes):
            if fn == 3 and wt == 0:
                status_code = int(val)
            elif fn == 4 and wt == 2:
                desc = val.decode("utf-8", errors="replace")
            elif fn == 6 and wt == 2:
                for bfn, bwt, bval in parse_raw_fields(val):
                    if bfn == cmd and bwt == 2:
                        cmd_payload = bval
        return status_code, desc, cmd_payload

    def get_active_streaks(self) -> list[StreakChatInfo]:
        """Fetches active DM streaks (🔥) via ``/tiktok/v1/im/streaks/get`` (Protobuf) with fallback to ``/api/im/spotlight/relation/``."""
        streak_endpoints = (
            "https://api16-normal-c-alisg.tiktokv.com/tiktok/v1/im/streaks/get",
            "https://api-va.tiktokv.com/tiktok/v1/im/streaks/get",
            "https://api16-normal-c-useast1a.tiktokv.com/tiktok/v1/im/streaks/get",
        )
        profile_endpoints = (
            "https://www.tiktok.com/tiktok/v1/im/user/profile/",
            "https://api16-normal-c-alisg.tiktokv.com/tiktok/v1/im/user/profile/",
        )
        mobile_ua = (
            "com.ss.android.ugc.trill/470104 (Linux; U; Android 11; en_US; LMG710ULM; "
            "Build/RKQ1.210420.001; Cronet/TTNetVersion:7a79f1fa 2024-08-01 QuicVersion:f2537604 2024-04-10)"
        )
        pb_streaks: list[StreakChatInfo] = []
        for ep in streak_endpoints:
            req = urllib.request.Request(
                ep,
                headers={
                    "User-Agent": mobile_ua,
                    "Accept": "application/x-protobuf",
                    "Cookie": self.cookie_header,
                },
            )
            try:
                with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=12.0) as resp:
                    if resp.status == 200:
                        body = resp.read()
                        top_fields = parse_raw_fields(body)
                        if any(fn == 1 and wt == 0 for fn, wt, _ in top_fields):
                            continue
                        for fn, wt, val in top_fields:
                            if fn == 1 and wt == 2 and isinstance(val, bytes):
                                rec_fields = parse_raw_fields(val)
                                s_days = 0
                                conv_id = ""
                                conv_type = 1
                                end_at = 0
                                my_last_ts = 0
                                peer_last_ts = 0
                                peer_uid = 0
                                for rfn, rwt, rval in rec_fields:
                                    if rfn == 1 and rwt == 2 and isinstance(rval, bytes):
                                        uf = {k: v for k, _, v in parse_raw_fields(rval)}
                                        u_id = int(uf.get(1, 0))
                                        u_ts = int(uf.get(2, 0))
                                        if self.my_uid and u_id == self.my_uid:
                                            my_last_ts = u_ts
                                        elif u_id:
                                            peer_uid = u_id
                                            peer_last_ts = u_ts
                                    elif rfn == 3 and rwt == 0:
                                        s_days = int(rval)
                                    elif rfn == 12 and rwt == 0:
                                        end_at = int(rval)
                                    elif rfn == 100 and rwt == 0:
                                        conv_type = int(rval)
                                    elif rfn == 101 and rwt == 2 and isinstance(rval, bytes):
                                        conv_id = rval.decode("utf-8", errors="replace")
                                if s_days > 0 and conv_id:
                                    if not peer_uid and conv_id.startswith("0:1:"):
                                        parts = conv_id.split(":")
                                        if len(parts) >= 4:
                                            u1, u2 = int(parts[2]), int(parts[3])
                                            peer_uid = u1 if u2 == self.my_uid else u2
                                    pb_streaks.append(
                                        StreakChatInfo(
                                            conversation_id=conv_id,
                                            conversation_type=conv_type,
                                            streak_days=s_days,
                                            peer_uid=peer_uid,
                                            my_last_ts=my_last_ts,
                                            peer_last_ts=peer_last_ts,
                                            last_ts=max(my_last_ts, peer_last_ts),
                                            expire_ts=end_at,
                                        )
                                    )
                        if pb_streaks:
                            break
            except Exception:
                continue

        if pb_streaks:
            uids = sorted({s.peer_uid for s in pb_streaks if s.peer_uid})
            if uids:
                encoded = urllib.parse.quote(json.dumps(uids))
                for prof_ep in profile_endpoints:
                    req = urllib.request.Request(
                        f"{prof_ep}?aid=1988&user_ids={encoded}",
                        headers={
                            "User-Agent": mobile_ua,
                            "Accept": "application/json",
                            "Cookie": self.cookie_header,
                            "Referer": "https://www.tiktok.com/messages",
                        },
                    )
                    try:
                        with urllib.request.urlopen(
                            req, context=self._ssl_ctx, timeout=12.0
                        ) as resp:
                            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                            uid_map: dict[int, tuple[str, str]] = {}
                            for u in payload.get("users") or []:
                                prof = u.get("im_user_profile") or {}
                                raw_uid = prof.get("user_id") or prof.get("user_id_str")
                                if raw_uid is not None:
                                    uid_map[int(raw_uid)] = (
                                        str(prof.get("unique_id") or "").strip(),
                                        str(prof.get("nick_name") or "").strip(),
                                    )
                            if uid_map:
                                for s in pb_streaks:
                                    if s.peer_uid in uid_map:
                                        s.username, s.nickname = uid_map[s.peer_uid]
                                break
                    except Exception:
                        continue
            pb_streaks.sort(key=lambda s: s.streak_days, reverse=True)
            return pb_streaks

        pairs = [
            ("WebIdLastTime", str(int(time.time()) - 86400)),
            ("aid", "1988"),
            ("app_language", "ru-RU"),
            ("app_name", "tiktok_web"),
            ("browser_language", "ru-RU"),
            ("browser_name", "Mozilla"),
            ("browser_online", "true"),
            ("browser_platform", "Win32"),
            ("browser_version", "5.0 (Windows)"),
            ("channel", "tiktok_web"),
            ("cookie_enabled", "true"),
            ("count", "500"),
            ("device_id", self.device_id),
            ("device_platform", "web_pc"),
            ("focus_state", "true"),
            ("from_page", "message"),
            ("history_len", "2"),
            ("is_fullscreen", "false"),
            ("is_page_visible", "true"),
            ("max_time", "0"),
            ("min_time", "0"),
            ("need_blocks", "false"),
            ("os", "windows"),
            ("priority_region", self.region),
            ("referer", ""),
            ("region", self.region),
            ("screen_height", "1080"),
            ("screen_width", "1920"),
            ("tz_name", "UTC"),
            ("user_is_login", "true"),
            ("verifyFp", self.verify_fp),
            ("webcast_language", "ru-RU"),
        ]
        q_str, _ = tiktok_sign.sign(
            pairs,
            self.user_agent,
            ms_token=self.ms_token,
            sequence=self._next_sign_seq(),
        )
        req = urllib.request.Request(
            f"https://www.tiktok.com/api/im/spotlight/relation/?{q_str}",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Cookie": self.cookie_header,
                "Referer": "https://www.tiktok.com/messages",
                "User-Agent": self.user_agent,
            },
            method="GET",
        )
        with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        return parse_spotlight_streaks(payload, my_uid=self.my_uid)

    def init_user_messages(self, max_pages: int = 2) -> dict[str, list[IMMessage]]:
        """Calls ``/v2/message/get_by_user_init`` (``cmd=203``) to populate conversations and recent messages."""
        cursor = 0
        for _ in range(max_pages):
            inner = encode_uint(1, cursor)
            pb_req = self._build_envelope(203, inner, inbox_type=0)
            resp_bytes = self._post_im("/v2/message/get_by_user_init", pb_req, sign_request=False)

            for fn, wt, val in parse_raw_fields(resp_bytes):
                if fn == 15 and wt == 0 and not self.my_uid:
                    self.my_uid = int(val)

            code, _desc, cmd_body = self._extract_response_body(resp_bytes, 203)
            if code != 0 or not cmd_body:
                break

            next_cursor = 0
            has_more = False
            for fn, wt, val in parse_raw_fields(cmd_body):
                if fn == 1 and wt == 2:
                    msg = parse_message_body(val)
                    if msg:
                        self._register_parsed_message(msg)
                    else:
                        for _sfn, swt, sval in parse_raw_fields(val):
                            if swt == 2:
                                sub_msg = parse_message_body(sval)
                                if sub_msg:
                                    self._register_parsed_message(sub_msg)
                elif fn == 3 and wt == 0:
                    next_cursor = int(val)
                elif fn == 4 and wt == 0:
                    has_more = bool(val)

            if not has_more or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return self.init_messages_by_conv

    def _register_parsed_message(self, msg: IMMessage) -> None:
        if msg.conversation_short_id > 0:
            self.conv_short_ids[msg.conversation_id] = msg.conversation_short_id
        lst = self.init_messages_by_conv.setdefault(msg.conversation_id, [])
        if all(existing.server_message_id != msg.server_message_id for existing in lst):
            lst.append(msg)

    def ensure_conversation_short_id(self, conv_id: str, peer_uid: int | None = None) -> int:
        if conv_id in self.conv_short_ids and self.conv_short_ids[conv_id] > 0:
            return self.conv_short_ids[conv_id]

        if not peer_uid and ":" in conv_id:
            for p in conv_id.split(":")[2:]:
                if p.isdigit() and int(p) != self.my_uid:
                    peer_uid = int(p)
                    break

        if not peer_uid or not self.my_uid:
            return 0

        inner = encode_uint(1, 1) + encode_uint(2, peer_uid) + encode_uint(2, self.my_uid)
        pb_req = self._build_envelope(609, inner, inbox_type=0)
        resp_bytes = self._post_im("/v2/conversation/create", pb_req, sign_request=True)
        code, _desc, cmd_body = self._extract_response_body(resp_bytes, 609)
        if code == 0 and cmd_body:
            for fn, wt, val in parse_raw_fields(cmd_body):
                if fn == 1 and wt == 2:
                    for cfn, cwt, cval in parse_raw_fields(val):
                        if cfn == 2 and cwt == 0:
                            short_id = int(cval)
                            self.conv_short_ids[conv_id] = short_id
                            return short_id
        return 0

    def get_conversation_messages(
        self,
        conv_id: str,
        *,
        peer_uid: int | None = None,
        limit: int = 20,
    ) -> list[IMMessage]:
        short_id = self.ensure_conversation_short_id(conv_id, peer_uid=peer_uid)
        collected: dict[int, IMMessage] = {
            m.server_message_id: m for m in self.init_messages_by_conv.get(conv_id, [])
        }
        if short_id > 0:
            cursor_idx = max((m.index_in_conversation for m in collected.values()), default=0)
            inner_parts = [
                encode_str(1, conv_id),
                encode_uint(2, 1),
                encode_uint(3, short_id),
                encode_uint(4, 1),
            ]
            if cursor_idx > 0:
                inner_parts.append(encode_uint(5, cursor_idx))
            inner_parts.append(encode_uint(6, limit))

            pb_req = self._build_envelope(301, b"".join(inner_parts), inbox_type=0)
            resp_bytes = self._post_im(
                "/v1/message/get_by_conversation", pb_req, sign_request=False
            )
            code, _desc, cmd_body = self._extract_response_body(resp_bytes, 301)
            if code == 0 and cmd_body:
                for fn, wt, val in parse_raw_fields(cmd_body):
                    if fn == 1 and wt == 2:
                        msg = parse_message_body(val)
                        if msg:
                            collected[msg.server_message_id] = msg

        return sorted(collected.values(), key=lambda x: (x.create_time_ms, x.index_in_conversation))

    def react_message(
        self,
        *,
        conversation_id: str,
        conversation_short_id: int,
        server_message_id: int,
        emoji: str = "❤️",
        remove: bool = False,
        conversation_type: int = 1,
        client_message_id: str = "",
    ) -> bool:
        """Adds or removes an emoji reaction on a message via ``/v1/message/set_property`` (``cmd=705``)."""
        if not conversation_short_id or not server_message_id or not self.my_uid:
            return False

        prop_key = emoji if emoji.startswith("e:") else f"e:{emoji}"
        client_msg_id = client_message_id or str(uuid.uuid4()).upper()
        prop_content = (
            encode_uint(1, 1 if remove else 0)
            + encode_str(2, prop_key)
            + encode_str(4, str(self.my_uid))
        )
        prop_body = (
            encode_str(1, conversation_id)
            + encode_uint(2, conversation_type)
            + encode_uint(3, conversation_short_id)
            + encode_uint(4, server_message_id)
            + encode_str(5, client_msg_id)
            + encode_bytes(6, prop_content)
        )
        cmd_705_inner = encode_bytes(1, prop_body) + encode_str(2, "deprecated")
        pb_req = self._build_envelope(705, cmd_705_inner, inbox_type=0)
        resp_bytes = self._post_im("/v1/message/set_property", pb_req, sign_request=True)
        code, _desc, _cmd_body = self._extract_response_body(resp_bytes, 705)
        return code == 0

    def send_text_message(
        self,
        conv_id: str,
        text: str,
        *,
        peer_uid: int | None = None,
    ) -> int:
        """Sends a text message (``msg_type=7``) via ``/v1/message/send`` (``cmd=100``). Returns ``server_message_id``."""
        payload_json = json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":"))
        return self._send_raw_message(conv_id, payload_json, msg_type=7, peer_uid=peer_uid)

    def send_video_card(
        self,
        conv_id: str,
        card_json: str | Mapping[str, Any],
        *,
        peer_uid: int | None = None,
    ) -> int:
        """Sends a native TikTok video card (``msg_type=8``, ``aweType=800``) via ``/v1/message/send`` (``cmd=100``)."""
        payload_str = (
            json.dumps(card_json, ensure_ascii=False, separators=(",", ":"))
            if isinstance(card_json, Mapping)
            else str(card_json)
        )
        return self._send_raw_message(conv_id, payload_str, msg_type=8, peer_uid=peer_uid)

    def _send_raw_message(
        self,
        conv_id: str,
        payload_json: str,
        *,
        msg_type: int,
        peer_uid: int | None = None,
    ) -> int:
        short_id = self.ensure_conversation_short_id(conv_id, peer_uid=peer_uid)
        if not short_id:
            return 0

        client_uuid = str(uuid.uuid4())
        cmd_100_inner = (
            encode_str(1, conv_id)
            + encode_uint(2, 1)
            + encode_uint(3, short_id)
            + encode_str(4, payload_json)
            + encode_kv(5, "s:mentioned_users", "")
            + encode_kv(5, "s:client_message_id", client_uuid)
            + encode_uint(6, msg_type)
            + encode_str(7, "deprecated")
            + encode_str(8, client_uuid)
        )
        pb_req = self._build_envelope(100, cmd_100_inner, inbox_type=0)
        query_pairs = [
            ("aid", "1988"),
            ("version_code", "1.0.0"),
            ("app_name", "tiktok_web"),
            ("device_platform", "web_pc"),
        ]
        resp_bytes = self._post_im(
            "/v1/message/send", pb_req, sign_request=True, query_pairs=query_pairs
        )
        code, _desc, cmd_body = self._extract_response_body(resp_bytes, 100)
        if code == 0 and cmd_body:
            for fn, wt, val in parse_raw_fields(cmd_body):
                if fn == 1 and wt == 0:
                    return int(val)
            return 1
        return 0

    def delete_message(
        self,
        *,
        conversation_id: str,
        conversation_short_id: int,
        server_message_id: int,
        conversation_type: int = 1,
    ) -> bool:
        """Deletes a single message via ``/v1/message/delete`` (``cmd=701``)."""
        inner = (
            encode_str(1, conversation_id)
            + encode_uint(2, conversation_short_id)
            + encode_uint(3, conversation_type)
            + encode_uint(4, server_message_id)
        )
        pb_req = self._build_envelope(701, inner, inbox_type=0)
        resp_bytes = self._post_im("/v1/message/delete", pb_req, sign_request=True)
        code, _desc, _cmd_body = self._extract_response_body(resp_bytes, 701)
        return code == 0

    def delete_conversation(
        self,
        *,
        conversation_id: str,
        conversation_short_id: int,
        conversation_type: int = 1,
        last_message_index: int = 0,
    ) -> bool:
        """Deletes an entire conversation via ``/v2/conversation/delete`` (``cmd=603``)."""
        inner = (
            encode_str(1, conversation_id)
            + encode_uint(2, conversation_short_id)
            + encode_uint(3, conversation_type)
        )
        if last_message_index > 0:
            inner += encode_uint(4, last_message_index)
        pb_req = self._build_envelope(603, inner, inbox_type=0)
        resp_bytes = self._post_im("/v2/conversation/delete", pb_req, sign_request=True)
        code, _desc, _cmd_body = self._extract_response_body(resp_bytes, 603)
        return code == 0

    def delete_stranger_conversations(self) -> bool:
        """Deletes all stranger / message-request conversations via ``/v1/stranger/delete_all_conversations`` (``cmd=1005``)."""
        pb_req = self._build_envelope(1005, b"", inbox_type=0)
        resp_bytes = self._post_im(
            "/v1/stranger/delete_all_conversations", pb_req, sign_request=True
        )
        code, _desc, _cmd_body = self._extract_response_body(resp_bytes, 1005)
        return code == 0


__all__ = [
    "IMMessage",
    "StreakChatInfo",
    "TikTokIMClient",
    "encode_bytes",
    "encode_kv",
    "encode_str",
    "encode_tag",
    "encode_uint",
    "encode_varint",
    "parse_message_body",
    "parse_raw_fields",
    "parse_spotlight_streaks",
]
