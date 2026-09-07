"""The MCP server: tool surface, argument validation, blocking and error prose.

Everything here runs against fakes. The tools depend on three protocols rather
than on PostgreSQL and Redis precisely so this file can drive the blocking and
timeout paths deterministically, without a worker and without sleeping.

Two properties are load bearing enough to be asserted directly:

* the tool surface is exactly eight tools, and none of them names a credential;
* a tool that outlives its timeout returns a task id in prose rather than
  raising, because an agent handed an exception will just repeat the call.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from dtk.core.config import Config
from dtk.core.errors import TaskNotFound
from dtk.core.types import Platform, TaskState
from dtk.mcp import TOOL_NAMES, build_server, routing
from dtk.mcp.context import (
    EndpointHealth,
    HistoryPoint,
    McpContext,
    PoolSnapshot,
    TaskOutcome,
)
from dtk.mcp.server import INSTRUCTIONS
from dtk.mcp.tools import ToolSet

TASK_ID = "3f1a0f1e-0000-4000-8000-000000000001"
DOUYIN_SEC_UID = "MS4wLjABAAAAsyntheticsecuservalue"
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTasks:
    """Records submissions and hands back whatever the test scripted."""

    def __init__(
        self,
        *,
        wait: TaskOutcome | None = None,
        lookup: TaskOutcome | None = None,
        missing: bool = False,
    ) -> None:
        self.submitted: list[tuple[str, dict[str, Any]]] = []
        self._wait = wait
        self._lookup = lookup
        self._missing = missing
        self.waited: list[float] = []

    async def submit(self, endpoint: str, params: dict[str, Any]) -> str:
        self.submitted.append((endpoint, params))
        return TASK_ID

    async def wait(self, task_id: str, seconds: float) -> TaskOutcome | None:
        self.waited.append(seconds)
        return self._wait

    async def result(self, task_id: str) -> TaskOutcome:
        if self._missing:
            raise TaskNotFound("no such task, or its result has expired")
        assert self._lookup is not None
        return self._lookup


class FakePool:
    def __init__(self, *, health: EndpointHealth | None = None, active: int = 2) -> None:
        self._health = health
        self.identities = {
            Platform.DOUYIN.value: {"active": active, "cooling": 1},
            Platform.TIKTOK.value: {"active": 0},
        }
        self.endpoint_calls: list[str] = []

    async def snapshot(self) -> PoolSnapshot:
        return PoolSnapshot(
            identities=self.identities,
            endpoints=(self._health,) if self._health else (),
            observed_at=NOW,
        )

    async def endpoint(self, endpoint: str) -> EndpointHealth:
        self.endpoint_calls.append(endpoint)
        return self._health or EndpointHealth(endpoint)


class FakeHistory:
    def __init__(self, points: list[HistoryPoint] | None = None) -> None:
        self.points = points or []
        self.calls: list[tuple[Platform, str, datetime, int]] = []

    async def history(
        self, platform: Platform, content_id: str, *, since: datetime, limit: int
    ) -> list[HistoryPoint]:
        self.calls.append((platform, content_id, since, limit))
        return self.points


def make_context(
    tasks: FakeTasks | None = None,
    pool: FakePool | None = None,
    history: FakeHistory | None = None,
    *,
    timeout: int = 60,
) -> McpContext:
    config = Config({"api.mcp_tool_timeout": timeout})
    return McpContext(
        tasks=tasks or FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {"ok": True})),
        pool=pool or FakePool(),
        history=history or FakeHistory(),
        config=lambda: config,
    )


def tripped(endpoint: str = "douyin.author_posts") -> EndpointHealth:
    return EndpointHealth(
        endpoint=endpoint,
        circuit_open=True,
        retry_after_seconds=240,
        reason="risk rate 0.83 over 24 samples across 4 identities",
        total=24,
        ok=2,
        risk=20,
        # Relative to the real clock: the prose renders "2 hours ago" against
        # now, and pinning it to a fixed date would read as "6 months ago".
        last_success_at=datetime.now(UTC) - timedelta(hours=2),
    )


# ---------------------------------------------------------------------------
# Registration and schemas
# ---------------------------------------------------------------------------


class TestRegistration:
    @pytest.fixture
    def tools(self):
        server = build_server(make_context())
        return {tool.name: tool for tool in asyncio.run(server.list_tools())}

    def test_exactly_the_agreed_tool_set(self, tools):
        """Doc 06 fixes this list. More tools measurably degrade tool choice."""
        assert set(tools) == {
            "parse_url",
            "get_video",
            "get_user",
            "list_user_posts",
            "list_comments",
            "get_content_history",
            "pool_status",
            "get_task_result",
        }
        assert set(tools) == set(TOOL_NAMES)

    def test_no_tool_touches_credentials(self, tools):
        """An agent must have no way to reach identities, cookies or proxies.

        Enforced by absence rather than by a scope check: a tool that does not
        exist cannot be called by a confused model or a malicious prompt. The
        names and the argument names are what is checked - a description is free
        to *mention* the identity pool, it just may not be operable.
        """
        forbidden = (
            "identity",
            "identities",
            "cookie",
            "proxy",
            "api_key",
            "key",
            "secret",
            "mint",
            "import",
            "credential",
            "session",
        )
        names = list(tools) + [
            argument for tool in tools.values() for argument in tool.input_schema["properties"]
        ]
        for name in names:
            for word in forbidden:
                assert word not in name.lower(), f"{name!r} looks like credential access"

    def test_every_tool_is_read_only(self, tools):
        """No verb in this surface writes anything."""
        writes = ("create", "delete", "update", "set_", "add_", "remove", "retire", "test_")
        for name in tools:
            assert not name.startswith(writes)

    def test_required_and_optional_arguments(self, tools):
        assert tools["get_video"].input_schema["required"] == ["platform", "content_id"]
        assert tools["list_user_posts"].input_schema["required"] == ["platform", "uid"]
        assert tools["pool_status"].input_schema.get("required", []) == []
        paging = tools["list_comments"].input_schema["properties"]
        assert paging["cursor"]["default"] is None
        assert paging["count"]["default"] is None

    def test_every_argument_is_described(self, tools):
        """The descriptions are the whole interface an agent reads."""
        for tool in tools.values():
            assert tool.description
            for name, schema in tool.input_schema["properties"].items():
                assert schema.get("description"), f"{tool.name}.{name} has no description"

    def test_ids_are_strings_not_integers(self, tools):
        """A 19-digit aweme_id does not survive a JSON number (doc 11)."""
        assert tools["get_video"].input_schema["properties"]["content_id"]["type"] == "string"
        assert tools["get_user"].input_schema["properties"]["uid"]["type"] == "string"

    def test_instructions_point_at_the_timeout_path(self):
        assert "get_task_result" in INSTRUCTIONS
        assert "cookies" in INSTRUCTIONS


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestArgumentValidation:
    async def test_unknown_platform_lists_the_supported_ones(self):
        tools = ToolSet(make_context())
        with pytest.raises(ToolError) as caught:
            await tools.get_video("weibo", "7100000000000000000")
        assert "weibo" in str(caught.value)
        assert "INVALID_PARAM" in str(caught.value)

    async def test_blank_identifier_never_reaches_the_platform(self):
        tasks = FakeTasks()
        tools = ToolSet(make_context(tasks))
        with pytest.raises(ToolError):
            await tools.get_video("douyin", "   ")
        assert tasks.submitted == []

    @pytest.mark.parametrize("count", [0, -1, routing.MAX_COUNT + 1])
    async def test_page_size_is_bounded(self, count):
        tools = ToolSet(make_context())
        with pytest.raises(ToolError) as caught:
            await tools.list_comments("douyin", "7100000000000000000", None, count)
        assert str(routing.MAX_COUNT) in str(caught.value)

    async def test_tiktok_post_listing_rejects_a_handle_with_a_next_step(self):
        """The one identifier an agent cannot guess, so the error says what to do."""
        tools = ToolSet(make_context())
        with pytest.raises(ToolError) as caught:
            await tools.list_user_posts("tiktok", "@charlidamelio", None, None)
        message = str(caught.value)
        assert "secUid" in message
        assert "get_user" in message

    async def test_tiktok_profile_accepts_a_handle(self):
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {"uid": "123"}))
        tools = ToolSet(make_context(tasks))
        await tools.get_user("tiktok", "@charlidamelio")
        endpoint, params = tasks.submitted[0]
        assert endpoint == "tiktok.author_profile"
        assert params == {"unique_id": "charlidamelio"}

    async def test_identifier_is_translated_per_platform(self):
        """One vocabulary for the agent, each platform's own spelling below."""
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {}))
        tools = ToolSet(make_context(tasks))
        await tools.get_video("douyin", "7100000000000000000")
        await tools.get_video("tiktok", "7100000000000000000")
        assert tasks.submitted[0] == (
            "douyin.content_detail",
            {"aweme_id": "7100000000000000000"},
        )
        assert tasks.submitted[1] == ("tiktok.content_detail", {"item_id": "7100000000000000000"})

    async def test_absent_paging_arguments_are_omitted_not_zeroed(self):
        """Doc 11: an absent value is None, never 0 or an empty string."""
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {}))
        tools = ToolSet(make_context(tasks))
        await tools.list_user_posts("douyin", DOUYIN_SEC_UID, None, None)
        _endpoint, params = tasks.submitted[0]
        assert params == {"sec_user_id": DOUYIN_SEC_UID}

    async def test_cursor_and_count_are_passed_through(self):
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {}))
        tools = ToolSet(make_context(tasks))
        await tools.list_user_posts("douyin", DOUYIN_SEC_UID, "17000000000", 10)
        _endpoint, params = tasks.submitted[0]
        assert params["cursor"] == "17000000000"
        assert params["count"] == 10

    async def test_unparsable_since_is_rejected(self):
        tools = ToolSet(make_context())
        with pytest.raises(ToolError) as caught:
            await tools.get_content_history("douyin", "7100000000000000000", "last tuesday")
        assert "ISO 8601" in str(caught.value)


# ---------------------------------------------------------------------------
# URL routing
# ---------------------------------------------------------------------------


class TestParseUrl:
    async def test_a_recognized_post_url_becomes_a_detail_call(self):
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {"content_id": "7100"}))
        tools = ToolSet(make_context(tasks))
        result = await tools.parse_url("https://www.douyin.com/video/7100000000000000000")
        assert tasks.submitted[0] == (
            "douyin.content_detail",
            {"aweme_id": "7100000000000000000"},
        )
        assert result["platform"] == "douyin"
        assert result["resource"] == "video"
        assert result["data"] == {"content_id": "7100"}

    async def test_a_short_link_is_expanded_server_side(self):
        """What it points at is unknown until it is followed, and following it
        is an upstream request, so it stays one logical parse task."""
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {}))
        tools = ToolSet(make_context(tasks))
        await tools.parse_url("https://v.douyin.com/iRNBho6/")
        endpoint, params = tasks.submitted[0]
        assert endpoint == routing.PARSE_ENDPOINT
        assert params == {"url": "https://v.douyin.com/iRNBho6"}

    async def test_share_text_around_the_link_is_tolerated(self):
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {}))
        tools = ToolSet(make_context(tasks))
        await tools.parse_url("check this out https://www.douyin.com/video/7100000000000000000 !")
        assert tasks.submitted[0][0] == "douyin.content_detail"

    async def test_a_foreign_url_is_refused_without_a_request(self):
        tasks = FakeTasks()
        tools = ToolSet(make_context(tasks))
        with pytest.raises(ToolError) as caught:
            await tools.parse_url("https://example.com/watch?v=1")
        assert "INVALID_URL" in str(caught.value)
        assert "Retrying will not help" in str(caught.value)
        assert tasks.submitted == []

    async def test_an_unsupported_resource_says_what_is_covered(self):
        tools = ToolSet(make_context())
        with pytest.raises(ToolError) as caught:
            await tools.parse_url("https://live.douyin.com/123456789")
        assert "UNSUPPORTED_CONTENT" in str(caught.value)


# ---------------------------------------------------------------------------
# Blocking, timeout and task collection
# ---------------------------------------------------------------------------


class TestBlocking:
    async def test_a_tool_returns_the_answer_not_a_task_id(self):
        """The whole reason MCP tools block: an agent asked to poll gives up."""
        payload = {"content_id": "7100", "title": "a video"}
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, payload))
        tools = ToolSet(make_context(tasks))
        result = await tools.get_video("douyin", "7100000000000000000")
        assert result["status"] == "ok"
        assert result["data"] == payload

    async def test_it_waits_for_the_configured_timeout(self):
        tasks = FakeTasks(wait=TaskOutcome(TASK_ID, TaskState.DONE, {}))
        tools = ToolSet(make_context(tasks, timeout=15))
        await tools.get_video("douyin", "7100000000000000000")
        assert tasks.waited == [15.0]

    async def test_a_timeout_hands_back_prose_with_the_task_id(self):
        tasks = FakeTasks(wait=None)
        pool = FakePool(health=tripped())
        tools = ToolSet(make_context(tasks, pool, timeout=30))
        result = await tools.get_video("douyin", "7100000000000000000")

        assert result["status"] == "pending"
        assert result["task_id"] == TASK_ID
        message = result["message"]
        assert TASK_ID in message
        assert "get_task_result" in message
        assert "30 seconds" in message
        # The pool state travels with it, so the agent can judge the wait.
        assert "douyin: 2 active" in message
        assert "douyin.author_posts" in message

    async def test_a_timeout_does_not_raise(self):
        """Raising would tell the agent the call failed. It did not."""
        tools = ToolSet(make_context(FakeTasks(wait=None)))
        result = await tools.get_video("douyin", "7100000000000000000")
        assert result["status"] == "pending"

    async def test_an_empty_pool_is_named_as_the_likely_cause(self):
        tools = ToolSet(make_context(FakeTasks(wait=None), FakePool(active=0)))
        result = await tools.get_video("douyin", "7100000000000000000")
        assert "No identity is currently active" in result["message"]

    async def test_get_task_result_collects_a_finished_task(self):
        finished = TaskOutcome(TASK_ID, TaskState.DONE, {"content_id": "7100"})
        tools = ToolSet(make_context(FakeTasks(lookup=finished)))
        result = await tools.get_task_result(TASK_ID)
        assert result == {"status": "ok", "task_id": TASK_ID, "data": {"content_id": "7100"}}

    async def test_get_task_result_on_a_running_task_says_so(self):
        running = TaskOutcome(TASK_ID, TaskState.RUNNING)
        tools = ToolSet(make_context(FakeTasks(lookup=running)))
        result = await tools.get_task_result(TASK_ID)
        assert result["status"] == "pending"
        assert "running" in result["message"]

    async def test_an_expired_task_is_prose_not_a_traceback(self):
        tools = ToolSet(make_context(FakeTasks(missing=True)))
        with pytest.raises(ToolError) as caught:
            await tools.get_task_result(TASK_ID)
        assert "TASK_NOT_FOUND" in str(caught.value)


# ---------------------------------------------------------------------------
# Error prose
# ---------------------------------------------------------------------------


class TestErrorProse:
    async def test_a_tripped_endpoint_explains_itself(self):
        """The example from doc 06: an agent can act on this, on a stack trace
        it cannot."""
        failure = TaskOutcome(
            TASK_ID,
            TaskState.FAILED,
            None,
            {"code": "ENDPOINT_CIRCUIT_OPEN", "message": "", "retry_after": 240},
        )
        pool = FakePool(health=tripped())
        tools = ToolSet(make_context(FakeTasks(wait=failure), pool))

        with pytest.raises(ToolError) as caught:
            await tools.list_user_posts("douyin", DOUYIN_SEC_UID, None, None)

        message = str(caught.value)
        assert "douyin.author_posts is currently tripped" in message
        assert "last success was 2 hours ago" in message
        assert "240 seconds" in message
        assert "This is retryable" in message
        assert pool.endpoint_calls == ["douyin.author_posts"]

    async def test_a_never_working_endpoint_says_so_rather_than_guessing(self):
        health = EndpointHealth("douyin.comments", circuit_open=True, retry_after_seconds=300)
        failure = TaskOutcome(
            TASK_ID, TaskState.FAILED, None, {"code": "ENDPOINT_CIRCUIT_OPEN", "retry_after": 300}
        )
        tools = ToolSet(make_context(FakeTasks(wait=failure), FakePool(health=health)))
        with pytest.raises(ToolError) as caught:
            await tools.list_comments("douyin", "7100000000000000000", None, None)
        assert "no successful call has been recorded" in str(caught.value)

    async def test_a_permanent_failure_tells_the_agent_to_stop(self):
        """NON_RETRYABLE is the source, so this can never drift from REST."""
        failure = TaskOutcome(
            TASK_ID,
            TaskState.FAILED,
            None,
            {"code": "NOT_FOUND", "message": "the post does not exist"},
        )
        tools = ToolSet(make_context(FakeTasks(wait=failure)))
        with pytest.raises(ToolError) as caught:
            await tools.get_video("douyin", "7100000000000000000")
        message = str(caught.value)
        assert "Retrying will not help" in message
        assert "NOT_FOUND" in message

    async def test_a_parser_break_is_not_reported_as_risk_control(self):
        failure = TaskOutcome(
            TASK_ID,
            TaskState.FAILED,
            None,
            {"code": "UPSTREAM_CHANGED", "details": {"path": "aweme_detail.statistics"}},
        )
        tools = ToolSet(make_context(FakeTasks(wait=failure)))
        with pytest.raises(ToolError) as caught:
            await tools.get_video("douyin", "7100000000000000000")
        message = str(caught.value)
        assert "aweme_detail.statistics" in message
        assert "parser bug" in message

    async def test_an_unknown_error_code_still_produces_a_sentence(self):
        failure = TaskOutcome(
            TASK_ID, TaskState.FAILED, None, {"code": "WHAT_IS_THIS", "message": "odd"}
        )
        tools = ToolSet(make_context(FakeTasks(wait=failure)))
        with pytest.raises(ToolError) as caught:
            await tools.get_video("douyin", "7100000000000000000")
        assert "INTERNAL" in str(caught.value)

    async def test_health_is_not_looked_up_for_an_input_error(self):
        """A deleted video says nothing about the endpoint's health."""
        failure = TaskOutcome(TASK_ID, TaskState.FAILED, None, {"code": "NOT_FOUND"})
        pool = FakePool(health=tripped())
        tools = ToolSet(make_context(FakeTasks(wait=failure), pool))
        with pytest.raises(ToolError):
            await tools.get_video("douyin", "7100000000000000000")
        assert pool.endpoint_calls == []


# ---------------------------------------------------------------------------
# Reporting tools
# ---------------------------------------------------------------------------


class TestReporting:
    async def test_pool_status_reports_counts_and_circuits(self):
        pool = FakePool(health=tripped())
        result = await ToolSet(make_context(pool=pool)).pool_status()
        assert result["identities"]["douyin"] == {"active": 2, "cooling": 1}
        assert result["usable_identities"] == 2
        assert result["endpoints"][0]["state"] == "tripped"
        assert result["endpoints"][0]["retry_after_seconds"] == 240
        assert "douyin.author_posts" in result["summary"]

    async def test_pool_status_exposes_no_credential(self):
        """Counts, not rows. Doc 06: no endpoint returns cookies in clear text."""
        pool = FakePool(health=tripped())
        result = await ToolSet(make_context(pool=pool)).pool_status()
        rendered = repr(result).lower()
        for word in ("cookie", "proxy", "fingerprint", "sessionid", "mstoken", "identity_id"):
            assert word not in rendered

    async def test_history_defaults_to_a_bounded_window(self):
        history = FakeHistory()
        await ToolSet(make_context(history=history)).get_content_history("douyin", "7100", None)
        _platform, _content_id, since, limit = history.calls[0]
        assert since < datetime.now(UTC)
        assert limit == 500

    async def test_history_accepts_an_iso_date(self):
        history = FakeHistory()
        await ToolSet(make_context(history=history)).get_content_history(
            "douyin", "7100", "2026-01-15"
        )
        _platform, _content_id, since, _limit = history.calls[0]
        assert since == datetime(2026, 1, 15, tzinfo=UTC)

    async def test_history_keeps_missing_metrics_null(self):
        """A null is missing data; a zero is a measurement (doc 11)."""
        history = FakeHistory([HistoryPoint(ts=NOW, play_count=None, digg_count=0)])
        result = await ToolSet(make_context(history=history)).get_content_history(
            "douyin", "7100", None
        )
        point = result["points"][0]
        assert point["play_count"] is None
        assert point["digg_count"] == 0

    async def test_history_makes_no_upstream_request(self):
        tasks = FakeTasks()
        await ToolSet(make_context(tasks)).get_content_history("douyin", "7100", None)
        assert tasks.submitted == []


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


def _mounted_app(tasks: FakeTasks | None = None):
    from fastapi import FastAPI

    from dtk.mcp.http import mount

    app = FastAPI()
    app.state.config = Config.defaults()
    mount(app, context=make_context(tasks))
    return app


def _authenticate(monkeypatch) -> None:
    """Stand in for the key lookup, which needs PostgreSQL and Redis.

    The guard's own logic - header required, key required, scope required - is
    what is under test here; resolving the digest is covered where it lives.
    """
    import uuid as uuid_module

    from dtk.api.deps import Principal
    from dtk.core.types import Scope, UserRole
    from dtk.mcp import http as mcp_http

    principal = Principal(
        user_id=uuid_module.uuid4(),
        role=UserRole.OPERATOR,
        scopes=frozenset({Scope.DOUYIN_READ}),
        api_key_id=uuid_module.uuid4(),
        rate_limit_per_min=None,
    )

    async def resolve(_request):
        return principal

    async def allow(_request, resolved):
        return resolved

    monkeypatch.setattr(mcp_http, "current_principal", resolve)
    monkeypatch.setattr(mcp_http, "enforce_rate_limit", allow)


def _rpc(client, method: str, params: dict[str, Any] | None = None):
    return client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-API-Key": "dtk_test_key",
        },
    )


class TestHttpTransport:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        return TestClient(_mounted_app())

    def test_an_unauthenticated_call_is_refused_in_the_usual_envelope(self, client):
        response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "UNAUTHENTICATED"
        assert response.headers["WWW-Authenticate"].startswith("Bearer")

    def test_a_session_cookie_does_not_open_the_mcp_endpoint(self, client):
        """The cookie belongs to a browser; this endpoint is for programs."""
        from dtk.api.deps import SESSION_COOKIE

        client.cookies.set(SESSION_COOKIE, "whatever")
        response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert response.status_code == 401

    def test_an_api_key_reaches_the_protocol(self, monkeypatch):
        """End to end over the mounted transport: guard, session manager, tools.

        The session manager runs inside the sub-application's lifespan, which a
        mounted ASGI app never receives on its own - so this also covers the
        lifespan chaining in ``dtk.mcp.http``.
        """
        from fastapi.testclient import TestClient

        _authenticate(monkeypatch)
        with TestClient(_mounted_app()) as client:
            response = _rpc(
                client,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            )
        assert response.status_code == 200
        assert "dtk" in response.text
        assert "get_task_result" in response.text  # the server instructions
