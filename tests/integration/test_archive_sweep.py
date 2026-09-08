"""Availability tracking and backfill.

The single fact this file exists for: a deleted post answers the platform's own
not-found, the fetch raises, the archive write never happens, and the row keeps
saying `live` forever. Until this sweep, "which of the things I saved are gone"
had no answer at all - so the important assertions are that NOT_FOUND is
recorded as a finding, and that nothing else ever is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dtk.core.config import Config
from dtk.core.db import session_scope
from dtk.core.errors import ContentPrivate, NotFound, UpstreamRiskControl
from dtk.core.types import ContentKind, Platform
from dtk.db.models import ArchivedContent
from dtk.models.content import Author, Content, ContentStats, Media, Page
from dtk.services import archive
from dtk.worker.ops import OperationDeps, archive_sweep
from tests.integration import test_api_support as support

api_app = support.api_app

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


def content(content_id: str = "7000000000000000001") -> Content:
    author = Author(platform=Platform.DOUYIN, uid="MS4wA", nickname="someone")
    return Content(
        platform=Platform.DOUYIN,
        content_id=content_id,
        kind=ContentKind.VIDEO,
        web_url=f"https://www.douyin.com/video/{content_id}",
        title="a post",
        description="a post",
        author=author,
        stats=ContentStats(),
        media=Media(),
        fetched_at=NOW,
    )


async def seed(content_id: str, *, checked_days_ago: int | None, availability: str = "live"):
    checked = None if checked_days_ago is None else NOW - timedelta(days=checked_days_ago)
    async with session_scope() as session:
        session.add(
            ArchivedContent(
                platform="douyin",
                content_id=content_id,
                kind="video",
                web_url=f"https://www.douyin.com/video/{content_id}",
                title="a post",
                description="a post",
                author_uid="MS4wA",
                tags=[],
                availability=availability,
                availability_checked_at=checked,
                first_seen_at=NOW - timedelta(days=30),
                last_seen_at=NOW - timedelta(days=30),
            )
        )


class StubFetch:
    """Stands in for the pipeline: one scripted outcome per content id."""

    def __init__(self, outcomes: dict[str, Any]) -> None:
        self.outcomes = outcomes
        self.asked: list[str] = []
        self.cache_ttls: list[int] = []

    async def fetch(self, session, platform, endpoint, params, *, parse, cache_ttl=0, ctx=None):
        key = params.get("aweme_id") or params.get("sec_user_id") or next(iter(params.values()))
        self.asked.append(str(key))
        self.cache_ttls.append(cache_ttl)
        outcome = self.outcomes.get(str(key), "ok")
        if isinstance(outcome, Exception):
            raise outcome
        parse({})
        return None


def fast(config: Config | None = None) -> Config:
    """A config whose inter-request pause is zero.

    The pause is real and deliberately long in production - it is what keeps a
    sweep from looking like a burst from one address - but waiting it out in a
    test measures nothing.
    """
    base = (config or Config.defaults()).as_dict()
    return Config({**base, "archive.recheck_pause_seconds": 0})


def deps(fetch: Any, config: Config | None = None) -> OperationDeps:
    return OperationDeps(
        config=lambda: fast(config),
        cipher=None,  # type: ignore[arg-type]
        secret_key="x" * 40,
        pool=None,  # type: ignore[arg-type]
        transport=None,  # type: ignore[arg-type]
        signers=None,  # type: ignore[arg-type]
        fetch=fetch,  # type: ignore[arg-type]
    )


class TestAvailability:
    async def test_a_deleted_post_is_recorded_as_the_finding(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        """NOT_FOUND is what "this post is gone" looks like from the outside."""
        await seed("7000000000000000001", checked_days_ago=None)
        fetch = StubFetch({"7000000000000000001": NotFound("gone")})
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})

        assert result["checked"] == 1
        assert result["gone"] == 1
        async with session_scope() as session:
            row = await archive.get(session, "douyin", "7000000000000000001")
            assert row.availability == "deleted"
            # The record is kept, so it stays searchable and exportable with
            # the truth attached rather than disappearing.
            assert row.title == "a post"
            assert row.availability_checked_at is not None

    async def test_risk_control_never_marks_a_post_gone(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        """A risk-control response says nothing about whether the post exists,
        and reading it as deletion would quietly corrupt the archive."""
        await seed("7000000000000000002", checked_days_ago=None)
        fetch = StubFetch({"7000000000000000002": UpstreamRiskControl("blocked")})
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})

        assert result["gone"] == 0
        assert result["errors"]
        async with session_scope() as session:
            row = await archive.get(session, "douyin", "7000000000000000002")
            assert row.availability == "live"
            # Not stamped either: the check did not conclude anything, so it
            # must stay at the front of the queue rather than moving to the back.
            assert row.availability_checked_at is None

    async def test_a_private_post_is_recorded_as_private(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        await seed("7000000000000000003", checked_days_ago=None)
        fetch = StubFetch({"7000000000000000003": ContentPrivate("owner only")})
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})

        assert result["private"] == 1
        async with session_scope() as session:
            row = await archive.get(session, "douyin", "7000000000000000003")
            assert row.availability == "private"

    async def test_a_recently_checked_post_is_not_checked_again(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        await seed("7000000000000000004", checked_days_ago=1)
        fetch = StubFetch({})
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})
        assert result["checked"] == 0
        assert fetch.asked == []

    async def test_a_post_already_known_gone_is_never_re_asked(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        """A deleted post is a settled fact; re-asking forever would spend the
        pool proving something already known."""
        await seed("7000000000000000005", checked_days_ago=None, availability="deleted")
        fetch = StubFetch({})
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})
        assert result["checked"] == 0

    async def test_the_check_never_reads_the_cache(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        """A cache hit would answer with what was stored while the post was
        alive - the one answer this job must not get."""
        await seed("7000000000000000006", checked_days_ago=None)
        fetch = StubFetch({})
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            await archive_sweep.run(deps(fetch), session, {})
        assert fetch.cache_ttls == [0]

    async def test_rechecking_can_be_switched_off(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        await seed("7000000000000000007", checked_days_ago=None)
        fetch = StubFetch({})
        config = Config({**Config.defaults().as_dict(), "archive.recheck_after_days": 0})
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch, config), session, {})
        assert result["checked"] == 0
        assert "disabled" in result["detail"]


class TestBackfill:
    async def test_walks_pages_until_the_history_ends(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        pages = [
            Page(items=[content("7000000000000000011")], cursor="c1", has_more=True),
            Page(items=[content("7000000000000000021")], cursor=None, has_more=False),
        ]
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(pages), raising=True)

        fetch = StubFetch({})
        async with session_scope() as session:
            result = await archive_sweep.backfill(
                deps(fetch), session, {"platform": "douyin", "author_id": "MS4wA", "pages": 10}
            )

        assert result["pages"] == 2
        assert result["stopped"] == "end of history"
        assert result["archived"] == 2

    async def test_stops_at_the_page_ceiling(self, api_app, db_engine, redis_client, monkeypatch):
        """An author with endless history must not hold a worker forever."""
        endless = [
            Page(items=[content(f"70000000000000{index:04d}")], cursor=f"c{index}", has_more=True)
            for index in range(10)
        ]
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(endless), raising=True)

        fetch = StubFetch({})
        async with session_scope() as session:
            result = await archive_sweep.backfill(
                deps(fetch), session, {"platform": "douyin", "author_id": "MS4wA", "pages": 3}
            )
        assert result["pages"] == 3
        assert result["stopped"] == "page limit"

    async def test_a_platform_error_stops_the_walk_and_says_why(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver([]), raising=True)
        fetch = StubFetch({"MS4wA": UpstreamRiskControl("blocked")})
        async with session_scope() as session:
            result = await archive_sweep.backfill(
                deps(fetch), session, {"platform": "douyin", "author_id": "MS4wA", "pages": 5}
            )
        assert result["pages"] == 0
        assert result["stopped"] == "UPSTREAM_RISK_CONTROL"


def _resolver(pages: list[Any] | None = None):
    """A registry.resolve stand-in whose parser returns scripted models."""
    calls = {"n": 0}

    def resolve(endpoint: str, params: dict[str, Any], config: Any):
        index = calls["n"]
        calls["n"] += 1

        def parse(_payload: dict[str, Any]) -> Any:
            if pages is None:
                return content(str(params.get("content_id") or "1"))
            if index >= len(pages):
                raise AssertionError("walked past the scripted pages")
            return pages[index]

        return type(
            "ResolvedCall",
            (),
            {
                "platform": Platform.DOUYIN,
                "endpoint": endpoint,
                "params": params,
                "parse": staticmethod(parse),
                "cache_ttl": 0,
            },
        )()

    return resolve


class TestCircuit:
    async def test_an_open_circuit_stops_the_pass(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        """An open circuit is a fact about the endpoint, not about one post.

        Every remaining check would get the same answer without a request being
        made, and every row would be left unchecked anyway - measured on a live
        sweep that spent two of its five checks discovering this.
        """
        from dtk.core.errors import EndpointCircuitOpen

        for index in range(4):
            await seed(f"700000000000000010{index}", checked_days_ago=None)

        fetch = StubFetch(
            {f"700000000000000010{index}": EndpointCircuitOpen("open") for index in range(4)}
        )
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})

        assert result["checked"] == 1
        assert "stopped early" in result["detail"]
        assert len(fetch.asked) == 1

    async def test_risk_control_does_not_stop_the_pass(
        self, api_app, db_engine, redis_client, monkeypatch
    ):
        """One identity being blocked says nothing about the next one."""
        from dtk.core.errors import UpstreamRiskControl

        for index in range(3):
            await seed(f"700000000000000020{index}", checked_days_ago=None)

        fetch = StubFetch(
            {f"700000000000000020{index}": UpstreamRiskControl("blocked") for index in range(3)}
        )
        monkeypatch.setattr(archive_sweep.registry, "resolve", _resolver(), raising=True)
        async with session_scope() as session:
            result = await archive_sweep.run(deps(fetch), session, {})

        assert result["checked"] == 3
        assert result["detail"] == ""
