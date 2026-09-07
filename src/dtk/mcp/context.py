"""What the MCP tools depend on, expressed as protocols.

The tools call the services layer in the same process. They never issue an HTTP
request against our own API: that would add a hop, bypass the in-process cache
and re-authenticate a caller who is already authenticated
(docs/design/06-api-auth-mcp.md).

Everything the tools touch arrives through :class:`McpContext`, so the tool
bodies contain no wiring and a unit test can drive them against three small
fakes instead of PostgreSQL and Redis.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from dtk.core.config import Config
from dtk.core.types import Platform, TaskState


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """A finished, or at least observed, unit of work.

    ``result`` and ``error`` are mutually exclusive and both are ``None`` while
    the task is still queued or running. ``endpoint`` is what the task was
    queued as; it is what lets a result collected later be attributed to a
    platform, both for the error prose and for the scope check.
    """

    task_id: str
    state: TaskState
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    endpoint: str | None = None

    @property
    def settled(self) -> bool:
        return self.state in (TaskState.DONE, TaskState.FAILED)

    @property
    def expired(self) -> bool:
        """Finished successfully, but its payload has already been evicted.

        The task row outlives its result by ``retention.task_result_hours``.
        Reporting that as an empty success would tell an agent the post has no
        data; ``GET /api/v1/tasks/{id}`` answers TASK_NOT_FOUND for exactly this
        case, and so does the tool set.
        """
        return self.state is TaskState.DONE and self.error is None and self.result is None


class TaskGateway(Protocol):
    """Submit work and wait for it, over the same asynchronous path as REST."""

    async def submit(self, endpoint: str, params: dict[str, Any]) -> str:
        """Queue one unit of work and return its task id."""
        ...

    async def wait(self, task_id: str, seconds: float) -> TaskOutcome | None:
        """Block up to ``seconds``. ``None`` means it is still running."""
        ...

    async def result(self, task_id: str) -> TaskOutcome:
        """Look up a task without waiting. Raises ``TaskNotFound``."""
        ...


@dataclass(frozen=True, slots=True)
class EndpointHealth:
    """One endpoint's circuit state and recent record.

    ``last_success_at`` is what turns an error into advice: "tripped, and the
    last success was two hours ago" tells an agent to try something else, while
    a bare stack trace tells it nothing.
    """

    endpoint: str
    circuit_open: bool = False
    retry_after_seconds: int | None = None
    reason: str | None = None
    total: int = 0
    ok: int = 0
    risk: int = 0
    last_success_at: datetime | None = None

    @property
    def state(self) -> str:
        return "tripped" if self.circuit_open else "closed"


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    """Identity counts per platform plus the endpoint health board.

    Counts only. No cookie, no proxy address, no fingerprint and no identity id
    ever appears here: the MCP surface must not let an agent reach credentials.
    """

    identities: dict[str, dict[str, int]] = field(default_factory=dict)
    endpoints: tuple[EndpointHealth, ...] = ()
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def usable(self, platform: Platform | str | None = None) -> int:
        if platform is None:
            return sum(states.get("active", 0) for states in self.identities.values())
        return self.identities.get(str(platform), {}).get("active", 0)

    def tripped(self) -> tuple[EndpointHealth, ...]:
        return tuple(e for e in self.endpoints if e.circuit_open)

    def for_endpoint(self, endpoint: str) -> EndpointHealth | None:
        for entry in self.endpoints:
            if entry.endpoint == endpoint:
                return entry
        return None


class PoolReporter(Protocol):
    """Reads the identity pool and endpoint health for reporting only."""

    async def snapshot(self) -> PoolSnapshot: ...

    async def endpoint(self, endpoint: str) -> EndpointHealth: ...


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    ts: datetime
    play_count: int | None = None
    digg_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    collect_count: int | None = None
    follower_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "play_count": self.play_count,
            "digg_count": self.digg_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "collect_count": self.collect_count,
            "follower_count": self.follower_count,
        }


class HistoryReader(Protocol):
    """Reads recorded metric snapshots. Never performs an upstream request."""

    async def history(
        self,
        platform: Platform,
        content_id: str,
        *,
        since: datetime,
        limit: int,
    ) -> list[HistoryPoint]: ...


def _default_config() -> Config:
    return Config.defaults()


#: Decides whether one caller may read one platform. ``caller`` is whatever the
#: transport authenticated - a :class:`dtk.api.deps.Principal` over HTTP, and
#: ``None`` on stdio, where the process owner is the caller. It raises rather
#: than returning a boolean so the refusal carries the REST error code.
PlatformAuthorizer = Callable[[Platform, Any], None]


def _allow_any_platform(platform: Platform, caller: Any) -> None:
    """Default policy: no transport-level identity, so nothing to restrict.

    This is the stdio case. A local agent spawned the process and already has
    everything the process has; the HTTP transport replaces this with the same
    per-platform scope rule the REST routes enforce.
    """


@dataclass(frozen=True, slots=True)
class McpContext:
    """Everything the tool set needs, injected once at server build time.

    ``config`` is a callable rather than a snapshot because runtime settings are
    hot reloaded: reading the timeout through the callable means a change to
    ``api.mcp_tool_timeout`` takes effect without restarting the MCP server.

    ``authorize`` is per-call rather than per-server because one MCP server
    instance serves every API key that reaches the HTTP transport.
    """

    tasks: TaskGateway
    pool: PoolReporter
    history: HistoryReader
    config: Callable[[], Config] = _default_config
    authorize: PlatformAuthorizer = _allow_any_platform

    @property
    def tool_timeout(self) -> float:
        """Seconds a tool may block before falling back to a task id."""
        value = self.config().get("api.mcp_tool_timeout")
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            timeout = 60.0
        return max(1.0, timeout)


__all__ = [
    "EndpointHealth",
    "HistoryPoint",
    "HistoryReader",
    "McpContext",
    "PlatformAuthorizer",
    "PoolReporter",
    "PoolSnapshot",
    "TaskGateway",
    "TaskOutcome",
]
