"""Load a backup archive back into the database.

Submitted by ``POST /admin/backup/restore`` with ``{"path": <file name>}``, the
name being one row of the listing that endpoint's sibling returns. The route has
already refused an unreadable archive, an unsupported format version and a
different ``DTK_SECRET_KEY``, so by the time this runs the archive is one this
instance can actually use; :func:`dtk.ops.backup.restore_backup` checks all
three again anyway, because between queueing and running is a window in which a
file can change underneath both of us.

**Why this is a task at all.** The export is queued because it reads whole
tables; the restore is queued because it *writes* them. An HTTP handler that ran
it would hold one request-scoped transaction open for as long as the archive is
large, and a console that gave up waiting would leave that transaction with
nobody watching it. Here the work belongs to the worker, and
:class:`~dtk.worker.ops.OperationRunner` commits it exactly once, at the end.

**The archive fills gaps, it does not overwrite.** Rows already present win
(``ON CONFLICT DO NOTHING``), so restoring onto a live instance adds what is
missing rather than reverting what is there. The per-table counts in the result
are therefore what the archive offered, not how many rows were new - the
database does not report the difference, and inventing a number for it would be
worse than saying what was read.

Like every maintenance result this one is stored in the database and rendered in
a browser, so it carries the file's name, the per-table counts and nothing that
describes the host or the master key.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dtk.core.errors import Internal, NotFound
from dtk.core.logging import get_logger
from dtk.ops.backup import default_backup_dir, restore_backup

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Restore one archive and report what went back in.

    ``deps`` contributes only ``secret_key``: the archive holds ciphertext that
    was encrypted under the key this process runs with, and a restore that used
    any other key would store rows nothing can ever decrypt.
    """
    directory = default_backup_dir()
    # Only the file name is honoured, whatever the task row says. The route
    # validates the parameter, but a task is a row in a table and this keeps the
    # job from addressing anything outside the backup directory even if one
    # arrived by some other route.
    archive = directory / Path(str(params.get("path", ""))).name
    if not archive.is_file():
        raise NotFound(
            "that backup archive is no longer in the backup directory",
            details={"path": archive.name},
        )

    started = time.monotonic()
    try:
        report = await restore_backup(session, archive, secret_key=deps.secret_key)
    except OSError as exc:
        # An archive that was readable when the route checked it and is not
        # readable now: a truncated file, a volume that went away mid-restore.
        # Named in the log because the database is now partly written.
        log.error(
            "ops.restore.unreadable",
            path=archive.name,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise Internal(
            "the backup archive could not be read to the end",
            details={"path": archive.name},
        ) from exc

    return {
        "data": {
            "path": archive.name,
            # Rows the archive held per table, in the order they were replayed.
            "restored": dict(report.restored),
            "skipped": list(report.skipped),
            "rows": report.total_rows,
        },
        "meta": {
            "endpoint": "backup.restore",
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    }


__all__ = ["run"]
