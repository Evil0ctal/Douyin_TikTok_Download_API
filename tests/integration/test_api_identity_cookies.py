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


# --------------------------------------------------------------------------
# Reveal, export, restore
#
# The masked inventory above is what the drawer loads on every open. These are
# the routes somebody asks for on purpose, and they hand back credentials - so
# what is pinned here is that they do it under the wider scope, that the export
# says what it is, and that a document survives the round trip with the
# fingerprint attached.
# --------------------------------------------------------------------------


async def test_the_reveal_hands_back_the_jar_as_it_will_be_sent(client: Any) -> None:
    await signed_in(client)
    identity_id = await make_identity(client)

    response = await client.get(f"/api/v1/admin/identities/{identity_id}/cookies/reveal")
    assert response.status_code == 200, response.text
    data = envelope(response)["data"]

    assert data["cookies"]["ttwid"] == TTWID
    assert data["cookies"]["UIFID_TEMP"] == UIFID
    # The header is the same jar in the shape curl takes, not a second source
    # of truth about what it holds.
    for name, value in data["cookies"].items():
        assert f"{name}={value}" in data["header"]


async def test_an_export_says_what_it_is(client: Any) -> None:
    """The document is a credential file and has to read as one.

    A file found on a laptop a year from now is the case this is for: without
    a line saying what it holds, an export looks like configuration.
    """
    await signed_in(client)
    identity_id = await make_identity(client)

    response = await client.post(
        "/api/v1/admin/identities/export", json={"identity_ids": [identity_id]}
    )
    assert response.status_code == 200, response.text
    document = envelope(response)["data"]

    assert document["version"] == 1
    assert "credentials" in document["warning"]
    entry = document["identities"][0]
    assert entry["cookies"]["ttwid"] == TTWID
    # The fingerprint travels with the jar. Without it a restore re-infers the
    # browser and signs as a different client than the platform first saw.
    assert entry["user_agent"] == CHROME_UA


async def test_a_document_survives_the_round_trip(client: Any) -> None:
    await signed_in(client)
    exported = await client.post(
        "/api/v1/admin/identities/export",
        json={"identity_ids": [await make_identity(client)]},
    )
    document = envelope(exported)["data"]

    response = await client.post(
        "/api/v1/admin/identities/import/bundle",
        json={"version": document["version"], "identities": document["identities"]},
    )
    assert response.status_code == 201, response.text
    result = envelope(response)["data"]
    assert result["stored"] == 1
    assert result["results"][0]["authenticated"] is True


async def test_a_file_from_a_newer_build_is_refused_rather_than_guessed_at(client: Any) -> None:
    """Reading it anyway would drop the fields this build does not know about.

    Dropping a fingerprint is not a visible failure - it produces an identity
    that works and signs as somebody else, which is the failure risk control is
    built to find.
    """
    await signed_in(client)
    response = await client.post(
        "/api/v1/admin/identities/import/bundle",
        json={"version": 99, "identities": [{"platform": "douyin", "cookies": {"ttwid": "x"}}]},
    )
    assert response.status_code == 400
    assert envelope(response)["error"]["code"] == "INVALID_PARAM"


async def test_an_entry_that_cannot_be_used_does_not_stop_the_others(client: Any) -> None:
    await signed_in(client)
    exported = await client.post(
        "/api/v1/admin/identities/export",
        json={"identity_ids": [await make_identity(client)]},
    )
    document = envelope(exported)["data"]
    # A retired identity exports with an empty jar; this is that entry.
    document["identities"].insert(0, {"platform": "douyin", "cookies": {}})

    response = await client.post(
        "/api/v1/admin/identities/import/bundle",
        json={"version": document["version"], "identities": document["identities"]},
    )
    result = envelope(response)["data"]
    assert result["stored"] == 1
    assert result["submitted"] == 2
    assert result["results"][0]["reason"] == "no_cookies"
