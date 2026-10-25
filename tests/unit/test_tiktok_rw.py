"""Unit tests for TikTok Read/Write modules (ticket_guard, im_protobuf, read_write_client)."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from dtk.platforms.douyin.read_write_client import (
    DouyinReadWriteClient,
    extract_douyin_live_streams,
)
from dtk.platforms.tiktok.im_protobuf import (
    encode_bytes,
    encode_kv,
    encode_str,
    encode_uint,
    parse_message_body,
    parse_spotlight_streaks,
)
from dtk.platforms.tiktok.read_write_client import TikTokReadWriteClient, normalize_cookie_jar
from dtk.signing.native.ticket_guard import (
    TicketGuardCredentials,
    derive_ticket_guard_iv,
    extract_uncompressed_public_key_b64,
    sign_ticket_guard,
)


def _generate_test_ec_keypair() -> tuple[str, ec.EllipticCurvePrivateKey]:
    priv = ec.generate_private_key(ec.SECP256R1())
    der = priv.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(der).decode("ascii"), priv


def test_ticket_guard_signing_verifies() -> None:
    priv_b64, priv_key = _generate_test_ec_keypair()
    creds = TicketGuardCredentials(
        private_key_b64=priv_b64,
        ticket="deadbeef0123456789abcdef",
        ts_sign="ts.1.test_signature",
    )
    headers = sign_ticket_guard("/api/commit/item/digg/", creds, timestamp=1792850000)
    assert headers["tt-ticket-guard-version"] == "2"
    assert headers["tt-ticket-guard-public-key"] == extract_uncompressed_public_key_b64(priv_b64)

    client_data = json.loads(
        base64.b64decode(headers["tt-ticket-guard-client-data"]).decode("utf-8")
    )
    assert client_data["ts_sign"] == "ts.1.test_signature"
    assert client_data["req_content"] == "ticket,path,timestamp"
    assert client_data["timestamp"] == 1792850000

    sig_bytes = base64.b64decode(client_data["req_sign"])
    expected_msg = (
        b"ticket=deadbeef0123456789abcdef&path=/api/commit/item/digg/&timestamp=1792850000"
    )
    priv_key.public_key().verify(sig_bytes, expected_msg, ec.ECDSA(hashes.SHA256()))


def test_derive_ticket_guard_iv() -> None:
    iv = derive_ticket_guard_iv()
    assert len(iv) == 12


def test_protobuf_roundtrip_and_message_parser() -> None:
    msg_bytes = (
        encode_str(1, "0:1:100:200")
        + encode_uint(2, 1)
        + encode_uint(3, 999888777)
        + encode_uint(4, 42)
        + encode_uint(5, 777666555)
        + encode_uint(6, 8)
        + encode_uint(7, 200)
        + encode_str(8, json.dumps({"aweType": 800, "itemId": "7306132438047116586"}))
        + encode_kv(9, "s:client_message_id", "uuid-1234")
        + encode_uint(10, 1792850000000)
    )
    parsed = parse_message_body(msg_bytes)
    assert parsed is not None
    assert parsed.conversation_id == "0:1:100:200"
    assert parsed.server_message_id == 999888777
    assert parsed.conversation_short_id == 777666555
    assert parsed.shared_video_id == "7306132438047116586"
    assert parsed.client_message_id == "uuid-1234"


def test_spotlight_streak_decoder() -> None:
    inner = (
        encode_uint(1, 21)
        + encode_uint(2, 1790000000)
        + encode_uint(3, 1792850000)
        + encode_str(5, "0:1:111:222")
        + encode_uint(9, 1792950000)
        + encode_uint(10, 1)
    )
    outer = encode_bytes(2, inner)
    b64_ext = base64.b64encode(outer).decode("ascii")
    payload = {
        "check_infos": [
            {
                "conv_id": "0:1:111:222",
                "conv_type": 1,
                "unique_id": "friend_user",
                "nick_name": "Friend",
                "biz_ext": b64_ext,
            }
        ]
    }
    streaks = parse_spotlight_streaks(payload, my_uid=111)
    assert len(streaks) == 1
    assert streaks[0].streak_days == 21
    assert streaks[0].peer_uid == 222
    assert streaks[0].username == "friend_user"


def test_normalize_cookie_jar() -> None:
    netscape = ".tiktok.com\tTRUE\t/\tTRUE\t1800000000\tsessionid\tabc123\n"
    jar = normalize_cookie_jar(netscape)
    assert jar.get("sessionid") == "abc123"


def test_extract_douyin_live_streams_normalizes_room_payload() -> None:
    room_obj = {
        "id_str": "7388112233445566778",
        "status": 2,
        "title": "Test Live Room",
        "stream_url": {
            "default_resolution": "FULL_HD1",
            "flv_pull_url": {
                "FULL_HD1": "https://pull-flv.douyin.com/live/stream_hd.flv",
                "SD1": "https://pull-flv.douyin.com/live/stream_sd.flv",
            },
            "hls_pull_url_map": {
                "FULL_HD1": "https://pull-hls.douyin.com/live/stream_hd.m3u8",
            },
        },
    }
    streams = extract_douyin_live_streams(room_obj)
    assert len(streams) == 3
    assert {
        "quality": "FULL_HD1",
        "format": "flv",
        "url": "https://pull-flv.douyin.com/live/stream_hd.flv",
    } in streams
    assert {
        "quality": "FULL_HD1",
        "format": "hls",
        "url": "https://pull-hls.douyin.com/live/stream_hd.m3u8",
    } in streams


def test_douyin_extract_webcast_id_from_urls_and_digits() -> None:
    client = DouyinReadWriteClient(cookies={"sessionid": "test123"})
    res_direct = client.extract_webcast_id("https://live.douyin.com/8848123456")
    assert res_direct["web_rid"] == "8848123456"
    assert res_direct["room_id"] is None

    res_reflow = client.extract_webcast_id(
        "https://webcast.amemv.com/douyin/webcast/reflow/7388112233445566778?sec_user_id=MS4w"
    )
    assert res_reflow["web_rid"] is None
    assert res_reflow["room_id"] == "7388112233445566778"

    res_numeric = client.extract_webcast_id("9988776655")
    assert res_numeric == {"web_rid": "9988776655", "room_id": None, "resolved_url": None}


def test_douyin_rw_client_signing_and_live_methods() -> None:
    client = DouyinReadWriteClient(
        cookies={"sessionid": "dy_sess_123", "passport_csrf_token": "csrf_abc"}
    )
    captured: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(
        method: str,
        url_base: str,
        params: Any,
        *,
        body: bytes = b"",
        sign_mode: str = "abogus",
        referer: str = "",
        origin: str = "",
        use_secsdk_csrf: bool = False,
        extra_headers: Any = None,
    ) -> dict[str, Any]:
        captured.append((method, url_base, dict(params)))
        return {
            "status_code": 0,
            "data": {
                "data": [
                    {
                        "id_str": "739900112233",
                        "status": 2,
                        "title": "Douyin Live",
                        "owner": {"id_str": "1", "sec_uid": "sec_1", "nickname": "Host"},
                        "stream_url": {
                            "flv_pull_url": {"FULL_HD1": "https://example.com/live.flv"},
                            "hls_pull_url_map": {"FULL_HD1": "https://example.com/live.m3u8"},
                        },
                    }
                ]
            },
        }

    client._request_json = fake_request  # type: ignore[method-assign]
    live_res = client.get_live_room("8848123456")
    assert live_res["parsed_room"]["is_live"] is True
    assert live_res["parsed_room"]["room_id"] == "8848123456"
    assert live_res["parsed_room"]["title"] == "Douyin Live"
    assert captured[0][0] == "GET"
    assert "webcast/room/web/enter" in captured[0][1]
    assert captured[0][2]["web_rid"] == "8848123456"

    digg_res = client.digg_video("730000111222333", like=True)
    assert digg_res["status_code"] == 0
    assert captured[1][0] == "POST"
    assert "/aweme/v1/web/commit/item/digg/" in captured[1][1]

    def fake_reflow_request(
        method: str,
        url_base: str,
        params: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.append((method, url_base, dict(params)))
        return {
            "status_code": 0,
            "data": {
                "room": {
                    "id_str": "7388112233445566778",
                    "status": 2,
                    "title": "Reflow Room",
                    "owner": {"web_rid": "8848123456", "nickname": "Host", "sec_uid": "sec_1"},
                    "stream_url": {
                        "flv_pull_url": {"FULL_HD1": "https://example.com/reflow.flv"},
                    },
                }
            },
        }

    client._request_json = fake_reflow_request  # type: ignore[method-assign]
    reflow_res = client.get_live_room_by_room_id("7388112233445566778")
    assert reflow_res["parsed_room"]["is_live"] is True
    assert reflow_res["parsed_room"]["room_id"] == "7388112233445566778"
    assert reflow_res["parsed_room"]["web_rid"] == "8848123456"
    assert len(reflow_res["parsed_room"]["streams"]) == 1


def test_tiktok_rw_client_live_methods() -> None:
    client = TikTokReadWriteClient(cookies={"sessionid": "tt_sess_123"})
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(
        method: str,
        path: str,
        params: Any,
        *,
        body: bytes = b"",
        use_ticket_guard: bool = False,
        referer: str = "",
        extra_headers: Any = None,
    ) -> dict[str, Any]:
        calls.append((method, path, dict(params)))
        return {"status_code": 0, "data": [{"room_id": 7355667788, "alive": True}]}

    client._request_web_json = fake_request  # type: ignore[method-assign]
    room_res = client.get_live_room("@tiktok_creator")
    assert room_res["status_code"] == 0
    assert calls[0][1] == "/api-live/user/room/"
    assert calls[0][2]["uniqueId"] == "tiktok_creator"

    info_res = client.get_live_room_info("7355667788")
    assert info_res["status_code"] == 0
    assert calls[1][1] == "/webcast/room/info/"
    assert calls[1][2]["room_id"] == "7355667788"

    alive_res = client.check_live_alive(["7355667788", "7355667789"])
    assert alive_res["status_code"] == 0
    assert calls[2][1] == "/webcast/room/check_alive/"
    assert calls[2][2]["room_ids"] == "7355667788,7355667789"


def test_normalize_cookie_jar_strips_crlf_and_null_bytes() -> None:
    jar = normalize_cookie_jar(
        {"sessionid\r\n": "abc123\r\nX-Injected: 1\x00", "msToken": "tok_ok"}
    )
    assert jar["sessionid"] == "abc123X-Injected: 1"
    assert "\r" not in jar["sessionid"]
    assert "\n" not in jar["sessionid"]
    assert "\x00" not in jar["sessionid"]


def test_douyin_extract_webcast_id_rejects_spoofed_domains() -> None:
    client = DouyinReadWriteClient(cookies={"sessionid": "test123"})
    spoof_live = client.extract_webcast_id("https://evillive.douyin.com/8848123456")
    assert spoof_live["web_rid"] is None


def test_tiktok_cleanup_account_preserves_streak_conversations() -> None:
    from dtk.platforms.tiktok.im_protobuf import StreakChatInfo

    client = TikTokReadWriteClient(cookies={"sessionid": "tt_sess_123"})
    client.im.conv_short_ids = {
        "0:1:100:200": 111,
        "0:1:100:300": 222,
    }
    deleted_convs: list[str] = []

    client.im.get_active_streaks = lambda: [  # type: ignore[method-assign]
        StreakChatInfo(
            conversation_id="0:1:100:200",
            conversation_type=1,
            streak_days=21,
            username="streak_friend",
            nickname="Streak Friend",
            peer_uid=200,
        )
    ]
    client.im.init_user_messages = lambda max_pages=5: {}  # type: ignore[method-assign]
    client.im.get_conversation_messages = lambda conv_id, limit=50: []  # type: ignore[method-assign]
    client.im.delete_stranger_conversations = lambda: 0  # type: ignore[method-assign]
    client.im.delete_conversation = (  # type: ignore[method-assign]
        lambda conversation_id, conversation_short_id, last_message_index=0: (
            deleted_convs.append(conversation_id) or True
        )
    )

    res = client.cleanup_account(
        "sec_test",
        unlike_all=False,
        uncollect_all=False,
        delete_all_messages=True,
        preserve_streaks=True,
    )
    assert deleted_convs == ["0:1:100:300"]
    assert res["preserved_streak_conversations"] == ["0:1:100:200"]

