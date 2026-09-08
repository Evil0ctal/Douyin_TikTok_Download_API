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
