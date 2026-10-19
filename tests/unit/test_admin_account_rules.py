"""The rules the key and account routes decide before touching the database.

Each one here was a console click away from going wrong: a role picker that
saved on change and could make the published demo account an administrator, a
5.1.6 upgrade note that told operators to edit a key the API had no way to
edit, and a demo card that read the refusal of a viewer as "the demo is off".
The routes themselves are exercised end to end in
tests/integration/test_api_admin.py and test_api_demo.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from dtk.api.deps import Principal
from dtk.api.routes.admin import api_keys, users
from dtk.api.routes.admin import demo as demo_route
from dtk.api.routes.schemas import ApiKeyUpdate, UserUpdate
from dtk.api.routes.support import admin_only, manage_pool, read_admin
from dtk.core.errors import ForbiddenScope, InvalidParam
from dtk.core.types import PLATFORM_READ_SCOPES, Scope, UserRole
from dtk.db.models import ApiKey, User
from dtk.services import demo


def _user(role: UserRole) -> User:
    return User(id=uuid.uuid4(), username=role.value, password_hash="x", role=role.value)


def _key_principal(*scopes: Scope) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        role=UserRole.ADMIN,
        scopes=frozenset(scopes),
        api_key_id=uuid.uuid4(),
        rate_limit_per_min=None,
    )


# --------------------------------------------------------------------------
# The demo account
# --------------------------------------------------------------------------


class TestTheDemoAccountIsNotShapedByHand:
    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER])
    def test_its_role_cannot_be_changed(self, role: UserRole) -> None:
        """The promotion one click on the Users page used to perform."""
        with pytest.raises(InvalidParam) as caught:
            users._refuse_demo_account_change(_user(UserRole.DEMO), UserUpdate(role=role))
        assert caught.value.details["field"] == "role"
        assert caught.value.details["setting"] == demo.SETTING_KEY

    def test_its_password_cannot_be_reset(self) -> None:
        """A reset would leave the login page printing the old one."""
        with pytest.raises(InvalidParam) as caught:
            users._refuse_demo_account_change(
                _user(UserRole.DEMO), UserUpdate(password="a long enough password")
            )
        assert caught.value.details["field"] == "password"

    def test_it_cannot_be_deleted(self) -> None:
        with pytest.raises(InvalidParam) as caught:
            users._refuse_demo_account_delete(_user(UserRole.DEMO))
        assert caught.value.details["setting"] == demo.SETTING_KEY

    @pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER])
    def test_every_other_account_is_left_alone(self, role: UserRole) -> None:
        users._refuse_demo_account_change(_user(role), UserUpdate(role=UserRole.VIEWER))
        users._refuse_demo_account_change(_user(role), UserUpdate(password="another password"))
        users._refuse_demo_account_delete(_user(role))


# --------------------------------------------------------------------------
# Editing a key
# --------------------------------------------------------------------------


class TestTheEditBody:
    def test_both_fields_are_optional(self) -> None:
        """The route, not the schema, refuses an empty edit - so it can say
        which fields there were to send."""
        body = ApiKeyUpdate()
        assert body.name is None and body.scopes is None

    def test_an_empty_name_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ApiKeyUpdate(name="")

    def test_an_unknown_scope_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ApiKeyUpdate(scopes=["douyin:write"])  # type: ignore[list-item]

    def test_the_secret_is_not_a_field(self) -> None:
        """Editing never re-keys: whatever holds the key keeps working."""
        with pytest.raises(ValidationError):
            ApiKeyUpdate.model_validate({"key_hash": "x"})


class TestAnEditIsBoundedLikeACreate:
    def test_a_key_cannot_widen_another_past_its_own_scopes(self) -> None:
        caller = _key_principal(Scope.IDENTITY_MANAGE)
        with pytest.raises(ForbiddenScope):
            api_keys._refuse_escalation(caller, [Scope.ADMIN])

    def test_a_key_may_hand_on_what_it_holds(self) -> None:
        caller = _key_principal(*PLATFORM_READ_SCOPES)
        api_keys._refuse_escalation(caller, list(PLATFORM_READ_SCOPES))


class _Session:
    """Answers `get(User, id)` from a dict, which is all the check asks."""

    def __init__(self, *rows: User) -> None:
        self._rows = {row.id: row for row in rows}

    async def get(self, _model: Any, key: uuid.UUID) -> User | None:
        return self._rows.get(key)


def _api_key(owner: User, *, encrypted: bytes | None = None) -> ApiKey:
    return ApiKey(
        id=uuid.uuid4(),
        user_id=owner.id,
        name="k",
        prefix="abcd1234",
        key_hash="x",
        scopes=[],
        demo_secret_encrypted=encrypted,
    )


class TestThePublishedDemoKeyIsRecognised:
    async def test_by_its_readable_copy(self) -> None:
        owner = _user(UserRole.ADMIN)
        key = _api_key(owner, encrypted=b"ciphertext")
        assert await api_keys._is_published_demo_key(_Session(owner), key)  # type: ignore[arg-type]

    async def test_by_its_owner_when_it_predates_the_readable_copy(self) -> None:
        owner = _user(UserRole.DEMO)
        key = _api_key(owner)
        assert await api_keys._is_published_demo_key(_Session(owner), key)  # type: ignore[arg-type]

    async def test_an_operators_key_is_not(self) -> None:
        owner = _user(UserRole.OPERATOR)
        key = _api_key(owner)
        assert not await api_keys._is_published_demo_key(_Session(owner), key)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The demo card's state
# --------------------------------------------------------------------------


def _credentials(scopes: tuple[str, ...], prefix: str = "abcd1234") -> demo.DemoCredentials:
    return demo.DemoCredentials(
        username=demo.DEMO_USERNAME,
        password=None,
        api_key=None,
        api_key_prefix=prefix,
        scopes=scopes,
    )


class TestMissingScopes:
    def test_a_key_minted_before_bilibili_is_missing_its_read(self) -> None:
        """The 5.1.6 upgrade: a demo published earlier kept refusing Bilibili."""
        held = (Scope.DOUYIN_READ.value, Scope.TIKTOK_READ.value)
        assert demo_route._missing_scopes(_credentials(held)) == [Scope.BILIBILI_READ.value]

    def test_a_current_key_is_missing_nothing(self) -> None:
        held = tuple(scope.value for scope in demo.DEMO_KEY_SCOPES)
        assert demo_route._missing_scopes(_credentials(held)) == []

    def test_no_key_is_not_reported_as_a_stale_one(self) -> None:
        """ "Not provisioned" says so on its own; a reissue banner beside it
        would describe a key that does not exist."""
        assert demo_route._missing_scopes(None) == []
        assert demo_route._missing_scopes(_credentials((), prefix="")) == []


# --------------------------------------------------------------------------
# Who may read and who may change
# --------------------------------------------------------------------------


def _calls(dependant: Any) -> Iterator[Callable[..., Any]]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from _calls(dependency)


def _guards(router: Any, method: str, path: str) -> set[Callable[..., Any]]:
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return set(_calls(route.dependant))
    raise AssertionError(f"{method} {path} is not registered")


def test_any_console_reader_may_see_whether_the_demo_runs() -> None:
    """The card on Settings showed a running demo as off to everyone below
    administrator, because this answered them 403 and the card read that as
    off."""
    assert read_admin in _guards(demo_route.router, "GET", "/demo")
    assert admin_only not in _guards(demo_route.router, "GET", "/demo")


def test_only_an_administrator_may_reissue_the_demo() -> None:
    assert admin_only in _guards(demo_route.router, "POST", "/demo/rotate")


def test_editing_a_key_takes_what_creating_one_takes() -> None:
    assert manage_pool in _guards(api_keys.router, "PATCH", "/api-keys/{key_id}")
    assert manage_pool in _guards(api_keys.router, "POST", "/api-keys")
