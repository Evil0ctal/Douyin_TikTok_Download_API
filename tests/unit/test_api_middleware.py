"""The two request middlewares that read settings a user can change at runtime.

Both are exercised against a bare Starlette application rather than the real
one: what is under test is the middleware's own behaviour - which origin gets a
header, which body gets refused - and a route table, a database and an identity
pool would only make the failures harder to read. The same two are checked
against the real application in tests/integration/test_api_cors_and_limits.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from dtk.api.middleware import (
    MAX_REQUEST_BODY_BYTES,
    BodyLimitMiddleware,
    CorsMiddleware,
    cors_policy,
)
from dtk.api.routes.schemas import MAX_PASTE_CHARS
from dtk.core.config import Config

ORIGIN = "https://app.example"
OTHER = "https://evil.example"

#: Small enough that a test body is readable, large enough to span several
#: chunks when httpx streams it.
LIMIT = 64


async def _echo(request: Any) -> PlainTextResponse:
    body = await request.body()
    return PlainTextResponse(str(len(body)))


async def _ignores_body(request: Any) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app(**settings: Any) -> Starlette:
    """A two-route application wearing the same middleware in the same order.

    Settings are passed with underscores for the dots a setting key uses, so a
    test reads as one call rather than a call and a snapshot assignment.
    """
    app = Starlette(
        routes=[
            Route("/echo", _echo, methods=["POST"]),
            Route("/ping", _ignores_body, methods=["GET"]),
        ]
    )
    app.state.config = _snapshot(**settings)
    # add_middleware prepends, so the last one added is the outermost: the same
    # relative order create_app mounts them in.
    app.add_middleware(BodyLimitMiddleware, max_bytes=LIMIT)
    app.add_middleware(CorsMiddleware)
    return app


def client_for(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _snapshot(**settings: Any) -> Config:
    return Config({key.replace("__", "."): value for key, value in settings.items()})


async def _chunks(payload: bytes) -> AsyncIterator[bytes]:
    """Stream a body so httpx sends it chunked, with no Content-Length."""
    for start in range(0, len(payload), 16):
        yield payload[start : start + 16]


# --------------------------------------------------------------------------
# CORS policy
# --------------------------------------------------------------------------


def test_default_policy_is_same_origin_only():
    policy = cors_policy(Config.defaults())
    assert policy.origins == ()
    assert policy.enabled is False


def test_policy_drops_credentials_when_the_origin_list_is_a_wildcard():
    """The one combination a browser would refuse, and the one that leaks keys."""
    policy = cors_policy(
        _snapshot(security__cors_allow_origins=["*"], security__cors_allow_credentials=True)
    )
    assert policy.origins == ("*",)
    assert policy.allow_credentials is False


def test_policy_drops_credentials_when_a_wildcard_hides_in_a_longer_list():
    policy = cors_policy(
        _snapshot(security__cors_allow_origins=[ORIGIN, "*"], security__cors_allow_credentials=True)
    )
    assert policy.origins == ("*",)
    assert policy.allow_credentials is False


def test_policy_keeps_credentials_for_an_explicit_list():
    policy = cors_policy(
        _snapshot(
            security__cors_allow_origins=[f" {ORIGIN} ", ORIGIN, ""],
            security__cors_allow_credentials=True,
        )
    )
    assert policy.origins == (ORIGIN,)
    assert policy.allow_credentials is True


# --------------------------------------------------------------------------
# CORS behaviour
# --------------------------------------------------------------------------


async def test_no_cors_headers_by_default():
    async with client_for(build_app()) as http:
        response = await http.get("/ping", headers={"origin": ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_preflight_is_not_answered_when_no_origin_is_configured():
    """Nothing answers OPTIONS, so the router refuses the method (doc 06)."""
    async with client_for(build_app()) as http:
        response = await http.request(
            "OPTIONS",
            "/echo",
            headers={"origin": ORIGIN, "access-control-request-method": "POST"},
        )
    assert response.status_code == 405
    assert "access-control-allow-origin" not in response.headers


async def test_allowed_origin_is_mirrored_back():
    app = build_app(security__cors_allow_origins=[ORIGIN])
    async with client_for(app) as http:
        response = await http.get("/ping", headers={"origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "origin" in response.headers.get("vary", "").lower()


async def test_other_origins_get_no_allow_header():
    app = build_app(security__cors_allow_origins=[ORIGIN])
    async with client_for(app) as http:
        response = await http.get("/ping", headers={"origin": OTHER})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_preflight_is_answered_only_for_an_allowed_origin():
    app = build_app(security__cors_allow_origins=[ORIGIN])
    headers = {"access-control-request-method": "POST"}
    async with client_for(app) as http:
        allowed = await http.request("OPTIONS", "/echo", headers={"origin": ORIGIN, **headers})
        refused = await http.request("OPTIONS", "/echo", headers={"origin": OTHER, **headers})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in allowed.headers["access-control-allow-methods"]

    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers


async def test_the_origin_list_is_read_per_request_not_at_construction():
    """The finding: the list was read before the lifespan had loaded it.

    Nothing here rebuilds the application, which is what an operator saving the
    setting in the console gets: the same process, a new snapshot.
    """
    app = build_app()
    async with client_for(app) as http:
        before = await http.get("/ping", headers={"origin": ORIGIN})
        app.state.config = _snapshot(security__cors_allow_origins=[ORIGIN])
        after = await http.get("/ping", headers={"origin": ORIGIN})
        app.state.config = _snapshot(security__cors_allow_origins=[])
        again = await http.get("/ping", headers={"origin": ORIGIN})

    assert "access-control-allow-origin" not in before.headers
    assert after.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-origin" not in again.headers


async def test_credentials_are_never_granted_with_a_wildcard_origin():
    app = build_app(security__cors_allow_origins=["*"], security__cors_allow_credentials=True)
    async with client_for(app) as http:
        response = await http.get("/ping", headers={"origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


async def test_credentials_are_granted_for_an_explicit_origin():
    app = build_app(security__cors_allow_origins=[ORIGIN], security__cors_allow_credentials=True)
    async with client_for(app) as http:
        response = await http.get("/ping", headers={"origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


# --------------------------------------------------------------------------
# Request body ceiling
# --------------------------------------------------------------------------


def test_the_ceiling_leaves_room_for_the_largest_paste_a_schema_accepts():
    """A body the request models accept must never be refused by the ceiling.

    Pastes are the largest legitimate body and are ASCII in practice, so the
    ceiling has to clear MAX_PASTE_CHARS with room for JSON quoting.
    """
    assert MAX_REQUEST_BODY_BYTES >= MAX_PASTE_CHARS * 5


async def test_a_body_at_the_ceiling_is_accepted():
    async with client_for(build_app()) as http:
        response = await http.post("/echo", content=b"x" * LIMIT)
    assert response.status_code == 200
    assert response.text == str(LIMIT)


async def test_a_body_over_the_ceiling_is_refused_in_the_envelope():
    async with client_for(build_app()) as http:
        response = await http.post("/echo", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INVALID_PARAM"
    assert body["error"]["details"]["limit_bytes"] == LIMIT
    # A sentence in the caller's language, because "INVALID_PARAM" alone does
    # not tell an operator which parameter was wrong.
    assert body["error"]["details"]["reason"]
    assert body["meta"]["request_id"]


async def test_an_oversized_body_is_refused_even_when_the_route_would_not_read_it():
    """A declared Content-Length is refused before the application is entered."""
    async with client_for(build_app()) as http:
        response = await http.request("POST", "/ping", content=b"x" * (LIMIT + 1))
    assert response.status_code == 413


async def test_a_chunked_body_over_the_ceiling_is_refused_while_it_streams():
    """Nothing declares a length, so the ceiling has to be counted (doc 06)."""
    async with client_for(build_app()) as http:
        response = await http.post("/echo", content=_chunks(b"x" * (LIMIT * 4)))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "INVALID_PARAM"


async def test_a_chunked_body_under_the_ceiling_still_reaches_the_handler():
    async with client_for(build_app()) as http:
        response = await http.post("/echo", content=_chunks(b"x" * (LIMIT - 1)))
    assert response.status_code == 200
    assert response.text == str(LIMIT - 1)


async def test_requests_without_a_body_are_untouched():
    async with client_for(build_app()) as http:
        response = await http.get("/ping")
    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.parametrize("declared", ["not-a-number", ""])
async def test_an_unparseable_content_length_falls_back_to_counting(declared: str):
    """A header we cannot read is not a size we can act on, but the count is."""
    payload = b"x" * (LIMIT + 1)
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        request = http.build_request("POST", "/echo", content=payload)
        request.headers["content-length"] = declared
        response = await http.send(request)
    assert response.status_code == 413
