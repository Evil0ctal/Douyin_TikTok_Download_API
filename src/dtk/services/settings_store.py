"""Database-backed runtime configuration with hot reload.

The environment seeds these values once at init; afterwards the database is
authoritative and the console can change them without a restart. The bootstrap
layer stays in the environment because the master key decrypts the database and
therefore cannot live inside it.

Two details carry the design (docs/design/10-configuration.md):

* the snapshot is replaced wholesale, never mutated field by field, so a request
  in flight keeps the values it started with;
* pub/sub is not guaranteed to deliver, so a slow poll of the version counter
  provides eventual consistency. Pub/sub is the fast path, polling is the
  correct one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dtk.core.config import RUNTIME_SETTINGS, Config, Scope, coerce
from dtk.core.db import session_scope
from dtk.core.logging import get_logger
from dtk.core.redis import get_redis
from dtk.db.models import Setting, SettingsVersion

log = get_logger(__name__)

CHANNEL = "config:changed"
POLL_INTERVAL_SECONDS = 30.0


async def load_config() -> Config:
    """Build a snapshot from the database, falling back per key.

    Three tiers: database, then the code default. A value that fails validation
    - hand-edited into the table, say - falls back with a warning rather than
    taking the process down.
    """
    values: dict[str, Any] = {k: s.default for k, s in RUNTIME_SETTINGS.items()}
    version = 0
    async with session_scope() as session:
        rows = (await session.execute(select(Setting))).scalars().all()
        for row in rows:
            if row.key not in RUNTIME_SETTINGS:
                continue
            try:
                values[row.key] = coerce(row.key, row.value)
            except (ValueError, TypeError, KeyError) as exc:
                log.warning(
                    "config.invalid_stored_value",
                    key=row.key,
                    error=str(exc),
                    action="using default",
                )
        current = (await session.execute(select(SettingsVersion))).scalars().first()
        version = current.version if current else 0
    return Config(values, version=version)


async def seed_from_env(env: dict[str, Any]) -> int:
    """Copy runtime-scoped environment values into the table at first init.

    After this the environment no longer wins for these keys, which is logged at
    startup so "I changed .env and nothing happened" has an answer.
    """
    written = 0
    async with session_scope() as session:
        for key, spec in RUNTIME_SETTINGS.items():
            if spec.scope not in (Scope.RUNTIME, Scope.SENSITIVE):
                continue
            env_key = "DTK_" + key.replace(".", "_").upper()
            if env_key not in env:
                continue
            try:
                value = coerce(key, env[env_key])
            except (ValueError, TypeError):
                continue
            await session.execute(
                pg_insert(Setting)
                .values(key=key, value=value)
                .on_conflict_do_nothing(index_elements=[Setting.key])
            )
            written += 1
        await _bump_version(session)
    return written


async def set_value(key: str, value: Any, *, updated_by=None) -> Any:
    """Validate, persist and announce one setting."""
    coerced = coerce(key, value)  # raises before anything is written
    async with session_scope() as session:
        await session.execute(
            pg_insert(Setting)
            .values(key=key, value=coerced, updated_by=updated_by)
            .on_conflict_do_update(
                index_elements=[Setting.key],
                set_={"value": coerced, "updated_by": updated_by},
            )
        )
        version = await _bump_version(session)
    await _announce(version)
    log.info("config.updated", key=key, version=version)
    return coerced


async def reset_value(key: str) -> None:
    """Drop an override so the key falls back to its code default."""
    async with session_scope() as session:
        await session.execute(delete(Setting).where(Setting.key == key))
        version = await _bump_version(session)
    await _announce(version)
    log.info("config.reset", key=key, version=version)


async def _bump_version(session) -> int:
    await session.execute(
        pg_insert(SettingsVersion).values(id=1, version=0).on_conflict_do_nothing()
    )
    result = await session.execute(
        update(SettingsVersion)
        .where(SettingsVersion.id == 1)
        .values(version=SettingsVersion.version + 1)
        .returning(SettingsVersion.version)
    )
    return int(result.scalar_one())


async def _announce(version: int) -> None:
    with contextlib.suppress(Exception):
        # A failed publish is survivable: the poller still converges.
        await get_redis().publish(CHANNEL, json.dumps({"version": version}))


async def current_version() -> int:
    async with session_scope() as session:
        row = (await session.execute(select(SettingsVersion))).scalars().first()
        return row.version if row else 0


async def _reload_into(app) -> None:
    new = await load_config()
    if new.version != app.state.config.version:
        # Atomic reference swap. Field-level mutation would let one request read
        # a mixture of old and new values, which is close to unreproducible.
        app.state.config = new
        log.info("config.reloaded", version=new.version)


async def _watch(app) -> None:
    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        while True:
            try:
                await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0),
                    timeout=POLL_INTERVAL_SECONDS,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A dropped connection must not end the loop; the poll below
                # still converges once it comes back.
                log.warning("config.watch_error", error=str(exc))
                await asyncio.sleep(1.0)
                continue

            # Reload after a message and after every timeout alike: pub/sub can
            # drop messages across a reconnect, so the version poll is the part
            # that is actually guaranteed to converge.
            with contextlib.suppress(Exception):
                await _reload_into(app)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.aclose()  # type: ignore[attr-defined]


async def start_watcher(app) -> None:
    app.state.config_watcher = asyncio.create_task(_watch(app))


async def stop_watcher(app) -> None:
    task = getattr(app.state, "config_watcher", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    app.state.config_watcher = None


__all__ = [
    "CHANNEL",
    "POLL_INTERVAL_SECONDS",
    "current_version",
    "load_config",
    "reset_value",
    "seed_from_env",
    "set_value",
    "start_watcher",
    "stop_watcher",
]
