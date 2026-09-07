"""The tool set.

Eight tools, and deliberately no more: every extra tool measurably lowers an
agent's selection accuracy, and this surface has to stay pickable in one shot
(docs/design/06-api-auth-mcp.md).

Nothing here manages identities, imports cookies, lists proxies or reads an API
key. An agent must not be able to touch credentials, and the way to guarantee
that is to give it no tool that could - not to check a scope inside one.

Every tool blocks. REST is asynchronous-first because a promise of an immediate
answer collapses under load, but an MCP client's model of a tool is "call it,
get an answer": handing back a task id and asking the agent to poll burns its
context, and most agents simply give up. So the work still goes through the same
asynchronous path and the waiting happens here, bounded by
``api.mcp_tool_timeout``; only when that bound is hit does the agent hear about
a task id.
"""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from pydantic import Field

from dtk.core.errors import DtkError, ErrorCode, InvalidParam, InvalidUrl, TaskNotFound
from dtk.core.logging import get_logger
from dtk.core.types import Platform, TaskState
from dtk.mcp import prose, routing
from dtk.mcp.context import EndpointHealth, McpContext, PoolSnapshot, TaskOutcome
from dtk.urls import first_url, identify

log = get_logger(__name__)

#: Failures whose explanation is incomplete without the endpoint's health: an
#: agent needs to know whether the endpoint is tripped and when it last worked.
_HEALTH_CODES: Final[frozenset[ErrorCode]] = frozenset(
    {
        ErrorCode.ENDPOINT_CIRCUIT_OPEN,
        ErrorCode.IDENTITY_POOL_EXHAUSTED,
        ErrorCode.UPSTREAM_RISK_CONTROL,
        ErrorCode.SIGNING_FAILED,
        ErrorCode.INTERNAL,
    }
)

#: Default window for a metric history query.
HISTORY_DEFAULT_DAYS: Final = 30
#: Ceiling on returned points. A year of five-minute snapshots would drown the
#: agent's context for no gain.
HISTORY_MAX_POINTS: Final = 500

PlatformArg = Annotated[
    str,
    Field(description="Platform to query: 'douyin' or 'tiktok'."),
]
CursorArg = Annotated[
    str | None,
    Field(
        default=None,
        description=(
            "Opaque cursor from the previous page's 'cursor' field. Omit for the first page; "
            "pass it back unchanged, never parse it."
        ),
    ),
]
CountArg = Annotated[
    int | None,
    Field(
        default=None,
        description=(
            f"Items per page, {routing.MIN_COUNT}-{routing.MAX_COUNT}. "
            "Omit to use the platform's own default."
        ),
    ),
]


class ToolSet:
    """The tool implementations, bound to one :class:`McpContext`.

    Methods are registered with the MCP server as-is, so their signatures and
    docstrings are the schema and the description an agent sees. The trailing
    ``context`` parameter is the SDK's own handle on the call; it is excluded
    from the schema an agent sees, and it is how a tool reaches the caller the
    transport authenticated.
    """

    def __init__(self, context: McpContext) -> None:
        self._ctx = context

    # -- shared plumbing ---------------------------------------------------

    def _authorize(self, platform: Platform, context: Context | None) -> None:
        """Refuse a platform this caller's key was not granted.

        The transport guard can only check that a key has *some* read scope: the
        platform is inside the JSON-RPC body, not in the headers. Without this
        second check a key scoped to one platform would read the other through
        MCP, which ``/api/v1/{platform}/...`` refuses.
        """
        self._ctx.authorize(platform, _caller(context))

    async def _health(self, endpoint: str | None) -> EndpointHealth | None:
        if not endpoint:
            return None
        try:
            return await self._ctx.pool.endpoint(endpoint)
        except Exception as exc:  # reporting must never mask the failure it explains
            log.warning("mcp.health_lookup_failed", endpoint=endpoint, error=repr(exc))
            return None

    async def _snapshot(self) -> PoolSnapshot | None:
        try:
            return await self._ctx.pool.snapshot()
        except Exception as exc:  # reporting must never mask the failure it explains
            log.warning("mcp.pool_snapshot_failed", error=repr(exc))
            return None

    async def _guarded(self, work: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        """Convert any failure into prose the agent can act on.

        A domain error carries its own code and advice. Anything else is a bug
        or an outage: the SDK would hand the agent a bare "Error executing tool
        X" with nothing to decide on, so it becomes an INTERNAL sentence here.
        The exception itself is logged rather than sent - it may name a host, a
        query or a header, and none of that belongs in a model's context.
        """
        try:
            return await work
        except DtkError as exc:
            endpoint = exc.details.get("endpoint") if exc.details else None
            health = await self._health(endpoint if exc.code in _HEALTH_CODES else None)
            raise ToolError(prose.describe_error(exc, endpoint=endpoint, health=health)) from None
        except (ToolError, MCPError):
            # ToolError already carries prose; MCPError is a protocol-level
            # message the kernel has to answer itself, not a tool failure.
            raise
        except Exception as exc:
            log.exception("mcp.tool_crashed", error=repr(exc))
            raise ToolError(
                prose.describe(
                    ErrorCode.INTERNAL,
                    "the request failed inside dtk rather than at the platform",
                )
            ) from None

    async def _settle(self, outcome: TaskOutcome, endpoint: str) -> dict[str, Any]:
        """Unwrap a finished task, or raise its failure as prose."""
        where = outcome.endpoint or endpoint
        if outcome.state is TaskState.FAILED or outcome.error is not None:
            code_text = str((outcome.error or {}).get("code") or ErrorCode.INTERNAL.value)
            try:
                code = ErrorCode(code_text)
            except ValueError:
                code = ErrorCode.INTERNAL
            health = await self._health(where if code in _HEALTH_CODES else None)
            raise ToolError(prose.describe_task_error(outcome.error, endpoint=where, health=health))
        if outcome.expired:
            # An empty success would read as "this post has no data".
            raise ToolError(prose.expired_result_message(outcome.task_id, where or None))
        return outcome.result or {}

    async def _run(
        self, endpoint: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, float]:
        """Submit, then wait.

        Returns ``(payload, task_id, timeout)``; payload is ``None`` only when
        the wait timed out and the work is still going. The timeout travels back
        with it so the message quotes the wait that actually happened, not the
        one a hot reload installed in the meantime.
        """
        task_id = await self._ctx.tasks.submit(endpoint, params)
        timeout = self._ctx.tool_timeout
        outcome = await self._ctx.tasks.wait(task_id, timeout)
        if outcome is None or not outcome.settled:
            log.info("mcp.tool_timeout", endpoint=endpoint, task_id=task_id, timeout=timeout)
            return None, task_id, timeout
        return await self._settle(outcome, endpoint), task_id, timeout

    async def _pending(self, task_id: str, endpoint: str, timeout: float) -> dict[str, Any]:
        snapshot = await self._snapshot()
        return {
            "status": "pending",
            "task_id": task_id,
            "message": prose.timeout_message(task_id, timeout, snapshot, endpoint=endpoint),
        }

    async def _fetch(
        self, endpoint: str, params: dict[str, Any], extra: dict[str, Any]
    ) -> dict[str, Any]:
        payload, task_id, timeout = await self._run(endpoint, params)
        if payload is None:
            return await self._pending(task_id, endpoint, timeout)
        data, meta = _unwrap(payload)
        answer = {"status": "ok", "task_id": task_id, **extra, "data": data}
        if meta.get("cached") is not None:
            # Worth one word: a cached answer may be minutes old, and an agent
            # comparing two calls should know which one cost a real request.
            answer["cached"] = bool(meta["cached"])
        return answer

    # -- tools -------------------------------------------------------------

    async def parse_url(
        self,
        url: Annotated[
            str,
            Field(
                description=(
                    "A Douyin or TikTok link, or the share text containing one. Short links "
                    "(v.douyin.com, vm.tiktok.com) are followed automatically."
                )
            ),
        ],
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Fetch whatever a Douyin or TikTok link points at.

        The entry point to use when you have a link rather than an id: it works
        out the platform and the resource type itself and returns the normalized
        post or author. Short links are expanded server-side.
        """
        return await self._guarded(self._parse_url(url, context))

    async def _parse_url(self, url: str, context: Context | None = None) -> dict[str, Any]:
        candidate = first_url(url or "")
        if candidate is None:
            raise InvalidUrl(
                "no URL was found in the input",
                details={"reason": "no_url"},
            )
        kind = identify(candidate)
        if not kind.allowed or kind.platform is None:
            raise InvalidUrl(
                "that link is not a supported Douyin or TikTok URL",
                details={"reason": "host_not_allowed"},
            )
        self._authorize(kind.platform, context)

        endpoint, params = routing.route_url(kind)
        return await self._fetch(
            endpoint,
            params,
            {
                "platform": kind.platform.value,
                "resource": kind.resource.value,
                "url": kind.url,
            },
        )

    async def get_video(
        self,
        platform: PlatformArg,
        content_id: Annotated[
            str,
            Field(
                description=(
                    "The post id as a string: Douyin aweme_id, TikTok item id. Always a "
                    "string - these ids exceed the JavaScript safe integer range."
                )
            ),
        ],
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Get one post (video or image album) by its id.

        Returns the normalized post: author, stats, media URLs and tags. Use
        parse_url instead when you have a link rather than an id.
        """
        return await self._guarded(self._get_video(platform, content_id, context))

    async def _get_video(
        self, platform: str, content_id: str, context: Context | None = None
    ) -> dict[str, Any]:
        target = routing.coerce_platform(platform)
        self._authorize(target, context)
        identifier = routing.require_text(content_id, "content_id")
        endpoint, params = routing.build_task(target, routing.Capability.CONTENT_DETAIL, identifier)
        return await self._fetch(
            endpoint, params, {"platform": target.value, "content_id": identifier}
        )

    async def get_user(
        self,
        platform: PlatformArg,
        uid: Annotated[
            str,
            Field(
                description=(
                    "Douyin: the sec_user_id (starts with 'MS4wLjAB'). TikTok: the secUid, or "
                    "the @handle if that is all you have."
                )
            ),
        ],
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Get an author profile.

        The returned 'uid' is the stable key for that author; pass it to
        list_user_posts. A TikTok @handle can change, so prefer the uid from
        this result over the handle for any follow-up call.
        """
        return await self._guarded(self._get_user(platform, uid, context))

    async def _get_user(
        self, platform: str, uid: str, context: Context | None = None
    ) -> dict[str, Any]:
        target = routing.coerce_platform(platform)
        self._authorize(target, context)
        identifier = routing.require_text(uid, "uid")
        endpoint, params = routing.build_task(target, routing.Capability.AUTHOR_PROFILE, identifier)
        return await self._fetch(endpoint, params, {"platform": target.value, "uid": identifier})

    async def list_user_posts(
        self,
        platform: PlatformArg,
        uid: Annotated[
            str,
            Field(
                description=(
                    "The author's stable id: Douyin sec_user_id, TikTok secUid. A TikTok "
                    "@handle is not accepted here - call get_user first to obtain the uid."
                )
            ),
        ],
        cursor: CursorArg = None,
        count: CountArg = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """List an author's posts, one page at a time.

        The result carries 'cursor' and 'has_more'; pass the cursor back
        unchanged for the next page. Stop when 'has_more' is false.
        """
        return await self._guarded(self._list_user_posts(platform, uid, cursor, count, context))

    async def _list_user_posts(
        self,
        platform: str,
        uid: str,
        cursor: str | None,
        count: int | None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        target = routing.coerce_platform(platform)
        self._authorize(target, context)
        identifier = routing.require_text(uid, "uid")
        endpoint, params = routing.build_task(
            target, routing.Capability.AUTHOR_POSTS, identifier, cursor=cursor, count=count
        )
        return await self._fetch(endpoint, params, {"platform": target.value, "uid": identifier})

    async def list_comments(
        self,
        platform: PlatformArg,
        content_id: Annotated[
            str, Field(description="The post id whose top-level comments you want.")
        ],
        cursor: CursorArg = None,
        count: CountArg = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """List the top-level comments on a post, one page at a time.

        The result carries 'cursor' and 'has_more'; pass the cursor back
        unchanged for the next page.
        """
        return await self._guarded(
            self._list_comments(platform, content_id, cursor, count, context)
        )

    async def _list_comments(
        self,
        platform: str,
        content_id: str,
        cursor: str | None,
        count: int | None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        target = routing.coerce_platform(platform)
        self._authorize(target, context)
        identifier = routing.require_text(content_id, "content_id")
        endpoint, params = routing.build_task(
            target, routing.Capability.COMMENTS, identifier, cursor=cursor, count=count
        )
        return await self._fetch(
            endpoint, params, {"platform": target.value, "content_id": identifier}
        )

    async def get_content_history(
        self,
        platform: PlatformArg,
        content_id: Annotated[
            str,
            Field(
                description=(
                    "A post id, or an author id for follower history. Only ids this instance "
                    "has fetched before have any history."
                )
            ),
        ],
        since: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "ISO 8601 date or timestamp, for example '2026-01-15' or "
                    f"'2026-01-15T00:00:00Z'. Defaults to the last {HISTORY_DEFAULT_DAYS} days."
                ),
            ),
        ] = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Read the recorded metric history for one post or author.

        Answers "how did this grow" from snapshots already stored locally: it
        makes no upstream request and costs no identity quota. Metrics the
        platform did not report are null, never zero - a null is missing data,
        a zero is a real measurement. Points come back oldest first; when there
        are more than fit, the most recent ones are kept and 'truncated' is
        true, so widen 'since' only if you need the earlier part of the curve.
        """
        return await self._guarded(self._get_content_history(platform, content_id, since, context))

    async def _get_content_history(
        self, platform: str, content_id: str, since: str | None, context: Context | None = None
    ) -> dict[str, Any]:
        target = routing.coerce_platform(platform)
        self._authorize(target, context)
        identifier = routing.require_text(content_id, "content_id")
        start = _parse_since(since)
        points = await self._ctx.history.history(
            target, identifier, since=start, limit=HISTORY_MAX_POINTS
        )
        return {
            "status": "ok",
            "platform": target.value,
            "content_id": identifier,
            "since": start.isoformat(),
            "point_count": len(points),
            "truncated": len(points) >= HISTORY_MAX_POINTS,
            "points": [point.as_dict() for point in points],
        }

    async def pool_status(self) -> dict[str, Any]:
        """Report identity-pool and endpoint health.

        Call this when a request failed or timed out and you need to decide
        whether to wait, to try a different platform, or to stop. It reports
        counts and circuit states only; no credential is ever exposed.
        """
        return await self._guarded(self._pool_status())

    async def _pool_status(self) -> dict[str, Any]:
        snapshot = await self._ctx.pool.snapshot()
        return {
            "status": "ok",
            "observed_at": snapshot.observed_at.isoformat(),
            "identities": {
                platform: dict(states) for platform, states in snapshot.identities.items()
            },
            "usable_identities": snapshot.usable(),
            "endpoints": [
                {
                    "endpoint": entry.endpoint,
                    "state": entry.state,
                    "retry_after_seconds": entry.retry_after_seconds,
                    "reason": entry.reason,
                    "requests_recent": entry.total,
                    "ok_recent": entry.ok,
                    "risk_control_recent": entry.risk,
                    "last_success_at": (
                        entry.last_success_at.isoformat() if entry.last_success_at else None
                    ),
                }
                for entry in snapshot.endpoints
            ],
            "summary": prose.summarize_pool(snapshot),
        }

    async def get_task_result(
        self,
        task_id: Annotated[
            str,
            Field(
                description=(
                    "The task id from a tool call that reported status 'pending'. There is no "
                    "other reason to call this tool."
                )
            ),
        ],
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Collect the result of a call that had not finished in time.

        Only useful after another tool returned status 'pending' with a task id.
        Never start here: every other tool already waits for its own answer.
        """
        return await self._guarded(self._get_task_result(task_id, context))

    async def _get_task_result(
        self, task_id: str, context: Context | None = None
    ) -> dict[str, Any]:
        identifier = routing.require_text(task_id, "task_id")
        try:
            outcome = await self._ctx.tasks.result(identifier)
        except TaskNotFound as exc:
            raise ToolError(prose.describe_error(exc)) from None

        # A task id is not a capability: the same scope rule that guards the
        # tool which queued the work has to guard collecting its result, or a
        # key scoped to one platform reads the other one's data by task id.
        platform = _platform_of(outcome.endpoint)
        if platform is not None:
            self._authorize(platform, context)

        if not outcome.settled:
            snapshot = await self._snapshot()
            return {
                "status": "pending",
                "task_id": identifier,
                "message": prose.unfinished_task_message(identifier, outcome.state.value, snapshot),
            }
        payload = await self._settle(outcome, "")
        data, _meta = _unwrap(payload)
        return {"status": "ok", "task_id": identifier, "data": data}


def _caller(context: Context | None) -> Any | None:
    """The principal the transport authenticated, when there is one.

    Over streamable-http the SDK carries the Starlette request on the call
    context, and :class:`dtk.mcp.http.ApiKeyGuard` has already put the resolved
    principal on its state. On stdio there is no request and no principal, and
    the authorizer treats that as the local process owner.
    """
    if context is None:
        return None
    try:
        request = context.request_context.request
    except (AttributeError, ValueError):  # no request context on this transport
        return None
    return getattr(getattr(request, "state", None), "principal", None)


def _platform_of(endpoint: str | None) -> Platform | None:
    """The platform an endpoint name belongs to, if it names one.

    ``douyin.author_posts`` does; the logical ``parse`` endpoint does not,
    because which platform it touches is only settled once the URL is followed.
    """
    if not endpoint or "." not in endpoint:
        return None
    try:
        return Platform(endpoint.split(".", 1)[0])
    except ValueError:
        return None


def _unwrap(payload: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Separate the model from the task envelope.

    The worker stores ``{"data": ..., "meta": {...}}``. An agent wants the model;
    handing it a result nested one level deeper than the schema describes is a
    reliable way to make it give up. A payload without that shape is passed
    through unchanged.
    """
    if isinstance(payload, dict) and "data" in payload:
        meta = payload.get("meta")
        return payload["data"], meta if isinstance(meta, dict) else {}
    return payload, {}


def _parse_since(since: str | None) -> datetime:
    if since is None or not since.strip():
        return datetime.now(UTC) - timedelta(days=HISTORY_DEFAULT_DAYS)
    text = since.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise InvalidParam(
            "since must be an ISO 8601 date or timestamp, for example '2026-01-15'",
            details={"field": "since", "value": since},
        ) from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


#: Tool name -> the :class:`ToolSet` method implementing it. The registry is
#: explicit so a method added to the class does not silently become a tool.
TOOL_METHODS: Final[tuple[str, ...]] = (
    "parse_url",
    "get_video",
    "get_user",
    "list_user_posts",
    "list_comments",
    "get_content_history",
    "pool_status",
    "get_task_result",
)


__all__ = [
    "HISTORY_DEFAULT_DAYS",
    "HISTORY_MAX_POINTS",
    "TOOL_METHODS",
    "ToolSet",
]
