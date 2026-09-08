"""Console-triggered maintenance jobs.

Seven operations reach the worker that are not platform reads: running the self
check, taking a backup, restoring one, minting an identity, probing an identity
or a proxy, and sending a test alert. The console submits each as an ordinary
task, because each can take longer than a request should hold a connection
open.

They do not belong in :mod:`dtk.worker.registry`. That table describes upstream
endpoints - a platform, a signature, a token bucket, a parse - and none of that
applies here. Routing them through it is what left every one of these jobs
failing with "unknown endpoint": the table was the only dispatch the worker had,
and a job it did not know was a job that could not run.

So dispatch is split in two. ``registry`` answers "which upstream call is this",
and this package answers "which local job is this". :class:`OperationRunner` is
the seam, and :class:`TaskWorker` asks it first.

Each job lives in its own module and exposes one coroutine::

    async def run(deps: OperationDeps, session: AsyncSession,
                  params: Mapping[str, Any]) -> dict[str, Any]

The return value is stored as the task result and read by the console, so its
shape is a contract with a specific page - each module names the page and the
TypeScript interface it has to satisfy.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger

if TYPE_CHECKING:  # imported lazily so worker startup stays light
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.core.config import Config
    from dtk.core.crypto import Cipher
    from dtk.identity.minting import BrowserRpcClient
    from dtk.identity.pool import IdentityPool
    from dtk.ops.notify import Notifier
    from dtk.signing.registry import SignerRegistry
    from dtk.transport.base import Transport
    from dtk.worker.pool_filler import PoolFiller
    from dtk.worker.proxy_prober import ProxyProber

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OperationDeps:
    """The long-lived collaborators a maintenance job may use.

    Built once, with the worker, from the same objects the background loops
    already share - one identity pool, one transport, one browser-rpc client.
    Building a second set here would double the connection pools and let a job
    observe a different pool state than the loop that maintains it.

    The optional members are optional in deployment, not in principle: a install
    with no browser-rpc has no minting, and one with no channels configured has
    no notifier. A job whose dependency is absent must say so rather than crash.
    """

    config: Callable[[], Config]
    cipher: Cipher
    #: The master key this process encrypts with, not whatever the environment
    #: says right now. A backup's manifest fingerprints the key so a restore can
    #: refuse an archive it cannot decrypt; re-reading the environment at task
    #: time would let the archive be written under the worker's key and stamped
    #: with a different one, and the mismatch would surface at restore, which is
    #: the worst possible moment to discover it.
    secret_key: str
    pool: IdentityPool
    transport: Transport
    signers: SignerRegistry
    filler: PoolFiller | None = None
    prober: ProxyProber | None = None
    notifier: Notifier | None = None
    rpc: BrowserRpcClient | None = None


#: One maintenance job. Takes its dependencies, a session scoped to this task,
#: and the parameters the route submitted; returns the stored task result.
OperationHandler = Callable[
    [OperationDeps, "AsyncSession", Mapping[str, Any]], Awaitable[dict[str, Any]]
]


def _handlers() -> Mapping[str, OperationHandler]:
    """The dispatch table, imported lazily to keep the import graph shallow."""
    from dtk.worker.ops import (
        backup,
        diagnose,
        identity_mint,
        identity_test,
        notify_test,
        proxy_test,
        restore,
    )

    return MappingProxyType(
        {
            "diagnose": diagnose.run,
            "backup": backup.run,
            "backup.restore": restore.run,
            "identity.mint": identity_mint.run,
            "identity.test": identity_test.run,
            "proxy.test": proxy_test.run,
            "notify.test": notify_test.run,
        }
    )


#: Every endpoint this package handles. Named separately from the table so the
#: worker can answer "is this mine?" without importing every module, and so a
#: test can assert this set against the Maintenance enum the routes submit.
ENDPOINTS: Final[frozenset[str]] = frozenset(
    {
        "diagnose",
        "backup",
        "backup.restore",
        "identity.mint",
        "identity.test",
        "proxy.test",
        "notify.test",
    }
)


def handles(endpoint: str) -> bool:
    return endpoint in ENDPOINTS


class OperationRunner:
    """Runs one maintenance job, in a session of its own."""

    def __init__(self, deps: OperationDeps, session_factory: Any) -> None:
        self._deps = deps
        self._session_factory = session_factory

    def handles(self, endpoint: str) -> bool:
        return handles(endpoint)

    async def run(self, endpoint: str, params: Mapping[str, Any]) -> dict[str, Any]:
        handler = _handlers().get(endpoint)
        if handler is None:
            # Reachable only if ENDPOINTS and the table disagree, which the
            # coverage test forbids. Raising the same error the registry raises
            # keeps one story for the caller.
            raise InvalidParam(
                f"unknown endpoint: {endpoint}",
                details={"endpoint": endpoint, "known": sorted(ENDPOINTS)},
            )
        async with self._session_factory() as session:
            result = await handler(self._deps, session, params)
            # Handlers write rows - a probe outcome, a minted identity, an audit
            # of what a backup contained - and the session scope would roll them
            # back on the way out.
            await session.commit()
        return result


__all__ = [
    "ENDPOINTS",
    "OperationDeps",
    "OperationHandler",
    "OperationRunner",
    "handles",
]
