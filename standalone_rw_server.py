"""Standalone zero-database HTTP/FastAPI server for the TikTok Read/Write & Protobuf IM API.

Works out-of-the-box with either:
  1. FastAPI + Uvicorn (when installed)
  2. Python's built-in ``ThreadingHTTPServer`` fallback (zero web-framework dependencies required)

Run directly without PostgreSQL or Redis:
    python standalone_rw_server.py --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from typing import Any

# Ensure local `src/` is importable when running directly from repository root
_SRC_DIR = Path(__file__).resolve().parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from dtk import __version__  # noqa: E402
from dtk.platforms.tiktok.im_protobuf import DEFAULT_DEVICE_ID, DEFAULT_WEB_UA  # noqa: E402
from dtk.platforms.tiktok.read_write_client import TikTokReadWriteClient  # noqa: E402
from dtk.signing.native.ticket_guard import TicketGuardCredentials, sign_ticket_guard  # noqa: E402


def _build_client_from_payload(payload: dict[str, Any]) -> TikTokReadWriteClient:
    tg_raw = payload.get("ticket_guard")
    tg_creds: TicketGuardCredentials | None = None
    if isinstance(tg_raw, dict):
        if tg_raw.get("ticket"):
            tg_creds = TicketGuardCredentials(
                private_key_b64=str(tg_raw.get("private_key_b64") or ""),
                ticket=str(tg_raw.get("ticket") or ""),
                ts_sign=str(tg_raw.get("ts_sign") or ""),
                public_key_raw_b64=str(tg_raw.get("public_key_raw_b64") or ""),
            )
        elif tg_raw.get("encrypt_ticket_b64"):
            tg_creds = TicketGuardCredentials.from_local_storage(
                {"ec_privateKey": str(tg_raw.get("private_key_b64") or "")},
                {"ts_sign": str(tg_raw.get("ts_sign") or ""), "encrypt_ticket": str(tg_raw.get("encrypt_ticket_b64") or "")},
            )
    return TikTokReadWriteClient(
        payload.get("cookies") or {},
        ticket_guard=tg_creds,
        user_agent=str(payload.get("user_agent") or DEFAULT_WEB_UA),
        device_id=str(payload.get("device_id") or DEFAULT_DEVICE_ID),
    )


def dispatch_rw_action(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatches ``/api/v1/tiktok/rw/*`` requests directly to :class:`TikTokReadWriteClient`."""
    if path == "/api/v1/tiktok/rw/sign-ticket-guard":
        tg_raw = payload.get("ticket_guard") or {}
        creds = TicketGuardCredentials(
            private_key_b64=str(tg_raw.get("private_key_b64") or ""),
            ticket=str(tg_raw.get("ticket") or ""),
            ts_sign=str(tg_raw.get("ts_sign") or ""),
            public_key_raw_b64=str(tg_raw.get("public_key_raw_b64") or ""),
        )
        target_path = str(payload.get("path") or "/api/commit/item/digg/")
        ts = payload.get("timestamp")
        return {"path": target_path, "headers": sign_ticket_guard(target_path, creds, timestamp=ts)}

    client = _build_client_from_payload(payload)

    if path == "/api/v1/tiktok/rw/streaks":
        streaks = client.im.get_active_streaks()
        return {"count": len(streaks), "streaks": [s.to_dict() for s in streaks]}

    if path == "/api/v1/tiktok/rw/streaks/maintain":
        return client.maintain_streaks(
            like_incoming=bool(payload.get("like_incoming", True)),
            send_video_if_needed=bool(payload.get("send_video_if_needed", True)),
            force_send=bool(payload.get("force_send", False)),
            max_likes_per_chat=int(payload.get("max_likes_per_chat", 10)),
        )

    if path == "/api/v1/tiktok/rw/im/conversations":
        conv_map = client.im.init_user_messages()
        conversations = [
            {
                "conversation_id": cid,
                "conversation_short_id": client.im.conv_short_ids.get(cid, 0),
                "messages": [m.to_dict() for m in msgs],
            }
            for cid, msgs in conv_map.items()
        ]
        return {"count": len(conversations), "conversations": conversations}

    if path == "/api/v1/tiktok/rw/im/messages":
        client.im.init_user_messages(1)
        cid = str(payload["conversation_id"])
        msgs = client.im.get_conversation_messages(
            cid,
            peer_uid=payload.get("peer_uid"),
            limit=int(payload.get("limit", 25)),
        )
        return {
            "conversation_id": cid,
            "conversation_short_id": client.im.conv_short_ids.get(cid, 0),
            "count": len(msgs),
            "messages": [m.to_dict() for m in msgs],
        }

    if path == "/api/v1/tiktok/rw/im/send-text":
        msg_id = client.im.send_text_message(
            str(payload["conversation_id"]),
            str(payload["text"]),
            peer_uid=payload.get("peer_uid"),
        )
        return {"sent": msg_id > 0, "server_message_id": msg_id}

    if path == "/api/v1/tiktok/rw/im/send-video":
        msg_id = client.im.send_video_card(
            str(payload["conversation_id"]),
            payload["card"],
            peer_uid=payload.get("peer_uid"),
        )
        return {"sent": msg_id > 0, "server_message_id": msg_id}

    if path == "/api/v1/tiktok/rw/im/react":
        emoji = str(payload.get("emoji", "❤️"))
        remove = bool(payload.get("remove", False))
        ok_flag = client.im.react_message(
            conversation_id=str(payload["conversation_id"]),
            conversation_short_id=int(payload["conversation_short_id"]),
            server_message_id=int(payload["server_message_id"]),
            emoji=emoji,
            remove=remove,
            client_message_id=str(payload.get("client_message_id", "")),
        )
        return {"reacted": ok_flag, "emoji": emoji, "removed": remove}

    if path == "/api/v1/tiktok/rw/im/delete-message":
        sid = int(payload["server_message_id"])
        deleted = client.im.delete_message(
            conversation_id=str(payload["conversation_id"]),
            conversation_short_id=int(payload["conversation_short_id"]),
            server_message_id=sid,
        )
        return {"deleted": deleted, "server_message_id": sid}

    if path == "/api/v1/tiktok/rw/im/delete-conversation":
        cid = str(payload["conversation_id"])
        deleted = client.im.delete_conversation(
            conversation_id=cid,
            conversation_short_id=int(payload["conversation_short_id"]),
            last_message_index=int(payload.get("last_message_index", 0)),
        )
        return {"deleted": deleted, "conversation_id": cid}

    if path == "/api/v1/tiktok/rw/video/digg":
        return client.digg_video(str(payload["aweme_id"]), like=bool(payload.get("like", True)))

    if path == "/api/v1/tiktok/rw/video/collect":
        return client.collect_video(
            str(payload["item_id"]),
            str(payload["sec_uid"]),
            collect=bool(payload.get("collect", True)),
        )

    if path == "/api/v1/tiktok/rw/video/likes":
        return client.get_liked_videos(
            str(payload["sec_uid"]),
            count=int(payload.get("count", 30)),
            cursor=str(payload.get("cursor", "0")),
        )

    if path == "/api/v1/tiktok/rw/video/bookmarks":
        return client.get_collected_videos(
            str(payload["sec_uid"]),
            count=int(payload.get("count", 30)),
            cursor=str(payload.get("cursor", "0")),
        )

    if path == "/api/v1/tiktok/rw/social/follow":
        return client.follow_user(
            str(payload["user_id"]),
            str(payload["sec_user_id"]),
            follow=bool(payload.get("follow", True)),
            from_type=str(payload.get("from_type", "18")),
        )

    if path == "/api/v1/tiktok/rw/social/followers/list":
        return client.get_followers_list(
            str(payload["sec_uid"]),
            count=int(payload.get("count", 30)),
            min_cursor=str(payload.get("cursor", "0")),
        )

    if path == "/api/v1/tiktok/rw/social/following/list":
        return client.get_following_list(
            str(payload["sec_uid"]),
            count=int(payload.get("count", 30)),
            min_cursor=str(payload.get("cursor", "0")),
        )

    if path == "/api/v1/tiktok/rw/social/followers/unfollowed":
        users = client.get_unfollowed_followers(sec_uid=payload.get("sec_uid"))
        return {"count": len(users), "followers": users}

    if path == "/api/v1/tiktok/rw/social/follow-back":
        return client.follow_back_all_followers(max_follows=int(payload.get("max_follows", 15)))

    if path == "/api/v1/tiktok/rw/feed/foryou":
        return client.get_foryou_feed(count=int(payload.get("count", 15)))

    if path == "/api/v1/tiktok/rw/account/cleanup":
        return client.cleanup_account(
            str(payload["sec_uid"]),
            unlike_all=bool(payload.get("unlike_all", True)),
            uncollect_all=bool(payload.get("uncollect_all", True)),
            delete_all_messages=bool(payload.get("delete_all_messages", True)),
        )

    raise KeyError(f"Unknown endpoint: {path}")


ROUTES_MANIFEST = [
    "POST /api/v1/tiktok/rw/streaks",
    "POST /api/v1/tiktok/rw/streaks/maintain",
    "POST /api/v1/tiktok/rw/im/conversations",
    "POST /api/v1/tiktok/rw/im/messages",
    "POST /api/v1/tiktok/rw/im/send-text",
    "POST /api/v1/tiktok/rw/im/send-video",
    "POST /api/v1/tiktok/rw/im/react",
    "POST /api/v1/tiktok/rw/im/delete-message",
    "POST /api/v1/tiktok/rw/im/delete-conversation",
    "POST /api/v1/tiktok/rw/video/digg",
    "POST /api/v1/tiktok/rw/video/collect",
    "POST /api/v1/tiktok/rw/video/likes",
    "POST /api/v1/tiktok/rw/video/bookmarks",
    "POST /api/v1/tiktok/rw/social/follow",
    "POST /api/v1/tiktok/rw/social/followers/list",
    "POST /api/v1/tiktok/rw/social/following/list",
    "POST /api/v1/tiktok/rw/social/followers/unfollowed",
    "POST /api/v1/tiktok/rw/social/follow-back",
    "POST /api/v1/tiktok/rw/feed/foryou",
    "POST /api/v1/tiktok/rw/account/cleanup",
    "POST /api/v1/tiktok/rw/sign-ticket-guard",
]


class _StdlibRWHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/health", "/docs"):
            self._send_json(
                200,
                {
                    "status": "ok",
                    "version": __version__,
                    "mode": "standalone-rw",
                    "endpoints": ROUTES_MANIFEST,
                },
            )
            return
        self._send_json(404, {"ok": False, "error": f"Not found: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
        try:
            payload = json.loads(raw) if raw.strip() else {}
            data = dispatch_rw_action(self.path.split("?", 1)[0], payload)
            self._send_json(200, {"ok": True, "data": data})
        except KeyError as exc:
            self._send_json(404, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone TikTok Read/Write API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()

    try:
        from fastapi import FastAPI
        import uvicorn
        from dtk.api.routes.tiktok_rw import router as tiktok_rw_router

        app = FastAPI(
            title="Douyin & TikTok Full Read/Write API (Standalone)",
            version=__version__,
            docs_url="/docs",
        )
        app.include_router(tiktok_rw_router)

        @app.get("/health", tags=["system"])
        def health() -> dict[str, str]:
            return {"status": "ok", "version": __version__, "mode": "fastapi-rw"}

        uvicorn.run(app, host=args.host, port=args.port)
    except ImportError:
        srv = ThreadingHTTPServer((args.host, args.port), _StdlibRWHandler)
        print(f"TikTok Read/Write Standalone Server v{__version__} listening on http://{args.host}:{args.port}")
        srv.serve_forever()


if __name__ == "__main__":
    main()
