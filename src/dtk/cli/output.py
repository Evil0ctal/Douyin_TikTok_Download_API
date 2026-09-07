"""Terminal rendering and the exit-code contract.

Three exit codes, and nothing else may leave the process:

* ``0`` the command did what it was asked to do;
* ``1`` it ran and failed - unreachable database, unknown user, dead proxy;
* ``2`` the invocation was wrong - unknown option, bad value, missing argument.

Keeping ``1`` and ``2`` apart is what lets a deploy script tell "the tool is
being called wrong" from "the system is broken", which is the only reason the
distinction is worth enforcing by hand.

Everything printed here goes through dtk.cli.masking first.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table
from rich.text import Text

from dtk.cli.masking import scrub

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

#: Data goes to stdout so it can be piped; diagnostics go to stderr so a pipe
#: still carries only data.
#:
#: ``markup=False`` on both: almost every string this module prints came from
#: outside it - a classifier detail, an exception message, a proxy label typed
#: by a vendor - and rich reads ``[...]`` in a plain string as console markup.
#: Left on, ``pip install httpx[socks]`` renders as ``pip install httpx`` with
#: the extra silently deleted, and a stray ``[/]`` anywhere in an upstream
#: string aborts the command with a MarkupError. Styling is applied by building
#: a :class:`~rich.text.Text`, which is unaffected by this flag.
console = Console(markup=False)
err_console = Console(stderr=True, markup=False)

_STATE_STYLES: Mapping[str, str] = {
    "active": "green",
    "ok": "green",
    "pass": "green",
    "done": "green",
    "healthy": "green",
    "cooling": "yellow",
    "warn": "yellow",
    "queued": "yellow",
    "running": "cyan",
    "degraded": "yellow",
    "retired": "dim",
    "skipped": "dim",
    "minting": "cyan",
    "failed": "red",
    "fail": "red",
    "error": "red",
}


def styled_state(value: str | None) -> Text:
    """Colour a state or status word by meaning, not by table column."""
    if value is None:
        return Text("-", style="dim")
    return Text(value, style=_STATE_STYLES.get(value.lower(), ""))


def new_table(*columns: str, title: str | None = None) -> Table:
    """A table with the house style: no heavy borders, dim headers."""
    table = Table(title=title, header_style="bold", box=None, pad_edge=False, title_justify="left")
    for column in columns:
        table.add_column(column, overflow="fold")
    return table


def print_table(table: Table, *, empty: str = "nothing to show") -> None:
    if table.row_count == 0:
        console.print(Text(empty, style="dim"))
        return
    console.print(table)


def ok(message: str) -> None:
    console.print(Text("ok  ", style="bold green") + Text(scrub(message)))


def info(message: str) -> None:
    console.print(Text(scrub(message)))


def warn(message: str) -> None:
    err_console.print(Text("warn ", style="bold yellow") + Text(scrub(message)))


def error(message: str) -> None:
    err_console.print(Text("error ", style="bold red") + Text(scrub(message)))


def fail(message: str, *, hint: str | None = None) -> NoReturn:
    """Report a runtime failure and leave with exit code 1."""
    error(message)
    if hint:
        err_console.print(Text(f"hint: {scrub(hint)}", style="dim"))
    raise typer.Exit(EXIT_FAILURE)


def usage(message: str) -> NoReturn:
    """Reject the invocation and leave with exit code 2."""
    raise typer.BadParameter(scrub(message))


def print_json(payload: Any) -> None:
    """Print a JSON document. Machine-readable output never gets a table."""
    console.print(JSON(json.dumps(payload, ensure_ascii=False, default=str, indent=2)))


def print_pairs(pairs: Iterable[tuple[str, Any]]) -> None:
    """Key/value block for single-record output.

    Rendered values are scrubbed. This block is what carries the free-text
    fields of a probe - a classifier detail, a transport failure - and those
    quote the request URL, signed query and all. A ``Text`` is passed through
    because it was built here from a state word, never from upstream text.
    """
    table = new_table("field", "value")
    for key, value in pairs:
        table.add_row(key, value if isinstance(value, Text) else scrub(str(value)))
    console.print(table)


def confirm(message: str, *, assume_yes: bool = False) -> bool:
    """Ask before a destructive action; ``--yes`` skips the prompt."""
    if assume_yes:
        return True
    return typer.confirm(message, default=False)


__all__ = [
    "EXIT_FAILURE",
    "EXIT_OK",
    "EXIT_USAGE",
    "confirm",
    "console",
    "err_console",
    "error",
    "fail",
    "info",
    "new_table",
    "ok",
    "print_json",
    "print_pairs",
    "print_table",
    "styled_state",
    "usage",
    "warn",
]
