"""Opening an endpoint on purpose, and the endpoints that can never be opened.

The ban on admin, auth and setup paths is a guarantee rather than a default, so
these are written as attempts to get around it.
"""

from __future__ import annotations

import pytest

from dtk.api import public_endpoints as pe
from dtk.core.types import Scope

VIDEO = "/api/v1/{platform}/video"


# --------------------------------------------------------------------------
# Closed unless opened
# --------------------------------------------------------------------------


def test_nothing_is_public_by_default() -> None:
    assert pe.parse([]) == frozenset()
    assert pe.is_public([], "GET", VIDEO) is False


def test_an_endpoint_is_opened_by_naming_it_exactly() -> None:
    configured = [f"GET {VIDEO}"]
    assert pe.is_public(configured, "GET", VIDEO) is True


def test_opening_one_method_does_not_open_another() -> None:
    configured = [f"GET {VIDEO}"]
    assert pe.is_public(configured, "POST", VIDEO) is False


def test_opening_one_path_does_not_open_a_sibling() -> None:
    configured = [f"GET {VIDEO}"]
    assert pe.is_public(configured, "GET", "/api/v1/{platform}/user") is False


def test_the_setting_is_read_from_a_comma_separated_string_too() -> None:
    """It is typed into a settings field by a person, so shape is forgiving."""
    assert pe.parse(f"GET {VIDEO}, POST /api/v1/parse") == {
        f"GET {VIDEO}",
        "POST /api/v1/parse",
    }


@pytest.mark.parametrize("junk", [None, 42, {"a": 1}, [None, 7], ["nomethod"], [""]])
def test_unparseable_configuration_opens_nothing(junk: object) -> None:
    """The safe direction for a value nobody can read is closed."""
    assert pe.parse(junk) == frozenset()


# --------------------------------------------------------------------------
# The endpoints that can never be opened
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/users",
        "/api/v1/admin/api-keys",
        "/api/v1/admin/settings/{key}",
        "/api/v1/admin/identities",
        "/api/v1/admin/proxies",
        "/api/v1/admin/backup",
        "/api/v1/auth/login",
        "/api/v1/auth/sessions",
        "/api/setup/init",
    ],
)
def test_a_protected_path_is_never_opened_however_it_is_listed(path: str) -> None:
    for method in ("GET", "POST", "PUT", "DELETE"):
        assert pe.is_protected(path) is True
        assert pe.is_public([f"{method} {path}"], method, path) is False


def test_a_protected_entry_is_dropped_from_the_parsed_set() -> None:
    """Not merely ignored at lookup: it never enters the allowlist at all."""
    parsed = pe.parse(["GET /api/v1/admin/users", f"GET {VIDEO}"])
    assert parsed == {f"GET {VIDEO}"}


def test_the_ban_covers_everything_under_the_prefix() -> None:
    """Including routes that do not exist yet."""
    assert pe.is_protected("/api/v1/admin/something/invented/later") is True


# --------------------------------------------------------------------------
# What an anonymous caller may do
# --------------------------------------------------------------------------


def test_an_anonymous_caller_gets_read_scopes_only() -> None:
    principal = pe.anonymous()

    assert principal.scopes == frozenset({Scope.DOUYIN_READ, Scope.TIKTOK_READ})
    assert Scope.ADMIN not in principal.scopes
    assert principal.api_key_id is None


def test_an_anonymous_caller_cannot_satisfy_an_admin_requirement() -> None:
    from dtk.core.errors import ForbiddenScope

    with pytest.raises(ForbiddenScope):
        pe.anonymous().require(Scope.ADMIN)


# --------------------------------------------------------------------------
# The console's switch list
#
# The rows the console renders have to agree with what the request path
# enforces. A first version of _rows walked app.routes, where include_router
# leaves the sub-router's own unprefixed paths ("/api-keys"), so every admin
# route came back unprotected and switchable. The ban still held at request
# time, but the console would have shown a switch that lied.
# --------------------------------------------------------------------------


def _access_rows():
    from dtk.api.app import create_app
    from dtk.api.routes.admin.access import _rows

    class _Req:
        def __init__(self, app: object) -> None:
            self.app = app

    return _rows(_Req(create_app()), frozenset())


def test_the_switch_list_covers_the_documented_api() -> None:
    rows = _access_rows()
    assert len(rows) > 40
    assert all(row["path"].startswith("/api") for row in rows)


def test_every_path_in_the_switch_list_is_a_full_path() -> None:
    """The prefix is what makes a path recognisably an admin one."""
    assert any(row["path"].startswith("/api/v1/admin/") for row in _access_rows())


def test_no_admin_auth_or_setup_row_is_offered_as_switchable() -> None:
    offered = [
        row["path"]
        for row in _access_rows()
        if not row["protected"] and pe.is_protected(row["path"])
    ]
    assert offered == []


def test_the_protected_flag_agrees_with_the_rule_the_request_path_uses() -> None:
    """One source of truth, checked from both sides."""
    for row in _access_rows():
        assert row["protected"] == pe.is_protected(row["path"]), row["path"]


# --------------------------------------------------------------------------
# One scope rule, for every route
#
# `Principal.require` used to short-circuit on the account's role while
# `support.has_scope` did not, and the difference was not academic: almost every
# key on a self-hosted instance belongs to the admin user, so the archive and
# media scopes were unenforceable in exactly the deployment they were written
# for. Both now ask the principal, and these pin that down.
# --------------------------------------------------------------------------


def _key_principal(*scopes: Scope):
    import uuid as _uuid

    from dtk.api.deps import Principal
    from dtk.core.types import UserRole

    return Principal(
        user_id=_uuid.uuid4(),
        # An administrator's own key: the case that used to bypass every check.
        role=UserRole.ADMIN,
        scopes=frozenset(scopes),
        api_key_id=_uuid.uuid4(),
        rate_limit_per_min=None,
    )


def test_an_admins_read_key_is_still_bounded_by_its_scopes() -> None:
    from dtk.core.errors import ForbiddenScope

    key = _key_principal(Scope.DOUYIN_READ)
    assert key.permits(Scope.DOUYIN_READ)
    with pytest.raises(ForbiddenScope):
        key.require(Scope.ARCHIVE_EXPORT)


def test_the_admin_scope_on_a_key_still_opens_everything() -> None:
    assert _key_principal(Scope.ADMIN).permits(Scope.ARCHIVE_EXPORT)


def test_a_console_session_is_bounded_by_role_not_scopes() -> None:
    import uuid as _uuid

    from dtk.api.deps import Principal
    from dtk.core.types import UserRole

    session = Principal(
        user_id=_uuid.uuid4(),
        role=UserRole.ADMIN,
        scopes=frozenset(),
        api_key_id=None,
        rate_limit_per_min=None,
    )
    assert session.permits(Scope.ARCHIVE_EXPORT)


def test_the_two_scope_helpers_agree() -> None:
    from dtk.api.routes.support import has_scope

    key = _key_principal(Scope.DOUYIN_READ)
    for scope in (Scope.DOUYIN_READ, Scope.ARCHIVE_EXPORT, Scope.MEDIA_WRITE):
        assert has_scope(key, [scope]) == key.permits(scope)
