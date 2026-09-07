"""A background loop that outlives the failures of the job it runs.

Every periodic job in the worker - topping the pool up, probing proxies,
maintenance - has the same three requirements, so they are written once here:

* one raised exception must not end the loop, or a single transient database
  error silently stops a job for the lifetime of the process;
* shutdown has to interrupt the sleep, not wait it out, or SIGTERM takes as long
  as the longest interval;
* several worker replicas must not fire the same job at the same instant, which
  is what the jitter is for.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from typing import Any

from dtk.core.logging import get_logger

log = get_logger(__name__)

#: Consecutive failures are backed off up to this multiple of the interval, so a
#: job whose dependency is down stops hammering it without ever giving up.
MAX_FAILURE_BACKOFF_FACTOR = 8


class PeriodicLoop:
    """Runs ``tick`` every ``interval`` seconds until asked to stop."""

    __slots__ = ("_failures", "_interval", "_jitter", "_name", "_sleep", "_stop", "_tick")

    def __init__(
        self,
        name: str,
        interval: float,
        tick: Callable[[], Awaitable[Any]],
        *,
        stop: asyncio.Event | None = None,
        jitter: float = 0.1,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._name = name
        self._interval = max(0.0, interval)
        self._tick = tick
        self._stop = stop or asyncio.Event()
        self._jitter = max(0.0, jitter)
        self._sleep = sleep
        self._failures = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self, *, immediate: bool = True) -> None:
        log.info("worker.loop.started", loop=self._name, interval_seconds=self._interval)
        if not immediate:
            await self._wait(self._delay())
        while not self._stop.is_set():
            await self.run_once()
            await self._wait(self._delay())
        log.info("worker.loop.stopped", loop=self._name)

    async def run_once(self) -> None:
        """One tick. Never raises anything but cancellation."""
        try:
            await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failures += 1
            log.warning(
                "worker.loop.failed",
                loop=self._name,
                consecutive_failures=self._failures,
                error=f"{type(exc).__name__}: {exc}",
            )
        else:
            if self._failures:
                log.info("worker.loop.recovered", loop=self._name, after_failures=self._failures)
            self._failures = 0

    def _delay(self) -> float:
        factor = min(2**self._failures, MAX_FAILURE_BACKOFF_FACTOR) if self._failures else 1
        base = self._interval * factor
        if self._jitter:
            base += base * random.uniform(-self._jitter, self._jitter)
        return max(0.0, base)

    async def _wait(self, seconds: float) -> None:
        if self._stop.is_set():
            return
        if self._sleep is not None:
            await self._sleep(seconds)
            return
        # Waiting on the stop event rather than sleeping means shutdown is
        # immediate instead of taking up to one interval.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)


__all__ = ["MAX_FAILURE_BACKOFF_FACTOR", "PeriodicLoop"]
