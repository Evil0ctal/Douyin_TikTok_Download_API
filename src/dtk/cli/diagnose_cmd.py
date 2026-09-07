"""The one-command self-check.

The diagnosis itself lives in :mod:`dtk.ops.diagnose`, which is also what the
console calls. This module supplies the two things the shared implementation
cannot know by itself - the live dependencies to probe, and how to run one link
through the whole pipeline - and then prints the report.

The output is meant to be pasted into a bug report, so the plain-text rendering
is the primary one and it is redacted by the ops layer before it is returned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from dtk.cli import output, pipeline, probes, runtime
from dtk.cli.masking import short_id
from dtk.core.types import Outcome, Platform
from dtk.identity import BrowserRpcClient
from dtk.ops.diagnose import DiagnoseContext, DiagnosticReport, StepStatus, run_diagnostics
from dtk.worker.registry import resolve

_STATUS_STYLE = {
    StepStatus.PASS: "pass",
    StepStatus.WARN: "warn",
    StepStatus.FAIL: "fail",
    StepStatus.SKIP: "skipped",
}


def diagnose(
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the report as JSON instead of a table")
    ] = False,
    report_path: Annotated[
        Path | None, typer.Option("--output", "-o", help="Also write the text report to a file")
    ] = None,
    skip_smoke: Annotated[
        bool, typer.Option("--skip-smoke", help="Do not make the end-to-end request")
    ] = False,
    platform: Annotated[
        Platform, typer.Option("--platform", help="Platform for the smoke test")
    ] = Platform.DOUYIN,
    smoke_url: Annotated[
        str | None, typer.Option("--smoke-url", help="Link to use for the smoke test")
    ] = None,
    proxy_probe_url: Annotated[
        str | None, typer.Option("--probe-url", help="Service that reports a proxy's exit address")
    ] = None,
) -> None:
    """Run the six-step diagnosis and print the report."""

    async def operation(ctx: runtime.Context) -> DiagnosticReport:
        import httpx

        from dtk.core.db import get_engine
        from dtk.core.redis import get_redis

        rpc = BrowserRpcClient(ctx.settings.browser_rpc_url)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
            diagnose_ctx = DiagnoseContext(
                session=ctx.session,
                engine=get_engine(),
                redis=get_redis(),
                rpc=rpc if rpc.configured else None,
                registry=pipeline.build_registry(ctx.settings.browser_rpc_url, client=http),
                cipher=ctx.cipher,
                http=http,
                smoke=None if skip_smoke else _smoke_runner(ctx),
                smoke_url=None if skip_smoke else (smoke_url or probes.SMOKE_URLS[platform]),
                min_active=int(ctx.config.get("pool.min_size")),
            )
            if proxy_probe_url:
                diagnose_ctx.proxy_probe_url = proxy_probe_url
            try:
                return await run_diagnostics(diagnose_ctx)
            finally:
                await rpc.aclose()

    report = runtime.with_context(operation, redis=True, config=True)

    if as_json:
        output.print_json(report.as_dict())
    else:
        table = output.new_table("#", "step", "status", "reason")
        for step in report.steps:
            table.add_row(
                str(step.number),
                step.step,
                output.styled_state(_STATUS_STYLE[step.status]),
                step.reason,
            )
        output.print_table(table)
        for step in report.steps:
            if step.action:
                output.info(f"{step.step}: {step.action}")

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report.render_text(), encoding="utf-8")
        output.info(f"text report written to {report_path}")

    if not report.passed:
        output.fail(f"{len(report.failures)} step(s) failed")
    output.ok("all steps passed")


def _smoke_runner(ctx: runtime.Context):
    """One public link through resolve, sign, fetch and parse.

    Raises on anything other than a usable answer: the ops step turns an
    exception into a failed step with the reason attached.
    """

    async def run(url: str) -> dict[str, Any]:
        target = await pipeline.resolve_target(url)
        call = resolve(target.endpoint, target.params, ctx.config)
        identity = await pipeline.pick_identity(ctx.session, ctx.cipher, target.platform)
        async with pipeline.signing_stack(ctx.settings.browser_rpc_url) as (transport, signers):
            probe = await probes.probe_identity(transport, signers, identity, call)

        if not probe.ok:
            raise RuntimeError(
                f"{probe.outcome.value if probe.outcome else 'no answer'}: "
                f"{probe.detail or probe.rule or 'no detail'}"
            )
        return {
            "endpoint": target.endpoint,
            "identity": short_id(identity.id),
            "outcome": (probe.outcome or Outcome.OK).value,
            "http_status": probe.status,
            "latency_ms": probe.latency_ms,
        }

    return run


__all__ = ["diagnose"]
