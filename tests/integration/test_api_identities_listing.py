"""The proxy column of the identity listing.

The console renders one cell per identity for the egress it is bound to, and it
prefers a name over an id. Nothing ever produced that name, so the column has
always drawn the UUID - correct, and useless to the person deciding which proxy
to retire.

Two things are pinned here beyond the name itself. The listing is the page the
console polls every five seconds at ``limit=200``, so the label may not cost a
query per row; and a proxy URL carries a password (doc 08), so a missing label
is `null` rather than a fallback to anything derived from the URL.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, event

from dtk.api.routes.admin.identities import _proxy_labels, _row
from dtk.core.db import get_engine, session_scope
from dtk.core.types import Platform
from dtk.db.models import Identity, Proxy
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, signed_in

# Fixtures are re-exported by assignment, as in the other API modules: pytest
# picks them up from this module's namespace, and a test parameter of the same
# name does not then shadow an import.
api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
COOKIE_BLOB = "ttwid=1%7Cabcdefghijklmnop; odin_tt=0123456789abcdef"
PROXY_URL = "http://proxyuser:sup3rsecret@10.20.30.40:8080"

#: Every fragment of PROXY_URL that identifies the egress or unlocks it. The
#: listing may return none of them: the id is the only handle it needs.
URL_FRAGMENTS = ("sup3rsecret", "proxyuser", "10.20.30.40")

#: Any statement that reads the proxies table, however it is spelled.
_PROXY_STATEMENT = re.compile(r"\bproxies\b")


async def make_proxy(client: httpx.AsyncClient, *, label: str | None = None) -> str:
    body: dict[str, Any] = {"url": PROXY_URL}
    if label is not None:
        body["label"] = label
    response = await client.post("/api/v1/admin/proxies", json=body)
    assert response.status_code == 201, response.text
    return str(envelope(response)["data"]["id"])


async def make_identity(client: httpx.AsyncClient, *, proxy_id: str | None = None) -> str:
    body: dict[str, Any] = {
        "platform": Platform.DOUYIN.value,
        "cookies": COOKIE_BLOB,
        "user_agent": CHROME_UA,
    }
    if proxy_id is not None:
        body["proxy_id"] = proxy_id
    response = await client.post("/api/v1/admin/identities/import", json=body)
    assert response.status_code == 201, response.text
    return str(envelope(response)["data"]["identity_id"])


async def test_a_bound_identity_carries_the_name_of_its_proxy(client: Any) -> None:
    await signed_in(client)
    proxy_id = await make_proxy(client, label="frankfurt-1")
    identity_id = await make_identity(client, proxy_id=proxy_id)

    response = await client.get("/api/v1/admin/identities")
    rows = envelope(response)["data"]
    assert len(rows) == 1
    assert rows[0]["id"] == identity_id
    assert rows[0]["proxy_id"] == proxy_id
    assert rows[0]["proxy_label"] == "frankfurt-1"
    # The label is the whole of what the egress contributes to this response.
    for fragment in URL_FRAGMENTS:
        assert fragment not in response.text


async def test_an_identity_with_no_proxy_has_no_label(client: Any) -> None:
    await signed_in(client)
    await make_identity(client)

    rows = envelope(await client.get("/api/v1/admin/identities"))["data"]
    assert rows[0]["proxy_id"] is None
    assert rows[0]["proxy_label"] is None


async def test_an_unnamed_proxy_is_null_rather_than_its_url(client: Any) -> None:
    """A proxy nobody labelled has no name, and its URL is not a substitute.

    Masked or not, the URL names the host an operator is scraping through and
    the account it authenticates as. The console already falls back to the id,
    which identifies the egress and unlocks nothing.
    """
    await signed_in(client)
    proxy_id = await make_proxy(client)
    await make_identity(client, proxy_id=proxy_id)

    response = await client.get("/api/v1/admin/identities")
    rows = envelope(response)["data"]
    assert rows[0]["proxy_id"] == proxy_id
    assert rows[0]["proxy_label"] is None
    for fragment in URL_FRAGMENTS:
        assert fragment not in response.text


async def test_a_proxy_deleted_underneath_the_page_has_no_label(client: Any) -> None:
    """The delete button can land between reading the rows and reading the labels.

    ``ON DELETE SET NULL`` repairs the identity row in the database, not the
    copy the request in flight already holds, so the resolver is asked for a
    proxy that no longer exists. It answers nothing, and the row says nothing -
    a page that raised here would go blank because someone else pressed delete.
    """
    await signed_in(client)
    proxy_id = await make_proxy(client, label="doomed")
    identity_id = await make_identity(client, proxy_id=proxy_id)

    async with session_scope() as session:
        identity = await session.get(Identity, uuid.UUID(identity_id))
    assert identity is not None
    assert identity.proxy_id is not None

    async with session_scope() as session:
        await session.execute(delete(Proxy).where(Proxy.id == identity.proxy_id))

    async with session_scope() as session:
        labels = await _proxy_labels(session, [identity])
    assert labels == {}
    assert _row(identity, proxy_label=labels.get(identity.proxy_id))["proxy_label"] is None


async def test_the_page_resolves_every_proxy_in_one_statement(client: Any) -> None:
    """Four identities over two proxies cost one proxy query, not four.

    This is the constraint the whole helper exists for. The listing already
    spends an AES-GCM decrypt per row and the console polls it every five
    seconds; a per-row lookup would not fail any assertion about the label,
    which is exactly why the statement count is asserted instead.
    """
    await signed_in(client)
    first = await make_proxy(client, label="frankfurt-1")
    second = await make_proxy(client, label="osaka-2")
    for proxy_id in (first, first, second, None):
        await make_identity(client, proxy_id=proxy_id)

    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        rows = envelope(await client.get("/api/v1/admin/identities"))["data"]
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(rows) == 4
    assert {row["proxy_label"] for row in rows} == {"frankfurt-1", "osaka-2", None}
    touched_proxies = [s for s in statements if _PROXY_STATEMENT.search(s)]
    assert len(touched_proxies) == 1, touched_proxies
