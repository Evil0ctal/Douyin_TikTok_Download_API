"""The request-log reader behind the console's Logs page.

Three things are worth testing here and the rest is plumbing:

* the query cannot be talked out of its bounds. ``request_log`` is a hypertable
  that grows with traffic, and a caller who can drop the time window or the row
  limit can stall the instance from a browser address bar.
* an unknown ``outcome`` is refused. A filter that quietly matches everything
  is indistinguishable, on screen, from a filter that found everything.
* a row is ids and codes, never a credential. The value in ``endpoint`` is the
  logical name the pipeline scheduled on, and the last test here pins that to
  the writer rather than to this file's own fixtures.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dtk.core.db import session_scope
from dtk.core.types import Outcome, Platform, Scope
from dtk.db.models import RequestLog
from tests.integration import test_api_support as support
from tests.integration.test_api_support import (
    anonymous_client,
    envelope,
    error_code,
    make_api_key,
    signed_in,
)

# Fixtures are re-exported by assignment: pytest picks them up from this
# module's namespace, and a test parameter of the same name does not then
# shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

PATH = "/api/v1/admin/logs/requests"

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
#: A logged-in jar, long enough that finding a piece of it in a response is
#: unambiguous. The session value is SYNTHETIC, written for this test.
COOKIE_BLOB = (
    "ttwid=1%7Cabcdefghijklmnop; odin_tt=0123456789abcdef; "
    "sessionid=deadbeefcafebabe0123"  # SYNTHETIC
)
PROXY_URL = "http://proxyuser:sup3rsecret@10.20.30.40:8080"

#: A real endpoint name, so the shape asserted here is the shape the pipeline
#: writes rather than a placeholder that happens to look tidy.
ENDPOINT = "douyin.content_detail"


async def seed(
    *,
    ts: datetime | None = None,
    endpoint: str = ENDPOINT,
    outcome: Outcome = Outcome.OK,
    request_id: uuid.UUID | None = None,
    identity_id: uuid.UUID | None = None,
    proxy_id: uuid.UUID | None = None,
    **columns: Any,
) -> uuid.UUID:
    """Insert one request_log row the way the fetch pipeline does."""
    row_id = request_id or uuid.uuid4()
    async with session_scope() as session:
        session.add(
            RequestLog(
                ts=ts or datetime.now(UTC),
                request_id=row_id,
                platform=Platform.DOUYIN.value,
                endpoint=endpoint,
                identity_id=identity_id,
                proxy_id=proxy_id,
                outcome=outcome.value,
                duration_ms=columns.pop("duration_ms", 120),
                **columns,
            )
        )
    return row_id


async def rows(client: Any, **params: Any) -> list[dict[str, Any]]:
    response = await client.get(PATH, params=params)
    body = envelope(response)
    assert body["success"] is True, body
    return list(body["data"])


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------


async def test_the_request_log_needs_credentials(client: Any) -> None:
    assert error_code(await client.get(PATH)) == "UNAUTHENTICATED"


async def test_a_key_without_an_admin_scope_cannot_read_it(client: Any, api_app: Any) -> None:
    """The same gate ``GET /admin/audit`` uses, for the same reason: both are
    the record of what the instance did, and a plain content key is not it."""
    user_id = await signed_in(client)
    plain = await make_api_key(user_id, scopes=(Scope.DOUYIN_READ,))
    managing = await make_api_key(user_id, scopes=(Scope.IDENTITY_MANAGE,), name="pool key")

    async with anonymous_client(api_app) as caller:
        denied = await caller.get(PATH, headers={"Authorization": f"Bearer {plain}"})
        assert error_code(denied) == "FORBIDDEN_SCOPE"

        allowed = await caller.get(PATH, headers={"Authorization": f"Bearer {managing}"})
        assert allowed.status_code == 200


# --------------------------------------------------------------------------
# The bounds that keep this query off a full scan
# --------------------------------------------------------------------------


async def test_the_window_hides_rows_older_than_it(client: Any) -> None:
    await signed_in(client)
    now = datetime.now(UTC)
    recent = await seed(ts=now - timedelta(minutes=5))
    old = await seed(ts=now - timedelta(hours=3))

    assert [row["request_id"] for row in await rows(client, minutes=60)] == [str(recent)]
    assert [row["request_id"] for row in await rows(client, minutes=240)] == [
        str(recent),
        str(old),
    ]


async def test_a_window_wider_than_the_ceiling_is_refused(client: Any) -> None:
    """Rejected rather than clamped, like ``?wait=``: a caller that asked for a
    year of history has to learn it cannot have one."""
    await signed_in(client)
    assert error_code(await client.get(PATH, params={"minutes": 60 * 24 * 365})) == "INVALID_PARAM"


async def test_the_limit_bounds_the_response_and_takes_the_newest(client: Any) -> None:
    await signed_in(client)
    now = datetime.now(UTC)
    newest = await seed(ts=now - timedelta(seconds=1))
    await seed(ts=now - timedelta(seconds=30))
    await seed(ts=now - timedelta(seconds=60))

    page = await rows(client, limit=1)
    assert [row["request_id"] for row in page] == [str(newest)]


async def test_a_caller_cannot_ask_for_a_million_rows(client: Any) -> None:
    await signed_in(client)
    assert error_code(await client.get(PATH, params={"limit": 1_000_000})) == "INVALID_PARAM"


async def test_rows_come_back_newest_first(client: Any) -> None:
    await signed_in(client)
    now = datetime.now(UTC)
    for age in (300, 60, 5):
        await seed(ts=now - timedelta(seconds=age))

    timestamps = [row["ts"] for row in await rows(client)]
    assert timestamps == sorted(timestamps, reverse=True)


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


async def test_each_filter_narrows_the_result(client: Any) -> None:
    await signed_in(client)
    identity = uuid.uuid4()
    wanted = await seed(endpoint=ENDPOINT, identity_id=identity, outcome=Outcome.RISK_CONTROL)
    await seed(endpoint="douyin.comments", outcome=Outcome.OK)

    assert [row["request_id"] for row in await rows(client, request_id=str(wanted))] == [
        str(wanted)
    ]
    assert [row["request_id"] for row in await rows(client, endpoint=ENDPOINT)] == [str(wanted)]
    assert [row["request_id"] for row in await rows(client, identity_id=str(identity))] == [
        str(wanted)
    ]
    assert [
        row["request_id"] for row in await rows(client, outcome=[Outcome.RISK_CONTROL.value])
    ] == [str(wanted)]


async def test_the_outcome_filter_repeats(client: Any) -> None:
    await signed_in(client)
    await seed(outcome=Outcome.OK)
    await seed(outcome=Outcome.RISK_CONTROL)
    await seed(outcome=Outcome.NETWORK_ERROR)

    selected = await rows(client, outcome=[Outcome.RISK_CONTROL.value, Outcome.NETWORK_ERROR.value])
    assert {row["outcome"] for row in selected} == {"risk_control", "network_error"}


async def test_an_unknown_outcome_is_refused_rather_than_ignored(client: Any) -> None:
    await signed_in(client)
    await seed(outcome=Outcome.OK)

    response = await client.get(PATH, params={"outcome": "teapot"})
    assert error_code(response) == "INVALID_PARAM"
    # The point of refusing: it must not fall back to "everything".
    assert envelope(response)["data"] is None


async def test_a_malformed_request_id_is_refused(client: Any) -> None:
    await signed_in(client)
    assert (
        error_code(await client.get(PATH, params={"request_id": "not-a-uuid"})) == "INVALID_PARAM"
    )


# --------------------------------------------------------------------------
# The response the console renders
# --------------------------------------------------------------------------


async def test_a_row_carries_every_column_the_console_renders(client: Any) -> None:
    await signed_in(client)
    task_id = uuid.uuid4()
    identity_id = uuid.uuid4()
    proxy_id = uuid.uuid4()
    await seed(
        outcome=Outcome.RISK_CONTROL,
        task_id=task_id,
        identity_id=identity_id,
        proxy_id=proxy_id,
        http_status=429,
        duration_ms=812,
        cache_hit=False,
        signer="native",
        error_code="UPSTREAM_RISK_CONTROL",
        reject_reason="circuit_open",
    )

    row = (await rows(client))[0]
    assert set(row) == {
        "ts",
        "request_id",
        "task_id",
        "platform",
        "endpoint",
        "identity_id",
        "proxy_id",
        "outcome",
        "http_status",
        "duration_ms",
        "cache_hit",
        "signer",
        "error_code",
        "reject_reason",
    }
    assert row["task_id"] == str(task_id)
    assert row["identity_id"] == str(identity_id)
    assert row["proxy_id"] == str(proxy_id)
    assert row["outcome"] == Outcome.RISK_CONTROL.value
    assert row["http_status"] == 429
    assert row["duration_ms"] == 812
    assert row["cache_hit"] is False
    assert row["signer"] == "native"
    assert row["error_code"] == "UPSTREAM_RISK_CONTROL"
    # The wire code, untranslated: the console has a phrase for each one and
    # shows an unrecognised code verbatim.
    assert row["reject_reason"] == "circuit_open"


async def test_both_log_tabs_share_the_audit_envelope(client: Any) -> None:
    """The page renders the two tabs from one shape, so the two routes have to
    agree on it: a bare list under ``data``."""
    await signed_in(client)
    await seed()

    for path in (PATH, "/api/v1/admin/audit"):
        body = envelope(await client.get(path))
        assert isinstance(body["data"], list), path
        assert body["error"] is None


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


async def test_no_credential_reaches_a_row(client: Any) -> None:
    """The identity and the proxy appear as ids and nothing else.

    Both secrets are real ones on this instance: the cookie jar went in through
    the import endpoint and the proxy password through the proxy endpoint, so a
    leak here would be a leak of stored data rather than of a fixture string.
    """
    await signed_in(client)
    proxy_id = envelope(await client.post("/api/v1/admin/proxies", json={"url": PROXY_URL}))[
        "data"
    ]["id"]
    identity_id = envelope(
        await client.post(
            "/api/v1/admin/identities/import",
            json={
                "platform": Platform.DOUYIN.value,
                "cookies": COOKIE_BLOB,
                "user_agent": CHROME_UA,
            },
        )
    )["data"]["identity_id"]
    await seed(identity_id=uuid.UUID(identity_id), proxy_id=uuid.UUID(proxy_id))

    response = await client.get(PATH)
    row = envelope(response)["data"][0]
    assert row["identity_id"] == identity_id
    assert row["proxy_id"] == proxy_id

    body = response.text
    assert "sup3rsecret" not in body
    assert "deadbeefcafebabe0123" not in body
    assert "cookie" not in body.lower()
    # No filesystem path and no URL of any kind: a row is names, ids and codes.
    for fragment in ("://", "/Users/", "/var/", "/www/"):
        assert fragment not in body, fragment


async def test_the_logged_endpoint_is_the_logical_name_not_a_signed_url(client: Any) -> None:
    """Pinned to the writer, not to this file's fixtures.

    ``dtk.services.fetch`` builds the signed request and the log line from the
    same call. ``_to_transport_spec`` is where the two part company, and
    ``_log_request`` is what lands in the table; asserting on both together is
    what makes "request_log never holds a signed URL" a property of the code
    rather than of the row this test happened to insert.
    """
    from dtk.platforms import get_adapter
    from dtk.services.fetch import FetchContext, FetchService, _to_transport_spec
    from dtk.signing.base import SignedParams

    signature = "SIGNED-a1b2c3d4e5f6"
    spec = get_adapter(Platform.DOUYIN).build_request(ENDPOINT, aweme_id="7123")
    signed = SignedParams(
        query=f"aweme_id=7123&a_bogus={signature}",
        params={"a_bogus": signature},
        headers={"x-secsdk-web-signature": signature},
    )
    transport = _to_transport_spec(spec, signed, ENDPOINT)

    # The signature IS in the URL, and has to be: the query goes to the platform
    # byte for byte as it was signed. What must never carry it is the logical
    # endpoint name, which is what the log row keys on.
    assert signature in transport.url
    assert transport.endpoint == ENDPOINT
    assert signature not in transport.endpoint

    await signed_in(client)
    ctx = FetchContext()
    async with session_scope() as session:
        # The method touches no collaborator, and standing up a scheduler, a
        # pool and a transport to assert one column would test the harness
        # rather than the writer.
        await FetchService._log_request(
            FetchService.__new__(FetchService),
            session,
            ctx=ctx,
            platform=Platform.DOUYIN,
            endpoint=transport.endpoint,
            identity_id=None,
            outcome=Outcome.OK,
            status=200,
            duration_ms=42,
            error_code=None,
        )

    logged = await rows(client, request_id=str(ctx.request_id))
    assert [row["endpoint"] for row in logged] == [ENDPOINT]
    assert signature not in (await client.get(PATH)).text
