"""Probe one proxy on demand, for the console's Test button.

Submitted by ``POST /admin/proxies/{proxy_id}/test`` with ``{"proxy_id": ...}``
and read back by ``web/src/pages/Proxies.tsx`` (``ProxyProbeResult``), so the
payload is exactly ``ok``, ``latency_ms``, ``exit_ip``, ``country``,
``timezone`` and ``detail``. ``Setup.tsx`` reads the same shape without
``timezone`` while it walks a freshly imported list; a field it ignores costs
nothing, a field it reads and never receives renders blank.

The probe is :class:`dtk.worker.proxy_prober.ProxyProber` - the same object the
five-minute sweep drives - so pressing Test has the sweep's side effects: the
row's health and GeoIP are written back, and a proxy that turns out to be dead
cools the identities behind it instead of letting them fail one at a time.
``force=True`` because someone pressing a button has already decided that the
floor between two probes does not apply to them.

Nothing here decrypts the proxy URL. That row holds a password (doc 08) and
this result is stored in ``tasks.result`` and rendered in a browser, so the
plaintext never enters this module; the one field that could quote it -
``detail``, which is whatever the probe client's exception said - is scrubbed
of credentials by the prober before it is returned.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dtk.core.errors import Internal, InvalidParam, NotFound
from dtk.core.logging import get_logger
from dtk.db.repositories import ProxyRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Probe the proxy named in ``params`` and report where it comes out."""
    prober = deps.prober
    if prober is None:
        raise Internal("this worker was started without a proxy prober")

    proxy_id = _proxy_id(params)
    if await ProxyRepository(session).get(proxy_id) is None:
        # Read here rather than left to ``probe_one``'s ``None``: a row that is
        # gone and a row that could not be probed are different answers, and a
        # console tab older than a deletion deserves the first one.
        raise NotFound("no such proxy", details={"proxy_id": str(proxy_id)})

    result = await prober.probe_one(proxy_id, force=True)
    if result is None:
        # The row exists and still nothing was measured: its URL will not
        # decrypt under the current master key, or the probe client refused the
        # scheme outright (``socks5://`` without the socks extra). Answering
        # ``ok: false`` would claim a verdict on an egress nobody reached, and
        # would sit next to a health column the prober deliberately left alone.
        log.warning("worker.ops.proxy_test.not_probed", proxy_id=str(proxy_id))
        raise Internal("the proxy could not be probed", details={"proxy_id": str(proxy_id)})

    return {
        "data": {
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "exit_ip": result.exit_ip,
            "country": result.country,
            "timezone": result.timezone,
            "detail": result.detail,
        },
        # Which proxy this was about, for a stored result read long after the
        # task's params have stopped being in front of anyone.
        "meta": {"proxy_id": str(proxy_id)},
    }


def _proxy_id(params: Mapping[str, Any]) -> uuid.UUID:
    """Read the one parameter this job takes.

    The route parses the id before it submits, so anything unusable here came
    from a hand-written task or a console old enough to disagree about the
    parameter's name. Neither is worth quoting back into the message the
    console renders.
    """
    raw = params.get("proxy_id")
    if not isinstance(raw, str) or not raw.strip():
        raise InvalidParam("proxy.test requires a proxy_id", details={"param": "proxy_id"})
    try:
        return uuid.UUID(raw.strip())
    except ValueError:
        raise InvalidParam("proxy_id is not a uuid", details={"param": "proxy_id"}) from None


__all__ = ["run"]
