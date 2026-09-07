"""Console accounts.

``dtk user passwd`` is the reason this command group exists. A self-hosted tool
has no password-reset email and no support desk, so an administrator who forgets
the console password has exactly one way back in: a shell on the box. Without
this command the honest instruction would be "drop the database", which throws
away every identity, every snapshot and every setting to fix a forgotten string.

Passwords are hashed with argon2id at the OWASP-recommended parameters. Neither
the password nor its digest is ever printed, echoed or logged.
"""

from __future__ import annotations

from typing import Annotated

import typer
from argon2 import PasswordHasher
from rich.text import Text

from dtk.cli import output, runtime
from dtk.cli.masking import short_id
from dtk.core.types import UserRole
from dtk.db.repositories import UserRepository

#: OWASP Password Storage Cheat Sheet, argon2id: 19 MiB, two iterations, one
#: lane. Changing these does not invalidate existing hashes - the parameters are
#: encoded in the digest - so a rehash on next login is all an upgrade costs.
ARGON2_MEMORY_COST = 19456
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1

#: Short enough not to fight the operator, long enough that the argon2 cost
#: still matters. The console enforces the same floor.
MIN_PASSWORD_LENGTH = 8

app = typer.Typer(
    no_args_is_help=True,
    help="Console accounts: create one, reset a password, list them.",
)


def hasher() -> PasswordHasher:
    return PasswordHasher(
        memory_cost=ARGON2_MEMORY_COST,
        time_cost=ARGON2_TIME_COST,
        parallelism=ARGON2_PARALLELISM,
    )


def hash_password(password: str) -> str:
    """Validate and hash. The plaintext never leaves this call."""
    if len(password) < MIN_PASSWORD_LENGTH:
        output.fail(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters",
            hint="nothing was written; run the command again",
        )
    return hasher().hash(password)


def read_password(*, from_stdin: bool) -> str:
    """Collect a password without echoing it.

    ``--stdin`` exists for ``docker exec`` and provisioning scripts, where there
    is no terminal to prompt on. It reads exactly one line so a here-doc cannot
    smuggle a second command in, and strips a CR as well as the LF: a password
    piped from a CRLF file would otherwise be hashed with a trailing carriage
    return, and nothing the operator could type afterwards would match it.
    """
    if from_stdin:
        import sys

        line = sys.stdin.readline()
        if not line.strip():
            output.fail("no password on stdin")
        return line.rstrip("\r\n")
    return typer.prompt("password", hide_input=True, confirmation_prompt=True)


@app.command("create")
def create_user(
    username: Annotated[str, typer.Argument(help="Login name, unique across the instance")],
    role: Annotated[UserRole, typer.Option("--role", help="Permission level")] = UserRole.ADMIN,
    from_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the password from stdin instead of prompting")
    ] = False,
) -> None:
    """Create a console account, prompting for the password."""
    name = username.strip()
    if not name:
        output.usage("username must not be empty")

    password_hash = hash_password(read_password(from_stdin=from_stdin))

    async def operation(ctx: runtime.Context) -> str:
        repo = UserRepository(ctx.session)
        if await repo.get_by_username(name) is not None:
            output.fail(f"a user named {name} already exists", hint="dtk user passwd to reset it")
        user = await repo.create(username=name, password_hash=password_hash, role=role)
        return str(user.id)

    user_id = runtime.with_context(operation)
    output.ok(f"created {role.value} account {name}")
    output.print_pairs([("id", user_id), ("username", name), ("role", role.value)])


@app.command("passwd")
def change_password(
    username: Annotated[str, typer.Argument(help="Account to reset")],
    from_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read the password from stdin instead of prompting")
    ] = False,
) -> None:
    """Reset a console password. The rescue path when nobody can log in."""
    name = username.strip()
    password_hash = hash_password(read_password(from_stdin=from_stdin))

    async def operation(ctx: runtime.Context) -> bool:
        repo = UserRepository(ctx.session)
        user = await repo.get_by_username(name)
        if user is None:
            output.fail(f"no user named {name}", hint="dtk user list shows the accounts")
        return await repo.set_password(user.id, password_hash)

    if not runtime.with_context(operation):
        output.fail(f"password for {name} was not changed")
    output.ok(f"password changed for {name}")


@app.command("list")
def list_users() -> None:
    """List console accounts. Never shows a password digest."""

    async def operation(ctx: runtime.Context) -> list[tuple[str, str, str, str, str]]:
        rows = await UserRepository(ctx.session).list_all()
        return [
            (
                short_id(row.id),
                row.username,
                row.role,
                row.created_at.isoformat(timespec="seconds") if row.created_at else "-",
                row.last_login_at.isoformat(timespec="seconds") if row.last_login_at else "never",
            )
            for row in rows
        ]

    rows = runtime.with_context(operation)
    table = output.new_table("id", "username", "role", "created", "last login")
    for identifier, username, role, created, last_login in rows:
        table.add_row(identifier, username, Text(role, style="bold"), created, last_login)
    output.print_table(table, empty="no accounts yet; create one with dtk user create")


__all__ = [
    "ARGON2_MEMORY_COST",
    "ARGON2_PARALLELISM",
    "ARGON2_TIME_COST",
    "MIN_PASSWORD_LENGTH",
    "app",
    "hash_password",
    "hasher",
    "read_password",
]
