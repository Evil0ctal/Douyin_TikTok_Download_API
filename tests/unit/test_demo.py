"""The public demo account: what it may reach, and what turns it off.

The tests that matter most here are the ones that fail when somebody adds a
route. A demo instance is the one deployment where a missed authorization check
is exercised by strangers rather than by the operator, so the properties are
asserted against the live OpenAPI document rather than against a list written
by hand beside the one in the source.
"""

from __future__ import annotations

import uuid

import pytest

from dtk.api import demo_readonly
from dtk.api.deps import Principal
from dtk.api.routes.support import ROLE_RANK
from dtk.core.types import Scope, UserRole
from dtk.services import demo

WRITE_METHODS = ("post", "put", "patch", "delete")

#: Endpoints that require no credentials at all, so the demo rule never sees
#: them. `login` has no principal yet by definition, and setup runs before any
#: account exists.
UNAUTHENTICATED = frozenset(
    {
        "POST /api/setup/init",
        "POST /api/v1/auth/login",
    }
)


@pytest.fixture(scope="module")
def spec() -> dict:
    from dtk.api.app import create_app

    return create_app().openapi()


def _write_routes(spec: dict) -> list[str]:
    return sorted(
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        for method in item
        if method in WRITE_METHODS
    )


class TestRoleLadder:
    def test_demo_ranks_below_every_real_role(self):
        """The whole safety argument rests on this one inequality.

        Every guard in the codebase defaults to `min_role=VIEWER`. If demo ever
        ranked at or above viewer, all of them would open at once.
        """
        assert ROLE_RANK[UserRole.DEMO] < ROLE_RANK[UserRole.VIEWER]
        assert ROLE_RANK[UserRole.DEMO] < ROLE_RANK[UserRole.OPERATOR]
        assert ROLE_RANK[UserRole.DEMO] < ROLE_RANK[UserRole.ADMIN]

    def test_every_role_has_a_rank(self):
        """A role missing from the table raises KeyError inside a guard, which
        would turn an authorization decision into a 500."""
        assert set(ROLE_RANK) == set(UserRole)


class TestReadOnlyRule:
    def test_reads_are_never_refused(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            assert demo_readonly.is_allowed(method, "/api/v1/admin/users") is True

    def test_an_unmatched_route_is_refused(self):
        """Closed by default, including for a request on its way to a 404."""
        assert demo_readonly.is_allowed("POST", None) is False

    def test_the_scraping_calls_are_allowed(self):
        assert demo_readonly.is_allowed("POST", "/api/v1/parse") is True
        assert demo_readonly.is_allowed("POST", "/api/v1/tasks/batch") is True

    @pytest.mark.parametrize(
        "route",
        [
            "/api/v1/downloads",
            "/api/v1/auth/password",
            "/api/v1/tools/identity",
            "/api/v1/admin/users",
            "/api/v1/admin/settings/{key}",
            "/api/v1/archive/delete",
        ],
    )
    def test_the_expensive_and_the_destructive_are_refused(self, route: str):
        assert demo_readonly.is_allowed("POST", route) is False

    def test_password_change_is_refused(self):
        """The demo password is published. One visitor changing it would lock
        out every other visitor and the README with them."""
        assert "POST /api/v1/auth/password" not in demo_readonly.ALLOWED_WRITES

    def test_every_allowlisted_route_exists(self, spec):
        """An entry that matches nothing is a typo that reads as a permission.

        Without this, `POST /api/v1/tools/parse_batch` - underscore instead of
        hyphen - would sit in the list looking like the demo could split a
        pasted batch, while the real route stayed refused.
        """
        real = set(_write_routes(spec))
        missing = sorted(demo_readonly.ALLOWED_WRITES - real - UNAUTHENTICATED)
        assert missing == [], f"allowlisted routes that do not exist: {missing}"

    def test_no_write_endpoint_is_open_to_demo_by_accident(self, spec):
        """The drift test.

        Every write endpoint this instance serves is either on the allowlist or
        refused for the demo account. A route added later is refused by default,
        and this fails only if somebody adds one to the allowlist without
        thinking - which is exactly the moment to make them think.
        """
        opened = [
            route for route in _write_routes(spec) if demo_readonly.is_allowed(*route.split(" ", 1))
        ]
        assert sorted(opened) == sorted(demo_readonly.ALLOWED_WRITES - UNAUTHENTICATED)

    def test_refuse_write_ignores_other_roles(self):
        """The rule is a no-op for everybody else, so it can sit on the hot
        path without every request paying for it."""
        for role in (UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER):
            demo_readonly.refuse_write(_request("DELETE", "/api/v1/admin/users"), role)


class TestPublishedKey:
    def test_the_key_reads_the_platforms_and_nothing_else(self):
        assert set(demo.DEMO_KEY_SCOPES) == {Scope.DOUYIN_READ, Scope.TIKTOK_READ}

    @pytest.mark.parametrize(
        "forbidden",
        [Scope.ADMIN, Scope.IDENTITY_MANAGE, Scope.ARCHIVE_EXPORT, Scope.MEDIA_WRITE],
    )
    def test_the_key_cannot_reach_the_dangerous_scopes(self, forbidden: Scope):
        principal = _key_principal(demo.DEMO_KEY_SCOPES)
        assert principal.permits(forbidden) is False

    def test_the_key_is_bounded_by_scopes_not_by_role(self):
        """A key is scoped even though its owner's role is not, which is what
        stops the published key from reading the request log while the demo
        console can."""
        assert _key_principal(demo.DEMO_KEY_SCOPES).scoped is True

    def test_the_session_is_not_scoped(self):
        """So the console gate is the role, and only the role."""
        assert _session_principal().scoped is False


class TestGeneratedPassword:
    def test_it_is_typeable(self):
        """Copied off a web page into a README and typed back by strangers, so
        no characters that survive neither a shell nor a phone keyboard."""
        password = demo.generate_password()
        assert password.replace("-", "").isalnum()
        assert 12 <= len(password) <= 48

    def test_it_is_not_predictable(self):
        assert len({demo.generate_password() for _ in range(50)}) > 45


def _request(method: str, path: str):
    class _Route:
        def __init__(self, p: str) -> None:
            self.path = p

    class _Request:
        def __init__(self, m: str, p: str) -> None:
            self.method = m
            self.scope = {"route": _Route(p)}

    return _Request(method, path)


def _key_principal(scopes) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        role=UserRole.DEMO,
        scopes=frozenset(scopes),
        api_key_id=uuid.uuid4(),
        rate_limit_per_min=None,
    )


def _session_principal() -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        role=UserRole.DEMO,
        scopes=frozenset(Scope),
        api_key_id=None,
        rate_limit_per_min=None,
    )
