"""``identity.mint`` - mint one guest identity on demand.

Submitted once per identity by ``POST /admin/identities/mint`` and awaited by
the console's Identities page, which only refreshes its list afterwards and
reads no field of the result. The shape below therefore answers to the API and
the CLI, which print it::

    {"minted": true, "identity_id": "...", "platform": "douyin",
     "proxy_id": "..." | null}

The minting itself stays in :class:`dtk.worker.pool_filler.PoolFiller`. That
class holds the cross-process lock doc 02 requires - one mint at a time across
every replica - along with the proxy choice, the failure backoff and the
"refuse an unusable fingerprint" rule. A second implementation reachable from a
button would share none of that, and two mints would then run at once exactly
when someone pressed Mint while the sweep was working.

What is left for this module is the difference between the sweep and the
button. The sweep picks the neediest platform and any free egress; the operator
names a platform and sometimes an egress. And an empty tick means different
things to the two callers: the loop shrugs and tries again in a minute, while a
person who pressed Mint and got no identity is looking at a failed task.

Nothing here may carry a cookie. The result is stored in the database and
rendered in a browser, and an identity's whole value is its jar (doc 06, 08);
what comes back is the identity's id, which is all a caller can act on anyway.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final, NoReturn

from dtk.core.errors import Internal, InvalidParam, NotConfigured, NotFound, RateLimited
from dtk.core.logging import get_logger
from dtk.core.types import Platform

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps
    from dtk.worker.pool_filler import FillResult

log = get_logger(__name__)

ENDPOINT: Final = "identity.mint"

#: How long to tell a caller to wait when another mint holds the lock. A mint
#: takes seconds; the lock's own TTL is a crash guard, so quoting that instead
#: would send the operator away for five minutes after a ten-second wait.
BUSY_RETRY_SECONDS: Final = 30

#: How long this job waits for its turn at the mint lock before giving up.
#:
#: The console submits one task per identity requested, up to ten, and the setup
#: wizard asks for five. Minting is serialized by design - one at a time, across
#: replicas - so without a wait, four of those five tasks lost the race and the
#: first-run experience was one success and four red toasts. Ten mints at a few
#: seconds each fit inside this comfortably; a wait that expires means something
#: is genuinely stuck, and then "try again" is the honest answer.
MINT_WAIT_SECONDS: Final = 180.0

#: Reasons that mean "someone else is minting", not "minting is broken". The
#: request was serialized rather than refused, so it is worth trying again.
_CONTENDED: Final[frozenset[str]] = frozenset({"busy", "locked"})


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    platform = _platform(params)
    proxy_id = _proxy_id(params)

    filler = deps.filler
    if filler is None or not filler.enabled:
        # No browser container: this install lives on imported cookie jars and
        # cannot mint at all. Saying so beats a task that runs and returns
        # nothing, which reads as a mint that silently did not work.
        # NotConfigured, not Internal: an install with no browser container will
        # not grow one by being retried, and Internal is advertised as
        # retryable, so an MCP agent or a retrying client would loop on it.
        raise NotConfigured(
            "minting needs browser-rpc; set DTK_BROWSER_RPC_URL or import cookies instead",
            details={"reason": "browser_rpc_unconfigured", "platform": platform.value},
        )

    # The session handed in stays unused: the filler opens its own per mint,
    # because the periodic sweep that also calls it has none to lend.
    started = time.monotonic()
    result = await filler.top_up_once(
        platform,
        proxy_id=proxy_id,
        wait_seconds=MINT_WAIT_SECONDS,
        # A press that failed says nothing about the sweep's health, and a burst
        # of them against a down browser would drive the shared backoff to its
        # ceiling and stop automatic refill for an hour.
        shared_backoff=False,
    )
    if not result.minted:
        _fail(result, platform, proxy_id)

    return {
        "data": {
            "minted": True,
            "identity_id": str(result.identity_id) if result.identity_id else None,
            "platform": (result.platform or platform).value,
            "proxy_id": str(result.proxy_id) if result.proxy_id else None,
        },
        "meta": {
            "endpoint": ENDPOINT,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    }


def _fail(result: FillResult, platform: Platform, proxy_id: uuid.UUID | None) -> NoReturn:
    """Turn "no identity this time" into a failed task.

    The reason travels as a code in ``details`` rather than as prose: the
    sentence the caller reads is rendered at the API boundary from the error
    code alone, in their own language (doc 14), so a hand-written English
    explanation here would reach a Chinese console untranslated.
    """
    details: dict[str, Any] = {"reason": result.reason, "platform": platform.value}
    if proxy_id is not None:
        details["proxy_id"] = str(proxy_id)
    log.info("worker.ops.mint.empty", **details)

    if result.reason == "proxy_not_found":
        raise NotFound("no proxy exists with that id", details=details)
    if result.reason in _CONTENDED:
        raise RateLimited(
            "another mint is already running; identities are minted one at a time",
            retry_after=BUSY_RETRY_SECONDS,
            details=details,
        )
    raise Internal(f"minting produced no identity: {result.reason}", details=details)


def _platform(params: Mapping[str, Any]) -> Platform:
    raw = params.get("platform")
    try:
        return Platform(str(raw))
    except ValueError:
        raise InvalidParam(
            "identity.mint requires a supported platform",
            details={"platform": str(raw), "known": [p.value for p in Platform]},
        ) from None


def _proxy_id(params: Mapping[str, Any]) -> uuid.UUID | None:
    """The egress the operator picked, if any. Absent means "you choose"."""
    raw = params.get("proxy_id")
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise InvalidParam("proxy_id is not a valid uuid", details={"proxy_id": str(raw)}) from None


__all__ = ["BUSY_RETRY_SECONDS", "ENDPOINT", "MINT_WAIT_SECONDS", "run"]
