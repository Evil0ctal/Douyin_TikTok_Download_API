"""Write a backup archive.

Submitted by ``POST /admin/backup`` with ``{"include_identities": bool}`` and
read by ``web/src/pages/Backup.tsx``, interface ``CreateResult``: ``path``,
``size_bytes`` and ``rows``, all optional there and all sent from here.

The export itself is :func:`dtk.ops.backup.create_backup`, the same call ``dtk
backup create`` makes - one archive format, one manifest, one key check, however
the backup was asked for. What this module decides is where the archive lands
and what may be said about it afterwards.

**Where.** :data:`~dtk.ops.backup.DEFAULT_BACKUP_DIR`, because that is the one
directory a reader looks in: an archive written anywhere else is one ``dtk
backup list`` will not show and the console cannot offer to restore.

**What may be said.** The archive holds ciphertext, and with
``include_identities`` it holds cookie jars; the result of this task does not.
It is stored in the database and rendered in a browser, so it carries the file's
name, its size and its row count and nothing else. Not the absolute path - that
describes the host's filesystem to whoever can open the console - and not the
manifest's ``key_check``, which exists to prove two DTK_SECRET_KEYs match and is
therefore an oracle for guesses at the master key.

**What is said when it fails.** Doc 15 alerts on a failed backup, because the
one way a backup fails is silently and the discovery is otherwise a restore
that has nothing to restore. The alert leaves the host entirely, so it says
less than the log does - see :func:`_reason`.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dtk.core.errors import Internal
from dtk.core.logging import get_logger
from dtk.ops.backup import create_backup, default_backup_dir
from dtk.worker.alerts import NotifyEvent, raise_alert

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dtk.worker.ops import OperationDeps

log = get_logger(__name__)


async def run(
    deps: OperationDeps, session: AsyncSession, params: Mapping[str, Any]
) -> dict[str, Any]:
    """Export the database and report what was written.

    ``deps`` is unused: a backup reads its own tables through ``session`` and
    talks to no platform, so none of the pool, the transport or the signers has
    anything to contribute.
    """
    directory = default_backup_dir()
    include_identities = bool(params.get("include_identities", False))
    started = time.monotonic()

    try:
        info = await create_backup(
            session,
            output=directory,
            secret_key=deps.secret_key,
            include_identities=include_identities,
        )
    except OSError as exc:
        # A read-only container filesystem or a full disk. Worth naming in the
        # log, because the archive is missing and nothing else will say why.
        log.error(
            "ops.backup.unwritable",
            directory=str(directory),
            error=f"{type(exc).__name__}: {exc}",
        )
        await _page_failure(deps, exc)
        raise Internal(
            f"could not write the backup archive into {directory}",
            details={"directory": directory.name},
        ) from exc
    except Exception as exc:
        # Doc 15's trigger is "the backup failed", and a database that went
        # away mid-export leaves the same hole in the backup history as a full
        # disk does. Only the alert is added here; the error travels on
        # unchanged, to be recorded on the task as it was before.
        await _page_failure(deps, exc)
        raise

    manifest = info.manifest
    return {
        # The name alone, not info.path: it is what the console displays and
        # what a restore addresses, and it cannot describe anything outside the
        # backup directory.
        "data": {
            "path": info.path.name,
            "size_bytes": info.size_bytes,
            "rows": manifest.total_rows if manifest is not None else 0,
        },
        "meta": {
            "endpoint": "backup",
            "include_identities": include_identities,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    }


async def _page_failure(deps: OperationDeps, exc: BaseException) -> None:
    """Raise doc 15's backup alert. Best effort, like every other emitter.

    Nothing is done with the outcome: a channel that refuses the alert has
    already logged why, and the caller is on its way to reporting the failure
    that got us here.
    """
    await raise_alert(deps.notifier, NotifyEvent.BACKUP_FAILED, reason=_reason(exc))


def _reason(exc: BaseException) -> str:
    """What may be said about a failed backup in a message that leaves the host.

    ``OSError`` puts the file it could not write into ``str(exc)`` and a driver
    error puts its DSN there, so neither is quoted. The alert carries the errno
    text - "No space left on device" - or the exception's class, which is
    already enough to tell the two apart; the log line beside it keeps the full
    text for whoever can reach the container.
    """
    strerror = getattr(exc, "strerror", None)
    if isinstance(strerror, str) and strerror:
        return strerror
    return type(exc).__name__


__all__ = ["run"]
