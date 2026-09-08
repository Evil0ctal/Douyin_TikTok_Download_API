"""Parse one link and show what came back.

The fastest way to answer "is this endpoint dead?". No login, no API key, no
rate limit and no cache: the URL is resolved, one signed request goes out
through a real identity, and the answer is printed - normalized, and with
``--raw`` the platform's own payload alongside it.

When parsing fails, ``--raw`` is the whole point: an ``UPSTREAM_CHANGED`` error
names the field that went missing, and the raw body next to it is what tells you
whether the platform renamed it or stopped returning it at all.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from dtk.cli import output, runtime
from dtk.core.config import extra_url_hosts
from dtk.core.errors import DtkError, UpstreamChanged
from dtk.core.logging import get_logger
from dtk.core.types import Outcome
from dtk.ops import pipeline
from dtk.ops.masking import mask_url, short_id
from dtk.services import archive
from dtk.worker.registry import resolve

log = get_logger(__name__)


def fetch(
    url: Annotated[str, typer.Argument(help="A Douyin or TikTok link, or pasted share text")],
    raw: Annotated[
        bool, typer.Option("--raw", help="Include the platform's own payload in the output")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print only the JSON result, for piping")
    ] = False,
    identity_id: Annotated[
        str | None, typer.Option("--identity", help="Use this identity instead of the next one")
    ] = None,
    proxy: Annotated[
        str | None, typer.Option("--proxy", help="Proxy for the short-link expansion hop")
    ] = None,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Seconds to wait for the platform")
    ] = pipeline.REQUEST_TIMEOUT_SECONDS,
) -> None:
    """Fetch one link and print the normalized result."""

    async def operation(ctx: runtime.Context) -> dict[str, Any]:
        target = await pipeline.resolve_target(
            url, proxy_url=proxy, extra_hosts=extra_url_hosts(ctx.config)
        )
        call = resolve(target.endpoint, target.params, ctx.config)
        identity = await pipeline.pick_identity(
            ctx.session, ctx.cipher, target.platform, identity_id=identity_id
        )
        async with pipeline.signing_stack(ctx.settings.browser_rpc_url) as (transport, signers):
            response = await pipeline.call_endpoint(
                transport, signers, identity, call, timeout=timeout
            )
            classification = transport.classify(response)

        body = response.json_or_none()
        summary = {
            "platform": target.platform.value,
            "resource": target.kind.resource.value,
            "endpoint": target.endpoint,
            "url": target.kind.url or url,
            "identity": short_id(identity.id),
            "proxy": mask_url(identity.proxy_url),
            "http status": str(response.status),
            "outcome": classification.outcome.value,
            "rule": classification.rule,
            "latency ms": str(response.elapsed_ms),
            "bytes": str(len(response.body)),
        }

        if classification.outcome is not Outcome.OK or not isinstance(body, dict):
            return {"summary": summary, "result": None, "raw": body if raw else None}

        try:
            parsed = call.parse(body)
        except UpstreamChanged as exc:
            return {
                "summary": summary,
                "result": None,
                "raw": body if raw else None,
                "parse_error": {"code": exc.code.value, "path": exc.path, "message": str(exc)},
            }
        # Archive here too. `dtk fetch` is one of the three entry points doc 01
        # promises share one logic, and it does not run through the worker - so
        # hooking only TaskWorker._execute left the CLI silently storing nothing,
        # which is exactly the asymmetry that rule exists to prevent.
        await _archive(ctx, parsed)
        return {
            "summary": summary,
            "result": pipeline.dump(parsed, include_raw=raw),
            "raw": None,
        }

    try:
        report = runtime.with_context(operation, config=True)
    except DtkError as exc:  # pragma: no cover - runtime.run maps these already
        output.fail(f"{exc.code.value}: {exc}")

    if as_json:
        output.print_json(
            {
                "summary": report["summary"],
                "result": report.get("result"),
                "raw": report.get("raw"),
                "parse_error": report.get("parse_error"),
            }
        )
    else:
        output.print_pairs(
            [
                (key, output.styled_state(value) if key == "outcome" else value)
                for key, value in report["summary"].items()
            ]
        )
        if report.get("result") is not None:
            output.print_json(report["result"])
        if report.get("raw") is not None:
            output.info("raw response:")
            output.print_json(report["raw"])

    if report.get("parse_error"):
        error = report["parse_error"]
        output.fail(
            f"{error['code']}: the parser expected {error['path']}",
            hint="re-run with --raw to see what the platform sent instead",
        )
    if report["summary"]["outcome"] != Outcome.OK.value:
        output.fail(
            f"the platform answered {report['summary']['outcome']} ({report['summary']['rule']})",
            hint="dtk diagnose checks the identity pool, proxies and signatures in one pass",
        )
    if not as_json:
        output.ok("endpoint answered")


__all__ = ["fetch"]


async def _archive(ctx: Any, parsed: Any) -> None:
    """Keep what this fetch parsed, on the same terms the worker uses.

    Failures are logged and dropped: the caller asked for data and has it, and a
    missing archive row is a smaller harm than a command that reports failure
    after a successful fetch.
    """
    if not bool(ctx.config.get("archive.enabled")):
        return
    try:
        await archive.record(
            ctx.session, (parsed,), store_raw=bool(ctx.config.get("archive.store_raw"))
        )
        await ctx.session.commit()
    except Exception as exc:
        log.warning("cli.archive_failed", error=f"{type(exc).__name__}: {exc}")
