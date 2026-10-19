"""Whether the optional sidecars answer: the media downloader and the transcriber.

Both live behind a compose profile (``downloader``, ``transcriber``), so "not
there" is a normal state of a correct deployment and is reported as
``configured: false`` rather than as a fault - the same distinction the
browser-rpc row has always made. What was missing until now was the other
half: an operator who *had* started the profile had no row anywhere saying
whether the container answered, and found out from a 503 on the first download.

The answer is the shape ``GET /system/status`` already uses for
``browser_rpc`` - ``configured``, ``ok``, ``latency_ms``, ``detail_code`` - so
the System page and the self check render all three the same way. Only codes
leave this module. The downloader's and transcriber's own wording can carry a
volume path or an internal address, and a status row has no use for either.

Both callers bound the probe, because both are polled or waited on by a person:
the System page re-reads the status every few seconds, and a self check that
hangs on a wedged sidecar is a self check nobody finishes reading.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final, Protocol

from dtk.core.logging import get_logger

log = get_logger(__name__)

DOWNLOADER: Final = "downloader"
TRANSCRIBER: Final = "transcriber"

#: Longer than browser-rpc's one second on purpose. The downloader's /health
#: measures the media volume as it answers, and a library of a few thousand
#: posts takes longer than a second to walk; reporting that as "down" would be
#: the false alarm this row exists to avoid.
SIDECAR_PROBE_TIMEOUT_SECONDS: Final = 2.5

#: Why a configured sidecar is not ``ok``. Stable identifiers the console
#: translates; nothing the sidecar said travels with them.
DETAIL_UNREACHABLE: Final = "unreachable"
DETAIL_TIMEOUT: Final = "timeout"
DETAIL_DEGRADED: Final = "degraded"


class DownloaderProbe(Protocol):
    """The part of :class:`dtk.media.client.DownloaderClient` a probe reads."""

    @property
    def configured(self) -> bool: ...

    async def health(self) -> Any: ...


class TranscriberProbe(Protocol):
    """The part of :class:`dtk.transcriber.TranscriberClient` a probe reads."""

    @property
    def configured(self) -> bool: ...

    async def health(self) -> dict[str, Any]: ...


def not_configured() -> dict[str, Any]:
    """The profile is not running here. Absent, not broken."""
    return {"configured": False, "ok": None}


def _down(code: str) -> dict[str, Any]:
    return {"configured": True, "ok": False, "detail_code": code}


def _answered(started: float, *, healthy: bool) -> dict[str, Any]:
    return {
        "configured": True,
        "ok": healthy,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "detail_code": None if healthy else DETAIL_DEGRADED,
    }


async def downloader_status(
    client: DownloaderProbe | None, *, timeout: float = SIDECAR_PROBE_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Probe the media downloader's ``/health``."""
    if client is None or not client.configured:
        return not_configured()
    started = time.perf_counter()
    try:
        health = await asyncio.wait_for(client.health(), timeout)
    except TimeoutError:
        return _down(DETAIL_TIMEOUT)
    except Exception as exc:
        # health() already folds a refused connection into `available=False`;
        # anything that still escapes is a body it could not read. Named by
        # type only - the message quotes the sidecar's address.
        log.warning("ops.sidecar.unreadable", component=DOWNLOADER, error=type(exc).__name__)
        return _down(DETAIL_UNREACHABLE)
    if not getattr(health, "available", False):
        # The Go service answers "ok" whenever it answers at all, so a health
        # that is not available is one that did not answer, or refused the
        # token - either way the jobs submitted to it would not run.
        return _down(DETAIL_UNREACHABLE)
    return _answered(started, healthy=True)


async def transcriber_status(
    client: TranscriberProbe | None, *, timeout: float = SIDECAR_PROBE_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Probe the transcriber's ``/health``.

    Unlike the downloader it can answer and still not be able to work: the
    model loads after the HTTP server starts, and a missing ffmpeg binary is
    reported as ``degraded`` rather than by refusing to start. That is a
    configured, answering, *unhealthy* sidecar, and the row says so.
    """
    if client is None or not client.configured:
        return not_configured()
    started = time.perf_counter()
    try:
        body = await asyncio.wait_for(client.health(), timeout)
    except TimeoutError:
        return _down(DETAIL_TIMEOUT)
    except Exception as exc:
        log.warning("ops.sidecar.unreachable", component=TRANSCRIBER, error=type(exc).__name__)
        return _down(DETAIL_UNREACHABLE)
    status = str(body.get("status") or "") if isinstance(body, dict) else ""
    return _answered(started, healthy=status == "ok")


__all__ = [
    "DETAIL_DEGRADED",
    "DETAIL_TIMEOUT",
    "DETAIL_UNREACHABLE",
    "DOWNLOADER",
    "SIDECAR_PROBE_TIMEOUT_SECONDS",
    "TRANSCRIBER",
    "DownloaderProbe",
    "TranscriberProbe",
    "downloader_status",
    "not_configured",
    "transcriber_status",
]
