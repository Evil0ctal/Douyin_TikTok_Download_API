"""CORS and the body ceiling on the real application.

The mechanics live in tests/unit/test_api_middleware.py. What is checked here
is what only the assembled application can show: that a settings write in the
database reaches the middleware without a restart, that the ceiling holds on a
real unauthenticated endpoint, and that a refusal comes back through the same
correlation id and language negotiation as every other error.
"""

from __future__ import annotations

from typing import Any

import pytest

from dtk.api.middleware import MAX_REQUEST_BODY_BYTES
from dtk.services import settings_store
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

ORIGIN = "https://app.example"
OTHER = "https://evil.example"

LOGIN = "/api/v1/auth/login"


async def _save(app: Any, key: str, value: Any) -> None:
    """Write a setting and hand the process its new snapshot, as the console does."""
    await settings_store.set_value(key, value)
    app.state.config = await settings_store.load_config()


# --------------------------------------------------------------------------
# CORS
# --------------------------------------------------------------------------


async def test_a_fresh_instance_is_same_origin_only(client: Any) -> None:
    response = await client.get("/healthz", headers={"origin": ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_saving_an_origin_takes_effect_without_a_restart(client: Any, api_app: Any) -> None:
    """The finding: the list was read at construction, so it was always empty.

    The application object is never rebuilt here - only the snapshot behind it
    changes, which is exactly what an operator saving the setting gets.
    """
    before = await client.get("/healthz", headers={"origin": ORIGIN})
    assert "access-control-allow-origin" not in before.headers

    await _save(api_app, "security.cors_allow_origins", [ORIGIN])

    after = await client.get("/healthz", headers={"origin": ORIGIN})
    assert after.headers["access-control-allow-origin"] == ORIGIN

    other = await client.get("/healthz", headers={"origin": OTHER})
    assert "access-control-allow-origin" not in other.headers


async def test_saving_the_origin_from_the_console_is_enough(client: Any) -> None:
    """The whole loop the console offers: an admin saves it, the next call works.

    Nothing between the PUT and the next request restarts anything, which is
    the promise a SENSITIVE runtime setting makes (doc 10).
    """
    await signed_in(client)
    saved = await client.put(
        "/api/v1/admin/settings/security.cors_allow_origins",
        json={"value": [ORIGIN], "confirm": True},
    )
    assert saved.status_code == 200, saved.text

    response = await client.get("/healthz", headers={"origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN


async def test_a_preflight_is_answered_only_for_a_configured_origin(
    client: Any, api_app: Any
) -> None:
    await _save(api_app, "security.cors_allow_origins", [ORIGIN])
    headers = {"access-control-request-method": "POST"}

    allowed = await client.request("OPTIONS", LOGIN, headers={"origin": ORIGIN, **headers})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN

    refused = await client.request("OPTIONS", LOGIN, headers={"origin": OTHER, **headers})
    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers


async def test_credentials_cannot_be_combined_with_a_wildcard(client: Any, api_app: Any) -> None:
    """Both settings saved the dangerous way; the wildcard must win alone."""
    await _save(api_app, "security.cors_allow_credentials", True)
    await _save(api_app, "security.cors_allow_origins", ["*"])

    response = await client.get("/healthz", headers={"origin": OTHER})
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


async def test_an_error_response_still_carries_the_cors_headers(client: Any, api_app: Any) -> None:
    """A browser that cannot read the envelope cannot show the error either."""
    await _save(api_app, "security.cors_allow_origins", [ORIGIN])
    response = await client.get("/api/v1/system/status", headers={"origin": ORIGIN})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "X-Request-ID" in response.headers.get("access-control-expose-headers", "")


# --------------------------------------------------------------------------
# Request body ceiling
# --------------------------------------------------------------------------


async def test_an_unauthenticated_oversized_post_is_refused(client: Any) -> None:
    """No credentials are needed to reach this: the ceiling is the only guard."""
    response = await client.post(
        LOGIN,
        content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["limit_bytes"] == MAX_REQUEST_BODY_BYTES


async def test_a_refusal_is_correlated_and_localized(client: Any) -> None:
    """The ceiling sits inside the request context, not in front of it."""
    payload = b"x" * (MAX_REQUEST_BODY_BYTES + 1)
    headers = {"content-type": "application/json"}
    english = await client.post(LOGIN, content=payload, headers=headers)
    chinese = await client.post(f"{LOGIN}?lang=zh", content=payload, headers=headers)

    assert english.status_code == chinese.status_code == 413
    body = envelope(english)
    assert english.headers["X-Request-ID"] == body["meta"]["request_id"]
    assert body["meta"]["request_id"] != "unknown"
    # The message is rendered from the code in the caller's language; only the
    # code is a contract, so the two must differ without being compared to text.
    translated = envelope(chinese)["error"]
    assert translated["details"]["limit_bytes"] == MAX_REQUEST_BODY_BYTES
    assert translated["message"] != body["error"]["message"]


async def test_a_body_under_the_ceiling_reaches_the_handler(client: Any) -> None:
    """The ceiling must not shadow ordinary validation."""
    response = await client.post(LOGIN, json={"username": "nobody", "password": "wrong"})
    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"
