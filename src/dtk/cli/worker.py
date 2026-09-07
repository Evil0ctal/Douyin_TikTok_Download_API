"""``dtk worker`` - run the task worker process.

The loop itself, the pool filler, the proxy prober and the retention job all
live in :mod:`dtk.worker`; this is only the terminal entry point to the same
process the worker container runs as ``python -m dtk.worker``. Having one
implementation means a worker started by hand during an incident behaves
exactly like the one in the compose file, which is the entire point of being
able to start one by hand.

Deliberately without tuning flags. Concurrency, claim timeouts and the
background intervals belong to the process configuration, not to the invocation
- two workers on one machine disagreeing about their limits is a problem nobody
can see from the outside.
"""

from __future__ import annotations

import typer

from dtk.cli import output, runtime


def worker() -> None:
    """Run the task worker until it is stopped."""
    from dtk.worker.main import run

    settings = runtime.load_settings()
    try:
        runtime.run(lambda: run(settings))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        output.info("stopped")
        raise typer.Exit(output.EXIT_OK) from None


__all__ = ["worker"]
