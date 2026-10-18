"""Endpoints that post a form body, and endpoints that belong to one session."""

from __future__ import annotations

import pytest

from dtk.platforms.base import FORM_CONTENT_TYPE, EndpointSpec
from dtk.worker.registry import ENDPOINTS


def page(*, cursor: str = "0", count: int = 20, extra: str = "x") -> dict[str, str]:
    # Deliberately built in the opposite order to the form, which is the
    # order the page sends and the one a_bogus will hash.
    return {"cursor": cursor, "extra": extra, "count": str(count)}


POSTED = EndpointSpec(
    name="test.posted",
    path="https://example.invalid/list/",
    method="POST",
    build=page,
    form=("count", "cursor"),
)


class TestFormBody:
    def test_form_fields_leave_the_query_for_the_body_in_declared_order(self) -> None:
        spec = POSTED.to_request(cursor="17", count=10)

        assert spec["body"] == b"count=10&cursor=17"
        assert spec["params"] == {"extra": "x"}
        assert spec["headers"]["Content-Type"] == FORM_CONTENT_TYPE

    def test_a_value_is_encoded_once_here(self) -> None:
        spec = POSTED.to_request(cursor="a b&c")

        assert spec["body"] == b"count=20&cursor=a+b%26c"

    def test_an_endpoint_without_a_form_is_unchanged(self) -> None:
        plain = EndpointSpec(name="test.plain", path="https://example.invalid/x/", build=page)
        spec = plain.to_request()

        assert spec["body"] is None
        assert spec["params"] == page()
        assert "Content-Type" not in spec["headers"]

    def test_a_form_on_a_get_is_refused_where_it_is_declared(self) -> None:
        """A GET has no body to put the fields in; they would vanish."""
        with pytest.raises(ValueError, match="POST"):
            EndpointSpec(name="test.bad", path="https://example.invalid/x/", form=("cursor",))


def test_the_session_bound_endpoints_are_the_owner_only_lists() -> None:
    """Pinned here so that a new owner-only list is a decision, not a default.

    Each of these answers only the session that sends it, so unpinned it would
    return whichever account rotation drew and cache that list for everyone.
    TikTok's folder list is not among them: a guest sees an author's public
    folders, so it is an ordinary, shareable answer.
    """
    bound = {name for name, definition in ENDPOINTS.items() if definition.session_bound}

    assert bound == {
        "douyin.author_bookmarks",
        "douyin.author_collections",
        "tiktok.author_bookmarks",
    }
