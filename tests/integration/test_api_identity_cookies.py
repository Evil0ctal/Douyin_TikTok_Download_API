"""What ``GET /admin/identities/{id}/cookies`` may and may not say.

The drawer that reads this exists because an identity was a row of verdicts:
when one stopped working the console could say *that* the session was spent and
never *what the jar held*, so the next step was a shell and a hand-rolled
decrypt. This endpoint moves that step into the page.

It is also the first read route in the module that touches the cookie column,
and the module's rule is that no response ever carries a cookie. So the two
things pinned here are the two that matter: the inventory is genuinely useful -
names, roles, lengths - and not one raw value reaches the wire.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from dtk.core.types import Platform
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

#: Distinctive enough that finding any of them in a response is unambiguous.
TTWID = "1%7Cthisisthettwidvalueandnothingelse"
UIFID = "uifidtempvaluethatthesignerneedstosignwith"
SESSION = "sessionidvaluethatwouldbesomebodysaccount"

COOKIE_BLOB = f"ttwid={TTWID}; UIFID_TEMP={UIFID}; sessionid={SESSION}; junk=x"

#: Every value the jar holds. None may appear in a response, in any field.
SECRETS = (TTWID, UIFID, SESSION)


async def make_identity(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/admin/identities/import",
        json={
            "platform": Platform.DOUYIN.value,
            "cookies": COOKIE_BLOB,
            "user_agent": CHROME_UA,
        },
    )
    assert response.status_code == 201, response.text
    return str(envelope(response)["data"]["identity_id"])


async def inventory(client: httpx.AsyncClient, identity_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/admin/identities/{identity_id}/cookies")
    assert response.status_code == 200, response.text
    return dict(envelope(response)["data"])


async def test_the_jar_is_described_cookie_by_cookie(client: Any) -> None:
    await signed_in(client)
    data = await inventory(client, await make_identity(client))

    by_name = {entry["name"]: entry for entry in data["cookies"]}
    assert set(by_name) == {"ttwid", "UIFID_TEMP", "sessionid", "junk"}
    assert by_name["ttwid"]["length"] == len(TTWID)
    assert by_name["UIFID_TEMP"]["length"] == len(UIFID)


async def test_each_cookie_says_why_it_matters(client: Any) -> None:
    """The four buckets the console colours and explains by.

    `UIFID_TEMP` is the one worth a test of its own: the importer does not
    judge a paste on it, so it came back "other" and the console rendered "not
    something this build reads" over the value the Douyin signer signs with.
    """
    await signed_in(client)
    data = await inventory(client, await make_identity(client))

    roles = {entry["name"]: entry["role"] for entry in data["cookies"]}
    assert roles == {
        "ttwid": "required",
        "UIFID_TEMP": "useful",
        "sessionid": "session",
        "junk": "other",
    }


async def test_a_logged_in_jar_is_reported_as_one(client: Any) -> None:
    await signed_in(client)
    data = await inventory(client, await make_identity(client))
    assert data["authenticated"] is True
    assert data["missing_required"] == []
    assert data["readable"] is True


async def test_no_cookie_value_reaches_the_response(client: Any) -> None:
    """The rule the whole module is built on, checked against the raw body.

    Against the serialised response rather than a parsed field, because the
    point is that no value escapes anywhere - not in a mask that kept too much,
    not in an error detail, not in a field added later by someone who did not
    read the docstring.
    """
    await signed_in(client)
    identity_id = await make_identity(client)
    response = await client.get(f"/api/v1/admin/identities/{identity_id}/cookies")
    body = response.text

    for secret in SECRETS:
        assert secret not in body, f"a cookie value reached the response: {secret[:8]}…"

    # The mask keeps four characters at each end; anything longer than that
    # would be a mask that gives the value away a piece at a time.
    for entry in json.loads(body)["data"]["cookies"]:
        kept = entry["masked"].replace("*", "")
        assert len(kept) <= 8, entry["name"]


async def test_an_unknown_identity_is_a_404_rather_than_an_empty_jar(client: Any) -> None:
    """An empty inventory and a missing identity are different facts.

    Rendering the second as the first would tell an operator their identity
    holds no cookies, which is a real state with a real cause, about an
    identity that does not exist.
    """
    await signed_in(client)
    response = await client.get(
        "/api/v1/admin/identities/00000000-0000-4000-8000-000000000000/cookies"
    )
    assert response.status_code == 404
    assert envelope(response)["error"]["code"] == "NOT_FOUND"
