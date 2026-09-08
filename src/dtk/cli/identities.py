"""The identity pool from the terminal.

Cookie jars never appear in this output. ``list`` shows cookie *names* at most,
``mint`` reports which names came back, and ``test`` reports what the platform
answered - not what was sent. An identity's whole value is its credential, and a
terminal is the least private place there is.

``test`` issues one real signed request. It does not take a lease and does not
record an outcome: probing an identity must not be the thing that cools it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import typer

from dtk.cli import output, runtime
from dtk.core.config import extra_url_hosts
from dtk.core.types import IdentitySource, IdentityState, Platform
from dtk.db.repositories import IdentityRepository, ProxyRepository
from dtk.identity import BrowserRpcClient, BrowserRpcUnavailable, IdentityPool
from dtk.ops import pipeline, probes
from dtk.ops.masking import mask_cookies, mask_url, short_id
from dtk.worker.registry import resolve

app = typer.Typer(no_args_is_help=True, help="Inspect, mint, retire and probe pool identities.")


@app.command("list")
def list_identities(
    platform: Annotated[
        Platform | None, typer.Option("--platform", help="Only this platform")
    ] = None,
    state: Annotated[
        IdentityState | None, typer.Option("--state", help="Only identities in this state")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500, help="Rows to show")] = 50,
) -> None:
    """List identities with their health, never their cookies."""
    states = (state,) if state is not None else tuple(IdentityState)

    async def operation(ctx: runtime.Context) -> list[dict[str, str]]:
        rows = await IdentityRepository(ctx.session).list_by_state(
            platform=platform, states=states, limit=limit
        )
        proxies = {p.id: p for p in await ProxyRepository(ctx.session).list_all()}
        table_rows = []
        for row in rows:
            proxy = proxies.get(row.proxy_id) if row.proxy_id else None
            fingerprint = row.fingerprint or {}
            family = fingerprint.get("browser_family") or "-"
            major = fingerprint.get("browser_major")
            table_rows.append(
                {
                    "id": short_id(row.id),
                    "platform": row.platform,
                    "state": row.state,
                    "auth": "yes" if row.authenticated else "no",
                    "source": row.source,
                    "browser": f"{family} {major}" if major else str(family),
                    "proxy": (proxy.label or short_id(proxy.id)) if proxy else "direct",
                    "fails": str(row.consecutive_fails),
                    "cooldown": row.cooldown_until.isoformat(timespec="seconds")
                    if row.cooldown_until
                    else "-",
                    "last used": row.last_used_at.isoformat(timespec="seconds")
                    if row.last_used_at
                    else "never",
                }
            )
        return table_rows

    rows = runtime.with_context(operation)
    table = output.new_table(
        "id",
        "platform",
        "state",
        "auth",
        "source",
        "browser",
        "proxy",
        "fails",
        "cooldown",
        "last used",
    )
    for row in rows:
        table.add_row(
            row["id"],
            row["platform"],
            output.styled_state(row["state"]),
            row["auth"],
            row["source"],
            row["browser"],
            row["proxy"],
            row["fails"],
            row["cooldown"],
            row["last used"],
        )
    output.print_table(table, empty="the pool is empty; dtk identity mint fills it")


@app.command("mint")
def mint_identity(
    platform: Annotated[Platform, typer.Option("--platform", help="Platform to mint for")],
    count: Annotated[int, typer.Option("--count", min=1, max=20, help="How many to mint")] = 1,
    proxy_id: Annotated[
        str | None, typer.Option("--proxy", help="Use this proxy instead of picking a free one")
    ] = None,
) -> None:
    """Mint guest identities through browser-rpc.

    Each identity is bound to one proxy for life. A free proxy is chosen when
    none is named, because two identities sharing an exit address on the same
    platform is the anomaly the pool exists to avoid.
    """

    async def operation(ctx: runtime.Context) -> list[dict[str, str]]:
        if not ctx.settings.browser_rpc_url:
            output.fail(
                "browser-rpc is not configured",
                hint="set DTK_BROWSER_RPC_URL, or import cookies instead",
            )
        client = BrowserRpcClient(ctx.settings.browser_rpc_url)
        pool = IdentityPool(ctx.cipher)
        proxies = ProxyRepository(ctx.session)
        minted: list[dict[str, str]] = []
        try:
            for _ in range(count):
                if proxy_id is not None:
                    proxy = await proxies.get(_as_uuid(proxy_id))
                    if proxy is None:
                        output.fail(f"no proxy with id {proxy_id}")
                else:
                    free = await proxies.list_unbound(platform)
                    proxy = free[0] if free else None

                proxy_url = (
                    ctx.cipher.decrypt(proxy.url_encrypted, aad=str(proxy.id)) if proxy else None
                )
                geo_hint = {"country": proxy.country, "timezone": proxy.timezone} if proxy else None
                try:
                    result = await client.mint(platform, proxy_url=proxy_url, geo_hint=geo_hint)
                except BrowserRpcUnavailable as exc:
                    output.fail(f"minting failed: {exc}")

                identity_id = await pool.add(
                    ctx.session,
                    platform=platform,
                    cookies=result.cookies,
                    fingerprint=result.fingerprint,
                    source=IdentitySource.MINTED,
                    proxy_id=proxy.id if proxy else None,
                )
                minted.append(
                    {
                        "id": str(identity_id),
                        "proxy": (proxy.label or short_id(proxy.id)) if proxy else "direct",
                        "exit ip": result.exit_ip or "-",
                        "cookies": mask_cookies(result.cookies),
                    }
                )
        finally:
            await client.aclose()
        return minted

    minted = runtime.with_context(operation)
    table = output.new_table("id", "proxy", "exit ip", "cookies")
    for row in minted:
        table.add_row(row["id"], row["proxy"], row["exit ip"], row["cookies"])
    output.print_table(table)
    output.ok(f"minted {len(minted)} {platform.value} identity(s)")


@app.command("retire")
def retire_identity(
    identity_id: Annotated[str, typer.Argument(help="Identity id, full or the shown prefix")],
    reason: Annotated[str, typer.Option("--reason", help="Recorded on the identity event")] = (
        "retired from the cli"
    ),
) -> None:
    """Retire an identity and wipe its cookie jar immediately.

    Retiring twice is refused rather than repeated: the second call would
    overwrite ``retire_reason`` and ``retired_at`` with "retired from the cli",
    losing the record of why the identity was actually taken out of the pool.
    """

    async def operation(ctx: runtime.Context) -> tuple[str, bool]:
        row = await _resolve_identity(ctx, identity_id)
        if row.state == IdentityState.RETIRED.value:
            return str(row.id), True
        await IdentityPool(ctx.cipher).retire(ctx.session, str(row.id), reason)
        return str(row.id), False

    retired, already = runtime.with_context(operation, redis=True)
    if already:
        output.fail(
            f"identity {retired} is already retired",
            hint="its cookie jar was wiped when it was retired the first time",
        )
    output.ok(f"retired {retired}: {reason}")


@app.command("test")
def test_identity(
    identity_id: Annotated[str, typer.Argument(help="Identity id, full or the shown prefix")],
    url: Annotated[
        str | None, typer.Option("--url", help="Probe with this link instead of the built-in one")
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Seconds for the request")] = 25.0,
) -> None:
    """Make one real request as this identity and report what came back.

    No lease, no rate limit, no bookkeeping: the probe answers whether the
    platform still talks to this identity, and changes nothing.
    """

    async def operation(ctx: runtime.Context) -> dict[str, str]:
        row = await _resolve_identity(ctx, identity_id)
        platform = Platform(row.platform)
        identity = await pipeline.pick_identity(
            ctx.session, ctx.cipher, platform, identity_id=str(row.id)
        )
        target = await pipeline.resolve_target(
            url or probes.SMOKE_URLS[platform],
            proxy_url=identity.proxy_url,
            extra_hosts=extra_url_hosts(ctx.config),
        )
        call = resolve(target.endpoint, target.params, ctx.config)
        async with pipeline.signing_stack(ctx.settings.browser_rpc_url) as (transport, signers):
            probe = await probes.probe_identity(transport, signers, identity, call, timeout=timeout)
        return {
            "identity": str(identity.id),
            "platform": platform.value,
            "endpoint": target.endpoint,
            "proxy": mask_url(identity.proxy_url),
            "cookies": mask_cookies(identity.cookies),
            "ok": "yes" if probe.ok else "no",
            "outcome": probe.outcome.value if probe.outcome else "-",
            "status": str(probe.status) if probe.status is not None else "-",
            "latency ms": str(probe.latency_ms) if probe.latency_ms is not None else "-",
            "rule": probe.rule or "-",
            "detail": probe.detail or "-",
        }

    result = runtime.with_context(operation, config=True)
    output.print_pairs(
        [
            (key, output.styled_state(value) if key == "outcome" else value)
            for key, value in result.items()
        ]
    )
    if result["ok"] != "yes":
        output.fail(f"identity {result['identity']} did not get a usable answer")
    output.ok("identity answered")


async def _resolve_identity(ctx: runtime.Context, identity_id: str):
    """Accept a full id or the shortened prefix the tables print."""
    repo = IdentityRepository(ctx.session)
    if len(identity_id) >= 32:
        row = await repo.get(_as_uuid(identity_id))
        if row is None:
            output.fail(f"no identity with id {identity_id}")
        return row

    matches = [
        row
        for row in await repo.list_by_state(states=tuple(IdentityState), limit=None)
        if str(row.id).startswith(identity_id.lower())
    ]
    if not matches:
        output.fail(f"no identity whose id starts with {identity_id}")
    if len(matches) > 1:
        output.fail(
            f"{len(matches)} identities start with {identity_id}",
            hint="pass the full id shown by dtk identity list",
        )
    return matches[0]


def _as_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        output.usage(f"not an id: {value}")


__all__ = ["app"]
