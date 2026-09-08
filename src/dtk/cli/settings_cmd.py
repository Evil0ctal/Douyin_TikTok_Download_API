"""Runtime configuration from the terminal.

The settings table is authoritative once an instance is initialized, so a
misconfigured value cannot be fixed by editing ``.env`` and restarting - a fact
that surprises everybody exactly once. These commands are how it gets fixed
without the console, and ``dtk config set`` announces the change on the same
Redis channel the API and workers listen on, so a running deployment picks it up
without a restart.

Values that can carry a credential - a Telegram bot URL in ``notify.channels``,
say - are masked on the way out. A settings dump is the single most likely thing
to end up pasted into a bug report.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer
from rich.text import Text

from dtk.cli import output, runtime
from dtk.core.config import RUNTIME_SETTINGS, Scope, coerce
from dtk.ops.masking import mask_endpoint, mask_secret

#: Mapping keys whose values are credentials wherever they appear in a setting.
_SECRET_FIELDS = frozenset({"token", "secret", "password", "key", "api_key", "webhook"})

#: Mapping keys holding a URL that is itself a credential: a bot token or a
#: webhook path is enough to post to the channel, so only the host survives.
_URL_FIELDS = frozenset({"url", "endpoint", "webhook_url"})

app = typer.Typer(no_args_is_help=True, help="Runtime configuration stored in the database.")


def redact(key: str, value: Any) -> Any:
    """Mask credential-bearing parts of a setting value.

    Walks lists and mappings because the values that matter are nested: the
    channel descriptors in ``notify.channels`` are where a bot token lives.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for field, item in value.items():
            lowered = str(field).lower()
            if lowered in _URL_FIELDS and isinstance(item, str):
                redacted[field] = mask_endpoint(item)
            elif lowered in _SECRET_FIELDS and isinstance(item, str):
                redacted[field] = mask_secret(item)
            else:
                redacted[field] = redact(key, item)
        return redacted
    if isinstance(value, list):
        return [redact(key, item) for item in value]
    return value


def render(value: Any) -> str:
    """One-line rendering for a table cell."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


@app.command("list")
def list_settings(
    scope: Annotated[
        Scope | None, typer.Option("--scope", help="Show only settings in this scope")
    ] = None,
    changed: Annotated[
        bool, typer.Option("--changed", help="Only settings that differ from their default")
    ] = False,
) -> None:
    """List every runtime setting with its current and default value."""

    async def operation(ctx: runtime.Context) -> tuple[int, dict[str, Any]]:
        return ctx.config.version, {key: ctx.config.get(key) for key in RUNTIME_SETTINGS}

    version, values = runtime.with_context(operation, config=True)

    table = output.new_table("key", "value", "default", "scope", "description")
    for key, spec in sorted(RUNTIME_SETTINGS.items()):
        if scope is not None and spec.scope is not scope:
            continue
        current = values.get(key, spec.default)
        if changed and current == spec.default:
            continue
        style = "yellow" if spec.scope is Scope.SENSITIVE else ""
        table.add_row(
            key,
            Text(render(redact(key, current)), style="bold" if current != spec.default else ""),
            render(redact(key, spec.default)),
            Text(spec.scope.value, style=style),
            spec.description or "-",
        )
    output.print_table(table, empty="no settings match that filter")
    output.info(f"settings version {version}")


@app.command("get")
def get_setting(
    key: Annotated[str, typer.Argument(help="Setting key, for example cache.content_ttl")],
) -> None:
    """Print one setting's current value as JSON."""
    if key not in RUNTIME_SETTINGS:
        output.usage(f"unknown setting: {key}")

    async def operation(ctx: runtime.Context) -> Any:
        return ctx.config.get(key)

    value = runtime.with_context(operation, config=True)
    output.print_json({"key": key, "value": redact(key, value)})


@app.command("set")
def set_setting(
    key: Annotated[str, typer.Argument(help="Setting key")],
    value: Annotated[str, typer.Argument(help="New value; lists are comma separated")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask before changing a sensitive setting")
    ] = False,
) -> None:
    """Validate and store one setting, then announce the change.

    Sensitive settings widen the attack surface - the URL allowlist, the
    download proxy - so they ask for confirmation first.
    """
    spec = RUNTIME_SETTINGS.get(key)
    if spec is None:
        output.usage(f"unknown setting: {key}")

    try:
        coerced = coerce(key, value)
    except (ValueError, TypeError) as exc:
        output.usage(f"{key} expects {spec.type_.__name__}: {exc}")

    if spec.scope is Scope.SENSITIVE and not output.confirm(
        f"{key} is a sensitive setting. Change it to {render(coerced)}?", assume_yes=yes
    ):
        output.fail("cancelled; nothing was changed")

    async def operation(ctx: runtime.Context) -> Any:
        from dtk.services.settings_store import set_value

        return await set_value(key, coerced)

    stored = runtime.with_context(operation, redis=True)
    output.ok(f"{key} = {render(redact(key, stored))}")


__all__ = ["app", "redact", "render"]
