"""`/system/capabilities` says what `supported()` would say, before anyone asks.

The console greys out an endpoint a platform does not serve, and marks the
ones that need a pinned identity, from this table alone. A table that drifted
from the check the content routes actually make would grey out a working
endpoint or offer one that answers UNSUPPORTED_CONTENT - the exact refusal the
table exists to spare a reader - so the invariant tested here is agreement with
:func:`dtk.api.routes.content.supported`, not any particular platform's list.

Called directly: the route reads only the in-process registry, so a request
object with a correlation id is the whole fixture.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from fastapi.params import Depends

from dtk.api.deps import Principal
from dtk.api.routes import capabilities
from dtk.api.routes.content import supported
from dtk.api.routes.operations import Operation
from dtk.api.routes.support import demo_read
from dtk.core.errors import UnsupportedContent
from dtk.core.types import Platform, Scope, UserRole
from dtk.platforms.registry import available_platforms
from dtk.worker import registry

pytestmark = pytest.mark.anyio

#: The demo account's published key: two platform read scopes, nothing else.
#: If the narrowest credential this instance hands a stranger can read it, so
#: can every other one.
DEMO_KEY = Principal(
    user_id=uuid.uuid4(),
    role=UserRole.DEMO,
    scopes=frozenset({Scope.DOUYIN_READ, Scope.TIKTOK_READ}),
    api_key_id=uuid.uuid4(),
    rate_limit_per_min=None,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request() -> Request:
    return cast(Request, SimpleNamespace(state=SimpleNamespace(request_id=uuid.uuid4())))


def _served(platform: Platform, operation: Operation) -> bool:
    try:
        supported(platform, operation)
    except UnsupportedContent:
        return False
    return True


def test_every_platform_with_an_adapter_is_listed() -> None:
    assert set(capabilities.capability_table()) == set(available_platforms())


@pytest.mark.parametrize("platform", list(Platform))
def test_the_table_agrees_with_what_the_content_routes_refuse(platform: Platform) -> None:
    listed = {row["operation"] for row in capabilities.capability_table()[platform.value]}
    for operation in Operation:
        if operation is Operation.PARSE:
            continue  # the front door: platform-agnostic, never in the registry
        assert (operation.value in listed) == _served(platform, operation), (
            f"{platform.value}.{operation.value}: the table says "
            f"{'served' if operation.value in listed else 'not served'}, the route disagrees"
        )


def test_operations_are_named_the_way_the_routes_and_the_health_board_name_them() -> None:
    for platform, rows in capabilities.capability_table().items():
        for row in rows:
            assert row["endpoint"] == f"{platform}.{row['operation']}"
            assert row["endpoint"] in registry.ENDPOINTS


def test_session_bound_comes_from_the_registry() -> None:
    """The flag the console turns into "identity required" is the route's own."""
    for rows in capabilities.capability_table().values():
        for row in rows:
            assert row["session_bound"] is registry.ENDPOINTS[row["endpoint"]].session_bound
    # At least one, or the console's "required" marker is never exercised and
    # this test passes on a table that dropped the field's meaning entirely.
    assert any(
        row["session_bound"] for rows in capabilities.capability_table().values() for row in rows
    )


def test_internal_adapter_calls_are_not_offered() -> None:
    """The adapters also declare calls no route serves - a stream lookup, a
    session check. Listing them would put rows in the console that 404."""
    from dtk.platforms import get_adapter

    for platform, rows in capabilities.capability_table().items():
        listed = {row["endpoint"] for row in rows}
        internal = set(get_adapter(platform).endpoints.names()) - set(registry.ENDPOINTS)
        assert not listed & internal


async def test_the_route_answers_the_table_in_the_envelope() -> None:
    response = await capabilities.capabilities(_request(), DEMO_KEY)
    body: dict[str, Any] = json.loads(bytes(response.body))
    assert body["success"] is True
    assert body["data"] == json.loads(json.dumps(capabilities.capability_table()))


def test_the_route_is_open_to_the_demo_role() -> None:
    """Every role must get the same answer, or the console greys out different
    rows for a viewer than for an operator on the same instance."""
    import inspect

    default = inspect.signature(capabilities.capabilities).parameters["principal"].default
    assert isinstance(default, Depends)
    assert default.dependency is demo_read
