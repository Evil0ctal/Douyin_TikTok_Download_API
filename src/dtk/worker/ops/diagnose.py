"""Run the six-step self check.

Submitted by ``POST /admin/diagnose`` with ``{"include_smoke_test": bool}`` and
read by ``web/src/pages/Diagnose.tsx``, interface ``DiagnosticReport``:
``version``, ``started_at``, ``finished_at``, ``passed``, ``text`` and a
``steps`` array of ``ReportStep``. :meth:`dtk.ops.diagnose.DiagnosticReport.as_dict`
already produces exactly that, so the report is stored as it comes.

The check itself is :func:`dtk.ops.diagnose.run_diagnostics`, the same call
``dtk diagnose`` makes. What this module supplies is the half the shared
implementation cannot know: which live objects to probe, and how to run one
link through the whole pipeline. Both come from the worker's own collaborators
rather than from new ones - a second signing stack would open a second wreq
client and a second browser-rpc connection for one request, and a second
identity pool would survey a pool the loops do not maintain.

**The prose is not rendered here.** A step reports a :class:`StepCode` plus its
arguments, and the sentence is built from them per reader. The worker has no
reader: the task is fetched later, by whoever asks, in whatever language they
negotiated. So the report is stored with codes and arguments intact and
:func:`dtk.ops.diagnose.localize_report` re-renders it at the point where a
language is finally known (docs/design/14-i18n.md).

**Nothing here needs redacting.** The report masks proxy passwords, cookies,
signature parameters and API keys as it is built, because its whole purpose is
to be pasted into a public issue - and a task result is stored in the database
and rendered in a browser, which is the same exposure.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from dtk.core.db import get_engine
from dtk.core.redis import get_redis
from dtk.core.types import Platform
from dtk.ops import pipeline
from dtk.ops.diagnose import STEP_TIMEOUT_SECONDS, DiagnoseContext, SmokeRunner, run_diagnostics
from dtk.ops.probes import SMOKE_URLS

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from dtk.worker.ops import OperationDeps

#: Platform the end-to-end step exercises. One is enough to answer "does a
#: request still come back", and the console offers no choice; the CLI takes
#: ``--platform`` for the operator who needs the other one.
SMOKE_PLATFORM = Platform.DOUYIN


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Probe every dependency and report what each one said."""
    include_smoke = bool(params.get("include_smoke_test", True))
    config = deps.config()

    engine: AsyncEngine | None = None
    redis: Redis | None = None
    # A self check that dies because a handle it exists to report on is missing
    # is the least useful failure this job could produce. Absent, the components
    # step relays the same "not initialised" as a broken component.
    with contextlib.suppress(RuntimeError):
        engine = get_engine()
    with contextlib.suppress(RuntimeError):
        redis = get_redis()

    smoke: SmokeRunner | None = None
    if include_smoke:
        smoke = partial(
            pipeline.smoke,
            session=session,
            cipher=deps.cipher,
            config=config,
            transport=deps.transport,
            signers=deps.signers,
        )

    async with httpx.AsyncClient(timeout=STEP_TIMEOUT_SECONDS, follow_redirects=True) as http:
        report = await run_diagnostics(
            DiagnoseContext(
                session=session,
                engine=engine,
                redis=redis,
                # Unconfigured browser-rpc is absent rather than broken: the
                # signing step must skip on it, not fail on it.
                rpc=deps.rpc if deps.rpc is not None and deps.rpc.configured else None,
                registry=deps.signers,
                cipher=deps.cipher,
                http=http,
                smoke=smoke,
                smoke_url=SMOKE_URLS[SMOKE_PLATFORM] if include_smoke else None,
                min_active=int(config.get("pool.min_size")),
            )
        )

    return {
        "data": report.as_dict(),
        "meta": {
            "endpoint": "diagnose",
            "passed": report.passed,
            "verdict": report.verdict,
            "failures": len(report.failures),
            "warnings": len(report.warnings),
            "smoke_test": include_smoke,
            "duration_ms": round(
                (report.finished_at - report.started_at).total_seconds() * 1000, 2
            ),
        },
    }


__all__ = ["SMOKE_PLATFORM", "run"]
