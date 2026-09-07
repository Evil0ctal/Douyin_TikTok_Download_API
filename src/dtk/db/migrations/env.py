"""Alembic environment. Async engine, TimescaleDB aware.

Three decisions in this file exist because of TimescaleDB:

1. ``transaction_per_migration=True``. A continuous aggregate cannot be created
   inside a transaction block, so a script opens
   ``op.get_context().autocommit_block()`` around that statement. Alembic
   commits the enclosing transaction when the block opens; keeping one
   transaction per script bounds what that commit covers.
2. Autogenerate ignores TimescaleDB's internal schemas. Chunks, materialization
   hypertables and the catalog are not application tables, and without the
   filter every autogenerate run proposes dropping them.
3. The connection is a real asyncpg connection driven through ``run_sync``; the
   migration scripts see the ordinary synchronous Alembic API.

The URL comes from the environment (``DTK_DATABASE_URL``, then
``DATABASE_URL``, then ``sqlalchemy.url`` in alembic.ini) so that no password
is ever committed. A caller that already holds a connection injects it through
``config.attributes["connection"]``; from async code that means handing over the
synchronous facade, which is how an integration test migrates a throwaway
database::

    async with engine.connect() as conn:
        await conn.run_sync(
            lambda sync_conn: _upgrade_with(cfg, sync_conn, "head")
        )

where ``_upgrade_with`` sets ``cfg.attributes["connection"] = sync_conn`` and
calls ``alembic.command.upgrade``.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import AsyncConnection, async_engine_from_config

from dtk.core.db import Base
from dtk.db import models

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

#: Schemas TimescaleDB owns. Nothing in them is ours to create or drop.
INTERNAL_SCHEMAS = frozenset(
    {
        "_timescaledb_internal",
        "_timescaledb_catalog",
        "_timescaledb_config",
        "_timescaledb_cache",
        "_timescaledb_functions",
        "timescaledb_information",
        "timescaledb_experimental",
    }
)

#: Prefixes of objects TimescaleDB generates: chunks, compressed chunks and the
#: materialization hypertables behind continuous aggregates.
INTERNAL_PREFIXES = (
    "_hyper_",
    "_dist_hyper_",
    "_compressed_hypertable_",
    "_materialized_hypertable_",
)


def database_url() -> str:
    """Resolve the connection URL and normalize it to the async driver.

    An explicitly configured URL wins: a caller that passed one through
    :func:`dtk.db.migrate.alembic_config` means that database, and must not be
    redirected by a DTK_DATABASE_URL left in the shell.
    """
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("DTK_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    if not url:
        raise RuntimeError(
            "no database URL: set DTK_DATABASE_URL, for example "
            "postgresql+asyncpg://dtk:***@postgres:5432/dtk"
        )
    if url.startswith("postgresql://"):
        # A libpq-style URL pasted from psql or a hosting panel.
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Keep TimescaleDB's own objects out of autogenerate."""
    if getattr(obj, "schema", None) in INTERNAL_SCHEMAS:
        return False
    if name and name.startswith(INTERNAL_PREFIXES):
        return False
    # The aggregates and the view over them are created by hand-written DDL, so
    # autogenerate must neither propose creating nor dropping them.
    return not (
        type_ == "table" and name in {*models.CONTINUOUS_AGGREGATES, *models.DERIVED_VIEWS}
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic upgrade --sql``).

    Continuous aggregates and hypertable policies still appear in the script,
    but the operator has to run them outside a transaction themselves.
    """
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        include_object=include_object,
        # Required for autocommit_block(): see the module docstring.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    injected = config.attributes.get("connection", None)
    if isinstance(injected, Connection):
        # Already inside the caller's connection, and inside its event loop when
        # that connection came from AsyncConnection.run_sync.
        do_run_migrations(injected)
    elif isinstance(injected, AsyncConnection):
        raise RuntimeError(
            "config.attributes['connection'] must be a synchronous Connection; "
            "call it from inside AsyncConnection.run_sync (see the module docstring)"
        )
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
