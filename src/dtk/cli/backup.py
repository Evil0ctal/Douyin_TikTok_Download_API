"""Backup and restore from the terminal.

A thin shell over :mod:`dtk.ops.backup`: the archive format, the manifest, the
key check and the row encoding all live there, so the console and the CLI cannot
produce archives that disagree. What this module adds is the part that only
makes sense at a terminal - a confirmation prompt before a restore overwrites an
instance, and a readable summary of what an archive contains.

Two properties of the format are worth repeating where an operator will read
them, because both surprise people: credentials are exported still encrypted, so
restoring needs the same ``DTK_SECRET_KEY``; and identities are left out unless
asked for, because they are bound to a proxy and an exit address that a new
machine does not have.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dtk.cli import output, runtime
from dtk.ops.backup import (
    BackupError,
    BackupInfo,
    RestoreReport,
    SecretKeyMismatch,
    create_backup,
    list_backups,
    read_manifest,
    restore_backup,
)

#: Where archives go when no path is given. Relative, so a container that binds
#: a volume at ./backups gets them on the host without extra configuration.
DEFAULT_BACKUP_DIR = Path("backups")

app = typer.Typer(no_args_is_help=True, help="Create, inspect and restore backups.")


def _megabytes(size_bytes: int) -> str:
    return f"{size_bytes / 1_048_576:.1f} MiB"


@app.command("create")
def create(
    output_path: Annotated[
        Path, typer.Option("--output", "-o", help="Archive path, or a directory to write into")
    ] = DEFAULT_BACKUP_DIR,
    include_identities: Annotated[
        bool,
        typer.Option(
            "--include-identities",
            help="Also export login credentials; for moving a deployment, not for routine backups",
        ),
    ] = False,
) -> None:
    """Write a backup archive."""

    async def operation(ctx: runtime.Context) -> BackupInfo:
        return await create_backup(
            ctx.session,
            output=output_path,
            secret_key=ctx.settings.secret_key,
            include_identities=include_identities,
        )

    info = runtime.with_context(operation)
    manifest = info.manifest
    if manifest is not None:
        table = output.new_table("table", "rows")
        for name, rows in sorted(manifest.contents.items()):
            table.add_row(name, str(rows))
        output.print_table(table)
    output.ok(f"wrote {info.path} ({_megabytes(info.size_bytes)})")
    if not include_identities:
        output.info(
            "identities were not exported; pass --include-identities when moving the whole "
            "deployment to a machine with the same egress"
        )


@app.command("list")
def list_archives(
    directory: Annotated[
        Path, typer.Option("--dir", help="Directory to scan")
    ] = DEFAULT_BACKUP_DIR,
) -> None:
    """List archives in a directory, newest first."""
    infos = list_backups(directory)
    table = output.new_table("file", "created", "version", "rows", "identities", "size", "state")
    for info in infos:
        manifest = info.manifest
        table.add_row(
            info.path.name,
            manifest.created_at.isoformat(timespec="seconds") if manifest else "-",
            manifest.dtk_version if manifest else "-",
            str(manifest.total_rows) if manifest else "-",
            ("yes" if manifest.include_identities else "no") if manifest else "-",
            _megabytes(info.size_bytes),
            output.styled_state("ok" if manifest else "error"),
        )
        if info.error:
            output.warn(f"{info.path.name}: {info.error}")
    output.print_table(table, empty=f"no archives in {directory}")


@app.command("restore")
def restore(
    path: Annotated[Path, typer.Argument(help="Archive to restore")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
) -> None:
    """Restore an archive into this instance.

    Rows that already exist are kept: a restore fills in what is missing rather
    than overwriting an instance that is already in use.
    """
    if not path.is_file():
        output.usage(f"no such file: {path}")

    try:
        manifest = read_manifest(path)
    except BackupError as exc:
        output.fail(f"cannot read {path.name}: {exc}")

    output.print_pairs(
        [
            ("archive", path.name),
            ("created", manifest.created_at.isoformat(timespec="seconds")),
            ("written by", manifest.dtk_version),
            ("schema version", str(manifest.schema_version)),
            ("identities", "yes" if manifest.include_identities else "no"),
            ("rows", str(manifest.total_rows)),
        ]
    )
    if not output.confirm(
        f"Restore {manifest.total_rows} rows into this instance?", assume_yes=yes
    ):
        output.fail("cancelled; nothing was written")

    async def operation(ctx: runtime.Context) -> RestoreReport:
        try:
            return await restore_backup(ctx.session, path, secret_key=ctx.settings.secret_key)
        except SecretKeyMismatch:
            output.fail(
                "DTK_SECRET_KEY does not match the key this backup was taken with",
                hint=(
                    "restore with the original key; without it the cookies and proxy URLs "
                    "in the archive cannot be decrypted"
                ),
            )

    report = runtime.with_context(operation)
    table = output.new_table("table", "rows restored")
    for name, rows in sorted(report.restored.items()):
        table.add_row(name, str(rows))
    output.print_table(table, empty="the archive held no rows")
    for name in report.skipped:
        output.info(f"skipped {name}")
    output.ok(f"restored {report.total_rows} rows from {path.name}")


__all__ = ["DEFAULT_BACKUP_DIR", "app"]
