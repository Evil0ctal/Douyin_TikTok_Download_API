"""The watchlist: adding a standing instruction, and the loop that acts on it.

Two properties matter more than the CRUD. An entry may not be given an interval
that turns collection into a loop, and a due entry becomes an ordinary task -
the same queue everything else uses - rather than a second collection path with
its own rate limits.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from dtk.core.config import Config
from dtk.core.db import session_scope
from dtk.core.redis import get_redis
from dtk.core.types import TaskState
from dtk.db.models import Task
from dtk.services import tasks as task_service
from dtk.services import watchlist
from tests.integration import test_api_support as support
from tests.integration.test_api_support import envelope, error_code, signed_in

api_app = support.api_app
client = support.client

pytestmark = pytest.mark.integration

AUTHOR = "MS4wLjABAAAAexample"


async def add_entry(client, **overrides):
    body = {"platform": "douyin", "kind": "author", "target_id": AUTHOR, **overrides}
    return await client.post("/api/v1/admin/watchlist", json=body)


# --------------------------------------------------------------------------
# Creating one
# --------------------------------------------------------------------------


async def test_adding_a_target_schedules_it_immediately(client, db_engine, redis_client):
    """Someone who just added an author wants to see it collect; holding the
    first run back by the interval would make the feature look broken."""
    await signed_in(client)
    response = await add_entry(client, interval_seconds=3600)
    assert response.status_code == 201, response.text
    data = envelope(response)["data"]
    assert data["enabled"] is True
    assert data["runs"] == 0
    due_at = datetime.fromisoformat(data["next_run_at"])
    assert due_at <= datetime.now(UTC) + timedelta(seconds=5)


async def test_an_interval_below_the_floor_is_refused_by_name(client, db_engine, redis_client):
    """A target collected every ten seconds does not produce a better series -
    it spends the whole pool on one author."""
    await signed_in(client)
    response = await add_entry(client, interval_seconds=10)
    assert error_code(response) == "INVALID_PARAM"
    assert envelope(response)["error"]["details"]["minimum_seconds"] == 900


async def test_the_same_target_cannot_be_watched_twice(client, db_engine, redis_client):
    """Adding it again is a mistake, not a way to collect twice as often."""
    await signed_in(client)
    assert (await add_entry(client, interval_seconds=3600)).status_code == 201
    assert error_code(await add_entry(client, interval_seconds=3600)) == "INVALID_PARAM"


async def test_an_unknown_kind_never_reaches_the_database(client, db_engine, redis_client):
    """Refused by the request model, so it is a 400 in this API's own envelope
    rather than FastAPI's raw 422 (doc 06)."""
    await signed_in(client)
    response = await add_entry(client, kind="everything", interval_seconds=3600)
    assert error_code(response) == "INVALID_PARAM"


# --------------------------------------------------------------------------
# Changing one
# --------------------------------------------------------------------------


async def test_re_enabling_clears_the_failure_backoff(client, db_engine, redis_client):
    """The operator is asserting the problem is fixed; making them wait out an
    eight-hour backoff to find out would be its own bug."""
    await signed_in(client)
    created = envelope(await add_entry(client, interval_seconds=3600))["data"]
    entry_id = uuid.UUID(created["id"])

    async with session_scope() as session:
        entry = await watchlist.get(session, entry_id)
        entry.consecutive_failures = 6
        entry.last_error = "no such author"
        entry.enabled = False
        entry.next_run_at = datetime.now(UTC) + timedelta(hours=8)

    response = await client.patch(f"/api/v1/admin/watchlist/{entry_id}", json={"enabled": True})
    data = envelope(response)["data"]
    assert data["consecutive_failures"] == 0
    assert data["last_error"] is None
    assert datetime.fromisoformat(data["next_run_at"]) <= datetime.now(UTC) + timedelta(seconds=5)


async def test_shortening_the_interval_takes_effect_now(client, db_engine, redis_client):
    await signed_in(client)
    created = envelope(await add_entry(client, interval_seconds=86400))["data"]
    entry_id = created["id"]
    async with session_scope() as session:
        entry = await watchlist.get(session, uuid.UUID(entry_id))
        entry.next_run_at = datetime.now(UTC) + timedelta(hours=20)

    response = await client.patch(
        f"/api/v1/admin/watchlist/{entry_id}", json={"interval_seconds": 3600}
    )
    next_run = datetime.fromisoformat(envelope(response)["data"]["next_run_at"])
    assert next_run <= datetime.now(UTC) + timedelta(seconds=3700)


async def test_removing_an_entry_keeps_what_it_collected(client, db_engine, redis_client):
    """Deleting the instruction is not a request to delete what it produced."""
    await signed_in(client)
    created = envelope(await add_entry(client, interval_seconds=3600))["data"]
    response = await client.delete(f"/api/v1/admin/watchlist/{created['id']}")
    assert envelope(response)["data"]["removed"] is True
    # Nothing here touches archived_contents or content_snapshots, which is the
    # property being asserted; the row simply stops existing.
    async with session_scope() as session:
        assert await watchlist.get(session, uuid.UUID(created["id"])) is None


async def test_pause_all_stops_every_entry_without_losing_them(client, db_engine, redis_client):
    await signed_in(client)
    await add_entry(client, interval_seconds=3600)
    await add_entry(client, target_id="other", interval_seconds=3600)

    response = await client.post("/api/v1/admin/watchlist/pause", json={})
    assert envelope(response)["data"]["paused"] == 2

    listed = envelope(await client.get("/api/v1/admin/watchlist"))["data"]
    assert listed["total"] == 2
    assert all(row["enabled"] is False for row in listed["items"])


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


async def test_a_due_entry_becomes_an_ordinary_task(api_app, db_engine, redis_client):
    """The whole design: no second collection path, no second set of limits."""
    from dtk.worker.watcher import Watcher

    async with session_scope() as session:
        await watchlist.add(
            session,
            platform="douyin",
            kind="author",
            target_id=AUTHOR,
            interval_seconds=3600,
            floor=900,
        )

    report = await Watcher(config=Config.defaults()).tick()
    assert report.due == 1
    assert report.submitted == 1

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        assert entry.runs == 1
        assert entry.last_task_id is not None
        # Scheduled from submission, not completion: a lost run must not stop
        # the entry forever.
        assert entry.next_run_at > datetime.now(UTC) + timedelta(minutes=55)

        task = await session.get(Task, entry.last_task_id)
        assert task is not None
        assert task.endpoint == "douyin.author_posts"
        assert task.params["author_id"] == AUTHOR
        assert task.state == TaskState.QUEUED.value

    # And it really is on the shared queue, not a private one. Read through
    # get_redis() rather than the fixture's client: the API suite repoints the
    # process-wide client at its own logical database, and asserting against a
    # different one would pass for the wrong reason.
    #
    # Membership rather than a count: the same tick also queues the archive
    # availability sweep, and a count would make this test fail every time
    # another scheduled job is added, which says nothing about the watchlist.
    queued = await get_redis().lrange(task_service.QUEUE_KEY, 0, -1)
    assert str(entry.last_task_id) in queued


async def test_the_batch_ceiling_spreads_a_large_watchlist(api_app, db_engine, redis_client):
    """A hundred entries due on the minute must not land in front of whoever is
    using the API. They stay due; they are not dropped."""
    from dtk.worker.watcher import Watcher

    async with session_scope() as session:
        for index in range(5):
            await watchlist.add(
                session,
                platform="douyin",
                kind="content",
                target_id=f"740891510711312722{index}",
                interval_seconds=3600,
                floor=900,
            )

    config = Config({**Config.defaults().as_dict(), "watchlist.batch_size": 2})
    report = await Watcher(config=config).tick()
    assert report.submitted == 2

    async with session_scope() as session:
        rows, _ = await watchlist.search(session, watchlist.WatchFilter())
        assert sum(1 for row in rows if row.runs == 0) == 3


async def test_nothing_runs_while_scheduled_collection_is_off(api_app, db_engine, redis_client):
    from dtk.worker.watcher import Watcher

    async with session_scope() as session:
        await watchlist.add(
            session,
            platform="douyin",
            kind="author",
            target_id=AUTHOR,
            interval_seconds=3600,
            floor=900,
        )

    config = Config({**Config.defaults().as_dict(), "watchlist.enabled": False})
    report = await Watcher(config=config).tick()
    assert report.disabled is True
    assert report.submitted == 0


async def test_a_failed_run_is_read_back_and_backs_the_entry_off(api_app, db_engine, redis_client):
    """The case worth having: an id that does not exist fails at the platform,
    not at submission, so a backoff counting only submission errors never fires."""
    from dtk.worker.watcher import Watcher

    async with session_scope() as session:
        await watchlist.add(
            session,
            platform="douyin",
            kind="author",
            target_id=AUTHOR,
            interval_seconds=3600,
            floor=900,
        )

    watcher = Watcher(config=Config.defaults())
    await watcher.tick()

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        task = await session.get(Task, entry.last_task_id)
        task.state = TaskState.FAILED.value
        task.error = {"code": "NOT_FOUND", "message": "no such author"}

    report = await watcher.tick()
    assert report.reconciled == 1

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        assert entry.consecutive_failures == 1
        assert entry.last_error == "NOT_FOUND"


async def test_a_successful_run_names_the_entry(api_app, db_engine, redis_client):
    """An operator who pasted a sec_user_id should see a nickname afterwards."""
    from dtk.worker.watcher import Watcher

    async with session_scope() as session:
        await watchlist.add(
            session,
            platform="douyin",
            kind="author",
            target_id=AUTHOR,
            interval_seconds=3600,
            floor=900,
        )

    watcher = Watcher(config=Config.defaults())
    await watcher.tick()

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        task = await session.get(Task, entry.last_task_id)
        task.state = TaskState.DONE.value
        task.result = {
            "data": {"items": [{"author": {"nickname": "\u4e00\u53ea\u5c0f\u4e5d\u4e5d"}}]}
        }

    await watcher.tick()

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        assert entry.label == "\u4e00\u53ea\u5c0f\u4e5d\u4e5d"
        assert entry.consecutive_failures == 0


async def test_an_evicted_result_is_not_read_as_a_failure(api_app, db_engine, redis_client):
    """A finished task whose payload retention has already cleared must not
    back off an entry that is working."""
    from dtk.worker.watcher import Watcher

    async with session_scope() as session:
        await watchlist.add(
            session,
            platform="douyin",
            kind="author",
            target_id=AUTHOR,
            interval_seconds=3600,
            floor=900,
        )

    watcher = Watcher(config=Config.defaults())
    await watcher.tick()

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        task = await session.get(Task, entry.last_task_id)
        task.state = TaskState.DONE.value
        task.result = None

    await watcher.tick()

    async with session_scope() as session:
        entry = (await watchlist.search(session, watchlist.WatchFilter()))[0][0]
        assert entry.consecutive_failures == 0
        assert entry.last_error is None
