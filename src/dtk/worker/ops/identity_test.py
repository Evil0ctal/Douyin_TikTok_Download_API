"""Probe one identity: one real signed request, classified.

Every row on the console's Identities page has a probe button, and it answers
the question that decides what the operator does next - the platform still
talks to this identity, or it does not. It runs as a task because a real
upstream call with a real timeout is longer than a request should hold a
connection open for.

The probe changes nothing. No lease, no rate limit, and the outcome is not
recorded against the identity: a probe that cooled the identity it just probed
would move the very pool state the operator is reading, and the console would
then show a cooling identity for a reason nobody can reconstruct. It is the
same deliberate bypass ``dtk identity test`` makes, on the same code
(:mod:`dtk.ops.pipeline`, :mod:`dtk.ops.probes`).

The result is ``IdentityProbeResult`` in ``web/src/pages/Identities.tsx``::

    { ok, outcome, status, latency_ms, rule, detail }

``detail`` arrives already scrubbed from :func:`dtk.ops.probes.probe_identity`,
and it has to be: a failed signed call quotes its own URL, and that URL carries
msToken and a_bogus. What this job returns is stored in the database and
rendered in a browser, so the proxy in the metadata is masked for the same
reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dtk.core.errors import InvalidParam
from dtk.core.logging import get_logger
from dtk.ops import pipeline, probes
from dtk.ops.masking import mask_url
from dtk.worker.registry import resolve

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Issue one signed request as the named identity and report the answer."""
    identity_id = _identity_id(params)
    identity = await pipeline.pick_identity(
        session, deps.cipher, identity_id=identity_id, pool=deps.pool
    )
    # A fixed public link per platform, so the probe measures the identity
    # rather than the caller's choice of content.
    target = await pipeline.resolve_target(
        probes.SMOKE_URLS[identity.platform], proxy_url=identity.proxy_url
    )
    call = resolve(target.endpoint, target.params, deps.config())

    probe = await probes.probe_identity(
        deps.transport,
        deps.signers,
        identity,
        call,
        timeout=pipeline.REQUEST_TIMEOUT_SECONDS,
    )
    log.info(
        "ops.identity_test",
        identity_id=identity.id,
        endpoint=target.endpoint,
        ok=probe.ok,
        outcome=probe.outcome.value if probe.outcome else None,
        rule=probe.rule,
    )

    # The enveloped form rather than the bare payload: the API unwraps either,
    # but the worker's completion log reads result["meta"] unconditionally.
    return {
        "data": {
            "ok": probe.ok,
            "outcome": probe.outcome.value if probe.outcome else None,
            "status": probe.status,
            "latency_ms": probe.latency_ms,
            "rule": probe.rule,
            "detail": probe.detail,
        },
        "meta": {
            "identity_id": identity.id,
            "platform": identity.platform.value,
            "endpoint": target.endpoint,
            "proxy": mask_url(identity.proxy_url),
        },
    }


def _identity_id(params: Mapping[str, Any]) -> str:
    value = str(params.get("identity_id") or "").strip()
    if not value:
        raise InvalidParam("identity_id is required", details={"param": "identity_id"})
    return value


__all__ = ["run"]
