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
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dtk.core.errors import Internal
from dtk.core.logging import get_logger
from dtk.ops.backup import create_backup, default_backup_dir

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
        raise Internal(
            f"could not write the backup archive into {directory}",
            details={"directory": directory.name},
        ) from exc

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


__all__ = ["run"]
