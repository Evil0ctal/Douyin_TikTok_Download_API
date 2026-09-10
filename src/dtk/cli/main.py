"""The dtk command line.

Positioned as an operations and rescue tool, not as a second way to query the
API (docs/design/15-operations.md). Everything here is something you reach for
when the console cannot help: nobody can log in, the instance has to move to
another machine, or an endpoint has gone quiet and you need to see the raw
answer without authentication, rate limiting or a cache in the way.

The command groups are the nouns an operator thinks in - user, backup, config,
identity, proxy - plus the four verbs that run or repair the system: migrate,
serve, worker, diagnose, and fetch for a single link.
"""

# ==============================================================================
# 　　　　 　　  ＿＿
# 　　　 　　 ／＞　　フ
# 　　　 　　| 　_　 _ l
# 　 　　 　／` ミ＿xノ
# 　　 　 /　　　 　 |       Feed me Stars ⭐ ️
# 　　　 /　 ヽ　　 ﾉ
# 　 　 │　　|　|　|
# 　／￣|　　 |　|　|
# 　| (￣ヽ＿_ヽ_)__)
# 　＼二つ
# ==============================================================================

from __future__ import annotations

from typing import Annotated

import typer

from dtk import __version__
from dtk.cli import (
    backup,
    diagnose_cmd,
    fetch_cmd,
    identities,
    output,
    proxies,
    runtime,
    settings_cmd,
    users,
    worker,
)

app = typer.Typer(
    name="dtk",
    help="Operations and rescue commands for a self-hosted dtk instance.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(users.app, name="user")
app.add_typer(backup.app, name="backup")
app.add_typer(settings_cmd.app, name="config")
app.add_typer(identities.app, name="identity")
app.add_typer(proxies.app, name="proxy")

app.command("fetch")(fetch_cmd.fetch)
app.command("diagnose")(diagnose_cmd.diagnose)
app.command("worker")(worker.worker)


def _version(value: bool) -> None:
    if value:
        output.console.print(f"dtk {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Print the version and exit"
        ),
    ] = False,
) -> None:
    """Root callback; every command hangs off this."""


@app.command("migrate")
def migrate(
    revision: Annotated[
        str, typer.Option("--revision", help="Target revision; 'head' applies everything")
    ] = "head",
    show: Annotated[
        bool, typer.Option("--show", help="Print the revision on disk and exit")
    ] = False,
) -> None:
    """Apply database migrations.

    Normally the migrate container does this at startup. Running it by hand is
    for an upgrade that has to be timed, or for a database restored from a
    backup taken at an older schema.
    """
    from dtk.db.migrate import head_revision, upgrade

    head = head_revision()
    if show:
        output.print_pairs([("revision on disk", head or "none")])
        return

    settings = runtime.load_settings()
    # Alembic runs its own event loop inside env.py, so this call must not be
    # made from inside one.
    upgrade(settings.database_url, revision)
    output.ok(f"database migrated to {revision} (head on disk: {head})")


@app.command("serve")
def serve(
    host: Annotated[str | None, typer.Option("--host", help="Bind address")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Bind port")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Reload on source changes")] = False,
    workers: Annotated[int, typer.Option("--workers", min=1, max=32, help="Worker processes")] = 1,
) -> None:
    """Run the API server with the configured bind address."""
    import uvicorn

    # Before the environment is read: how the command was called is knowable
    # without it, and a bad invocation must not be reported as exit 1 just
    # because DTK_SECRET_KEY happens to be missing too.
    if reload and workers > 1:
        output.usage("--reload cannot be combined with --workers")

    settings = runtime.load_settings()

    uvicorn.run(
        "dtk.api.app:create_app",
        factory=True,
        host=host or settings.bind_host,
        port=port or settings.bind_port,
        reload=reload,
        workers=None if reload else workers,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    app()


__all__ = ["app"]
