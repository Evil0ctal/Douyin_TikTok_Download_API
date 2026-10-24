"""Unit tests for TikTok Read/Write modules (ticket_guard, im_protobuf, read_write_client)."""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from dtk.platforms.tiktok.im_protobuf import (
    encode_bytes,
    encode_kv,
    encode_str,
    encode_uint,
    parse_message_body,
    parse_spotlight_streaks,
)
from dtk.platforms.tiktok.read_write_client import normalize_cookie_jar
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
