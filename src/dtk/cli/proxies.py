"""Egress management.

A proxy URL carries its own credentials, which is why the column is encrypted
and why every rendering in this module goes through ``mask_url``. The plaintext
exists for exactly as long as one probe takes.

``import`` is the bulk path: proxy vendors hand out a text file, and retyping
forty lines into ``add`` is how people end up pasting the file into a chat
window instead.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import typer

from dtk.cli import output, runtime
from dtk.db.models import Proxy
from dtk.db.repositories import ProxyRepository
from dtk.ops import probes
from dtk.ops.masking import mask_url, short_id

#: Schemes a proxy URL may use. Anything else is a typo or a copied web page.
ALLOWED_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})

#: Comment marker in an imported list, so a vendor's notes survive the import.
COMMENT_PREFIX = "#"

app = typer.Typer(no_args_is_help=True, help="Proxies: list, add, import and probe.")


def validate_proxy_url(url: str) -> str:
    """Reject anything that is not a usable proxy URL, before it is encrypted.

    No rejection quotes any part of the input. ``urlsplit`` reads everything
    before the first colon as the scheme, so a vendor line pasted without one -
    ``user:password@host:3128`` - would put the account name, and in the wrong
    paste order the password, into the message. ``proxy import`` reports these
    reasons verbatim, so the message has to be content-free at the source
    rather than sanitized by each caller.
    """
    text = url.strip()
    if not text:
        raise ValueError("empty proxy URL")
    parts = urlsplit(text)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"unsupported or missing scheme; use one of {sorted(ALLOWED_SCHEMES)}")
    if not parts.hostname:
        raise ValueError("proxy URL has no host")
    if parts.port is None:
        raise ValueError("proxy URL has no port")
    return text


def parse_import_file(text: str) -> tuple[list[tuple[str, str | None]], list[tuple[int, str]]]:
    """Split an import file into usable entries and rejected lines.

    One proxy per line, optionally followed by whitespace and a label. Blank
    lines and ``#`` comments are skipped. Rejected lines are reported with their
    line number and never with their content, which may hold a password.
    """
    entries: list[tuple[str, str | None]] = []
    rejected: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(COMMENT_PREFIX):
            continue
        url, _, label = line.partition(" ")
        try:
            entries.append((validate_proxy_url(url), label.strip() or None))
        except ValueError as exc:
            rejected.append((number, str(exc)))
    return entries, rejected


@app.command("list")
def list_proxies(
    healthy_only: Annotated[
        bool, typer.Option("--healthy", help="Only proxies that passed their last probe")
    ] = False,
) -> None:
    """List proxies with their credentials masked."""

    async def operation(ctx: runtime.Context) -> list[tuple[str, ...]]:
        rows = await ProxyRepository(ctx.session).list_all(healthy_only=healthy_only)
        return [
            (
                short_id(row.id),
                row.label or "-",
                mask_url(ctx.cipher.decrypt(row.url_encrypted, aad=str(row.id))),
                row.country or "-",
                row.timezone or "-",
                # The column defaults to true, so an unprobed proxy would read
                # as "healthy" next to a last check of "never". Say what is
                # actually known instead.
                ("healthy" if row.healthy else "failed") if row.last_check_at else "unchecked",
                row.last_check_at.isoformat(timespec="seconds") if row.last_check_at else "never",
            )
            for row in rows
        ]

    rows = runtime.with_context(operation)
    table = output.new_table("id", "label", "url", "country", "timezone", "state", "last check")
    for row in rows:
        table.add_row(*row[:5], output.styled_state(row[5]), row[6])
    output.print_table(table, empty="no proxies; add one with dtk proxy add")


@app.command("add")
def add_proxy(
    url: Annotated[str, typer.Argument(help="Proxy URL, for example http://user:pass@host:3128")],
    label: Annotated[str | None, typer.Option("--label", help="Name shown in listings")] = None,
    country: Annotated[
        str | None, typer.Option("--country", help="ISO country of the exit")
    ] = None,
    timezone: Annotated[
        str | None, typer.Option("--timezone", help="IANA zone of the exit")
    ] = None,
) -> None:
    """Add one proxy. The URL is encrypted before it reaches the database."""
    try:
        validated = validate_proxy_url(url)
    except ValueError as exc:
        output.usage(str(exc))

    async def operation(ctx: runtime.Context) -> str:
        proxy_id = uuid.uuid4()
        ctx.session.add(
            Proxy(
                id=proxy_id,
                url_encrypted=ctx.cipher.encrypt(validated, aad=str(proxy_id)),
                label=label,
                country=country,
                timezone=timezone,
            )
        )
        await ctx.session.flush()
        return str(proxy_id)

    proxy_id = runtime.with_context(operation)
    output.ok(f"added proxy {proxy_id}")
    output.print_pairs([("id", proxy_id), ("label", label or "-"), ("url", mask_url(validated))])


@app.command("import")
def import_proxies(
    path: Annotated[Path, typer.Argument(help="Text file: one proxy URL per line")],
) -> None:
    """Import a proxy list, skipping duplicates and unusable lines."""
    if not path.is_file():
        output.usage(f"no such file: {path}")
    entries, rejected = parse_import_file(path.read_text(encoding="utf-8"))
    if not entries and not rejected:
        output.fail(f"{path} contains no proxy URLs")

    async def operation(ctx: runtime.Context) -> tuple[list[tuple[str, str]], int]:
        repo = ProxyRepository(ctx.session)
        known = {
            ctx.cipher.decrypt(row.url_encrypted, aad=str(row.id)) for row in await repo.list_all()
        }
        added: list[tuple[str, str]] = []
        skipped = 0
        for url, label in entries:
            if url in known:
                skipped += 1
                continue
            proxy_id = uuid.uuid4()
            ctx.session.add(
                Proxy(
                    id=proxy_id,
                    url_encrypted=ctx.cipher.encrypt(url, aad=str(proxy_id)),
                    label=label,
                )
            )
            known.add(url)
            added.append((str(proxy_id), mask_url(url)))
        await ctx.session.flush()
        return added, skipped

    added, skipped = runtime.with_context(operation)

    table = output.new_table("id", "url")
    for proxy_id, masked in added:
        table.add_row(proxy_id, masked)
    output.print_table(table, empty="nothing new to add")
    for number, reason in rejected:
        output.warn(f"line {number}: {reason}")
    output.ok(
        f"added {len(added)}, skipped {skipped} duplicate(s), rejected {len(rejected)} line(s)"
    )


@app.command("test")
def test_proxy(
    proxy_id: Annotated[str, typer.Argument(help="Proxy id, full or the shown prefix")],
    probe_url: Annotated[
        str, typer.Option("--probe-url", help="Service that reports the exit address")
    ] = probes.DEFAULT_PROXY_PROBE_URL,
    write: Annotated[
        bool, typer.Option("--write/--no-write", help="Record the result on the proxy row")
    ] = True,
) -> None:
    """Probe one proxy and report its exit address, country and latency."""

    async def operation(ctx: runtime.Context) -> tuple[str, probes.ProxyProbe]:
        repo = ProxyRepository(ctx.session)
        row = await _resolve_proxy(ctx, proxy_id)
        url = ctx.cipher.decrypt(row.url_encrypted, aad=str(row.id))
        probe = await probes.probe_proxy(url, probe_url=probe_url)
        if write:
            await repo.set_health(row.id, healthy=probe.ok)
            if probe.ok and (probe.country or probe.timezone):
                await repo.set_geo(row.id, country=probe.country, timezone=probe.timezone)
        return str(row.id), probe

    identifier, probe = runtime.with_context(operation)
    output.print_pairs(
        [
            ("id", identifier),
            ("state", output.styled_state("healthy" if probe.ok else "failed")),
            ("latency ms", probe.latency_ms if probe.latency_ms is not None else "-"),
            ("exit ip", probe.exit_ip or "-"),
            ("country", probe.country or "-"),
            ("timezone", probe.timezone or "-"),
            ("detail", probe.detail or "-"),
        ]
    )
    if not probe.ok:
        output.fail(f"proxy {identifier} is not usable")
    output.ok("proxy answered")


async def _resolve_proxy(ctx: runtime.Context, proxy_id: str) -> Proxy:
    """Accept a full id or the shortened prefix the tables print."""
    repo = ProxyRepository(ctx.session)
    if len(proxy_id) >= 32:
        try:
            row = await repo.get(uuid.UUID(proxy_id))
        except ValueError:
            output.usage(f"not an id: {proxy_id}")
        if row is None:
            output.fail(f"no proxy with id {proxy_id}")
        return row

    matches = [row for row in await repo.list_all() if str(row.id).startswith(proxy_id.lower())]
    if not matches:
        output.fail(f"no proxy whose id starts with {proxy_id}")
    if len(matches) > 1:
        output.fail(f"{len(matches)} proxies start with {proxy_id}", hint="pass the full id")
    return matches[0]


__all__ = ["ALLOWED_SCHEMES", "app", "parse_import_file", "validate_proxy_url"]
