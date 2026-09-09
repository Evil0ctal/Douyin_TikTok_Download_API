"""Typed async data access.

One repository per table group, each holding an ``AsyncSession`` it never
commits: the transaction boundary belongs to the caller
(:func:`dtk.core.db.session_scope`), so a service can compose several
repositories into one atomic unit of work.

Two conventions worth knowing before using these:

* State changes that matter are compare-and-set. ``IdentityRepository.transition``
  moves an identity only if it is still in one of the states the caller
  expects, and reports whether it won. Read-then-write across an await point
  loses races between workers; the scheduler runs several.
* Bulk updates are issued with ``synchronize_session=False``. They are the
  fastest form and these repositories are used from short-lived sessions, but
  it means an ORM object already loaded in the same session keeps its old
  attribute values until it is refreshed.

Validation belongs to the layer above: values reaching ``SettingsRepository``
must already have passed :func:`dtk.core.config.coerce`, and cookie blobs must
already be ciphertext from :class:`dtk.core.crypto.Cipher`.

The append-only hypertable writers live in :mod:`dtk.db.timeseries` and are
re-exported here, so every repository is importable from one place.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from dtk.core.logging import get_logger
from dtk.core.types import IdentityState, Platform, TaskState, UserRole
from dtk.db.base import Repository, affected, utcnow
from dtk.db.models import (
    ApiKey,
    AuditLog,
    Identity,
    Proxy,
    Setting,
    SettingsVersion,
    Task,
    User,
)
from dtk.db.timeseries import (
    ContentSnapshotRepository,
    IdentityEventRepository,
    RequestLogRepository,
    request_log_row,
)

log = get_logger(__name__)

#: Identity states the scheduler may lease from, in preference order.
SCHEDULABLE_STATES: tuple[IdentityState, ...] = (IdentityState.ACTIVE, IdentityState.DEGRADED)

_NO_SYNC = {"synchronize_session": False}


def _states(value: IdentityState | Iterable[IdentityState]) -> list[str]:
    if isinstance(value, IdentityState):
        return [value.value]
    return [s.value for s in value]


class UserRepository(Repository):
    """Console accounts. A self-hosted instance has a handful."""

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        role: UserRole = UserRole.ADMIN,
    ) -> User:
        user = User(username=username, password_hash=password_hash, role=role.value)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return (await self.session.scalars(stmt)).one_or_none()

    async def list_all(self) -> Sequence[User]:
        stmt = select(User).order_by(User.created_at)
        return (await self.session.scalars(stmt)).all()

    async def count(self) -> int:
        return int(
            (await self.session.execute(select(func.count()).select_from(User))).scalar_one()
        )

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> bool:
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash)
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def set_role(self, user_id: uuid.UUID, role: UserRole) -> bool:
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(role=role.value)
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def touch_login(self, user_id: uuid.UUID, *, at: datetime | None = None) -> None:
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        await self.session.execute(stmt)

    async def delete(self, user_id: uuid.UUID) -> bool:
        stmt = delete(User).where(User.id == user_id).execution_options(**_NO_SYNC)
        return affected(await self.session.execute(stmt)) == 1


class ApiKeyRepository(Repository):
    """API credentials. Only the prefix and the digest are ever stored."""

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        name: str,
        prefix: str,
        key_hash: str,
        scopes: Sequence[str] = (),
        rate_limit: int | None = None,
        expires_at: datetime | None = None,
    ) -> ApiKey:
        key = ApiKey(
            user_id=user_id,
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            scopes=list(scopes),
            rate_limit=rate_limit,
            expires_at=expires_at,
        )
        self.session.add(key)
        await self.session.flush()
        return key

    async def get(self, key_id: uuid.UUID) -> ApiKey | None:
        return await self.session.get(ApiKey, key_id)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        """Look a key up by digest, revoked and expired ones included.

        Authentication should prefer :meth:`get_usable_by_hash`; this exists so
        the console can explain *why* a key was rejected.
        """
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        return (await self.session.scalars(stmt)).one_or_none()

    async def get_usable_by_hash(
        self, key_hash: str, *, at: datetime | None = None
    ) -> ApiKey | None:
        """The authentication path: neither revoked nor expired."""
        now = at or utcnow()
        stmt = select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),
            or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )
        return (await self.session.scalars(stmt)).one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[ApiKey]:
        stmt = select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
        return (await self.session.scalars(stmt)).all()

    async def list_all(self) -> Sequence[ApiKey]:
        stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
        return (await self.session.scalars(stmt)).all()

    async def revoke(self, key_id: uuid.UUID, *, at: datetime | None = None) -> bool:
        stmt = (
            update(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def touch_used(self, key_id: uuid.UUID, *, at: datetime | None = None) -> None:
        stmt = (
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        await self.session.execute(stmt)

    async def delete(self, key_id: uuid.UUID) -> bool:
        stmt = delete(ApiKey).where(ApiKey.id == key_id).execution_options(**_NO_SYNC)
        return affected(await self.session.execute(stmt)) == 1


class ProxyRepository(Repository):
    """Egress endpoints. ``url_encrypted`` is ciphertext, aad = the row id."""

    async def create(
        self,
        *,
        url_encrypted: bytes,
        label: str | None = None,
        country: str | None = None,
        timezone: str | None = None,
    ) -> Proxy:
        proxy = Proxy(url_encrypted=url_encrypted, label=label, country=country, timezone=timezone)
        self.session.add(proxy)
        await self.session.flush()
        return proxy

    async def get(self, proxy_id: uuid.UUID) -> Proxy | None:
        return await self.session.get(Proxy, proxy_id)

    async def list_all(self, *, healthy_only: bool = False) -> Sequence[Proxy]:
        stmt = select(Proxy)
        if healthy_only:
            stmt = stmt.where(Proxy.healthy.is_(True))
        return (await self.session.scalars(stmt.order_by(Proxy.created_at))).all()

    async def list_unbound(self, platform: Platform) -> Sequence[Proxy]:
        """Healthy proxies not already carrying a live identity of ``platform``.

        Doc 02 step 1: minting picks an egress that no active identity is using,
        so two identities never share an exit address on the same platform.
        """
        taken = select(Identity.proxy_id).where(
            Identity.platform == platform.value,
            Identity.proxy_id.is_not(None),
            Identity.state.not_in([IdentityState.RETIRED.value]),
        )
        stmt = (
            select(Proxy)
            .where(Proxy.healthy.is_(True), Proxy.id.not_in(taken))
            .order_by(Proxy.created_at)
        )
        return (await self.session.scalars(stmt)).all()

    async def set_health(
        self,
        proxy_id: uuid.UUID,
        *,
        healthy: bool,
        checked_at: datetime | None = None,
    ) -> bool:
        stmt = (
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(healthy=healthy, last_check_at=checked_at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def set_geo(
        self, proxy_id: uuid.UUID, *, country: str | None, timezone: str | None
    ) -> bool:
        stmt = (
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(country=country, timezone=timezone)
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def delete(self, proxy_id: uuid.UUID) -> bool:
        stmt = delete(Proxy).where(Proxy.id == proxy_id).execution_options(**_NO_SYNC)
        return affected(await self.session.execute(stmt)) == 1


class IdentityRepository(Repository):
    """The identity pool.

    Every state change is a conditional UPDATE. Several workers race for the
    same identities, and a lost race has to be visible to the caller rather
    than silently overwriting somebody else's decision.
    """

    async def create(
        self,
        *,
        platform: Platform,
        cookies_encrypted: bytes,
        fingerprint: Mapping[str, Any],
        source: str,
        proxy_id: uuid.UUID | None = None,
        authenticated: bool = False,
        state: IdentityState = IdentityState.MINTING,
    ) -> Identity:
        identity = Identity(
            platform=platform.value,
            cookies_encrypted=cookies_encrypted,
            fingerprint=dict(fingerprint),
            source=source,
            proxy_id=proxy_id,
            authenticated=authenticated,
            state=state.value,
        )
        self.session.add(identity)
        await self.session.flush()
        return identity

    async def get(self, identity_id: uuid.UUID) -> Identity | None:
        return await self.session.get(Identity, identity_id)

    async def list_by_state(
        self,
        *,
        platform: Platform | None = None,
        states: IdentityState | Iterable[IdentityState] = SCHEDULABLE_STATES,
        limit: int | None = None,
    ) -> Sequence[Identity]:
        """Candidates for the scheduler: least recently used first.

        NULLS FIRST is deliberate: an identity that has never been used is the
        least recently used one there is.
        """
        stmt = select(Identity).where(Identity.state.in_(_states(states)))
        if platform is not None:
            stmt = stmt.where(Identity.platform == platform.value)
        stmt = stmt.order_by(Identity.last_used_at.asc().nulls_first())
        if limit is not None:
            stmt = stmt.limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def list_by_proxy(self, proxy_id: uuid.UUID) -> Sequence[Identity]:
        stmt = select(Identity).where(
            Identity.proxy_id == proxy_id,
            Identity.state != IdentityState.RETIRED.value,
        )
        return (await self.session.scalars(stmt)).all()

    async def count_by_state(self, *, platform: Platform | None = None) -> dict[str, int]:
        stmt = select(Identity.state, func.count()).group_by(Identity.state)
        if platform is not None:
            stmt = stmt.where(Identity.platform == platform.value)
        rows = (await self.session.execute(stmt)).all()
        return {state: int(count) for state, count in rows}

    async def transition(
        self,
        identity_id: uuid.UUID,
        *,
        expected: IdentityState | Iterable[IdentityState],
        new: IdentityState,
        cooldown_until: datetime | None = None,
        clear_cooldown: bool = False,
        reset_fails: bool = False,
    ) -> bool:
        """Move an identity between states, only from an expected one.

        Returns False when another worker moved it first; the caller must treat
        that as "not mine" rather than retrying blindly.
        """
        values: dict[str, Any] = {"state": new.value}
        if cooldown_until is not None:
            values["cooldown_until"] = cooldown_until
        elif clear_cooldown:
            values["cooldown_until"] = None
        if reset_fails:
            values["consecutive_fails"] = 0
        stmt = (
            update(Identity)
            .where(Identity.id == identity_id, Identity.state.in_(_states(expected)))
            .values(**values)
            .execution_options(**_NO_SYNC)
        )
        changed = affected(await self.session.execute(stmt)) == 1
        if changed:
            log.debug("identity.transition", identity_id=str(identity_id), state=new.value)
        return changed

    async def mark_used(self, identity_id: uuid.UUID, *, at: datetime | None = None) -> None:
        stmt = (
            update(Identity)
            .where(Identity.id == identity_id)
            .values(last_used_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        await self.session.execute(stmt)

    async def record_failure(self, identity_id: uuid.UUID) -> int | None:
        """Increment the failure streak server-side and return the new value."""
        stmt = (
            update(Identity)
            .where(Identity.id == identity_id)
            .values(consecutive_fails=Identity.consecutive_fails + 1)
            .returning(Identity.consecutive_fails)
            .execution_options(**_NO_SYNC)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def reset_failures(self, identity_id: uuid.UUID) -> None:
        stmt = (
            update(Identity)
            .where(Identity.id == identity_id)
            .values(consecutive_fails=0)
            .execution_options(**_NO_SYNC)
        )
        await self.session.execute(stmt)

    async def due_for_reactivation(
        self, *, at: datetime | None = None, limit: int = 100
    ) -> Sequence[Identity]:
        """Cooling identities whose backoff has elapsed."""
        stmt = (
            select(Identity)
            .where(
                Identity.state == IdentityState.COOLING.value,
                Identity.cooldown_until.is_not(None),
                Identity.cooldown_until <= (at or utcnow()),
            )
            .order_by(Identity.cooldown_until)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def retire(
        self, identity_id: uuid.UUID, *, reason: str, at: datetime | None = None
    ) -> bool:
        """Retire an identity and wipe its cookie jar in the same statement.

        Doc 05 is explicit: keeping the statistics of a dead identity is worth
        something, keeping its credentials is not. The column is NOT NULL, so
        the wipe writes empty bytes rather than NULL.
        """
        stmt = (
            update(Identity)
            .where(Identity.id == identity_id, Identity.retired_at.is_(None))
            .values(
                state=IdentityState.RETIRED.value,
                retired_at=at or utcnow(),
                retire_reason=reason,
                cookies_encrypted=b"",
                cooldown_until=None,
            )
            .execution_options(**_NO_SYNC)
        )
        retired = affected(await self.session.execute(stmt)) == 1
        if retired:
            log.info("identity.retired", identity_id=str(identity_id), reason=reason)
        return retired


class TaskRepository(Repository):
    """Asynchronous jobs and the eviction of their payloads."""

    async def create(
        self,
        *,
        endpoint: str,
        params: Mapping[str, Any],
        api_key_id: uuid.UUID | None = None,
    ) -> Task:
        task = Task(endpoint=endpoint, params=dict(params), api_key_id=api_key_id)
        self.session.add(task)
        await self.session.flush()
        return task

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self.session.get(Task, task_id)

    async def list_recent(
        self, *, state: TaskState | None = None, limit: int = 50
    ) -> Sequence[Task]:
        stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if state is not None:
            stmt = stmt.where(Task.state == state.value)
        return (await self.session.scalars(stmt)).all()

    async def mark_running(self, task_id: uuid.UUID, *, at: datetime | None = None) -> bool:
        """Claim a queued task. False means another worker already has it."""
        stmt = (
            update(Task)
            .where(Task.id == task_id, Task.state == TaskState.QUEUED.value)
            .values(state=TaskState.RUNNING.value, started_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def mark_done(
        self, task_id: uuid.UUID, *, result: Mapping[str, Any], at: datetime | None = None
    ) -> bool:
        stmt = (
            update(Task)
            .where(Task.id == task_id, Task.state != TaskState.DONE.value)
            .values(state=TaskState.DONE.value, result=dict(result), finished_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def mark_failed(
        self, task_id: uuid.UUID, *, error: Mapping[str, Any], at: datetime | None = None
    ) -> bool:
        stmt = (
            update(Task)
            .where(Task.id == task_id, Task.state != TaskState.DONE.value)
            .values(state=TaskState.FAILED.value, error=dict(error), finished_at=at or utcnow())
            .execution_options(**_NO_SYNC)
        )
        return affected(await self.session.execute(stmt)) == 1

    async def purge_results(self, *, older_than: timedelta) -> int:
        """Drop payloads of finished tasks, keeping the rows for statistics.

        Doc 05 clears ``result``; ``error`` is cleared on the same schedule so a
        failure payload does not outlive the success payload next to it. A later
        lookup of such a task answers TASK_NOT_FOUND.
        """
        cutoff = utcnow() - older_than
        stmt = (
            update(Task)
            .where(
                Task.finished_at.is_not(None),
                Task.finished_at < cutoff,
                or_(Task.result.is_not(None), Task.error.is_not(None)),
            )
            .values(result=None, error=None)
            .execution_options(**_NO_SYNC)
        )
        purged = affected(await self.session.execute(stmt))
        if purged:
            log.info("tasks.results_purged", rows=purged)
        return int(purged)

    async def delete_old(self, *, older_than: timedelta) -> int:
        """Delete finished task metadata past its retention window.

        Finished only. Without the state filter this deletes by age alone, so a
        task still queued or running past the window is removed out from under
        its own Redis queue entry - the worker then pops an id whose row does
        not exist and drops it, which is indistinguishable from the commit race
        this filter was added alongside. A task older than the window that has
        still not finished is a symptom to look at, not a row to delete.
        """
        cutoff = utcnow() - older_than
        stmt = (
            delete(Task)
            .where(
                Task.created_at < cutoff,
                Task.state.in_((TaskState.DONE.value, TaskState.FAILED.value)),
            )
            .execution_options(**_NO_SYNC)
        )
        deleted = affected(await self.session.execute(stmt))
        if deleted:
            log.info("tasks.rows_deleted", rows=deleted)
        return int(deleted)


class SettingsRepository(Repository):
    """The runtime configuration layer, plus the version every process polls.

    Values must already be validated with :func:`dtk.core.config.coerce`; this
    layer stores what it is given.
    """

    async def get_all(self) -> dict[str, Any]:
        rows = (await self.session.execute(select(Setting.key, Setting.value))).all()
        return {row.key: row.value for row in rows}

    async def get(self, key: str) -> Setting | None:
        return await self.session.get(Setting, key)

    async def set(self, key: str, value: Any, *, updated_by: uuid.UUID | None = None) -> int:
        """Upsert one setting and return the new configuration version."""
        stmt = pg_insert(Setting).values(
            key=key, value=value, updated_by=updated_by, updated_at=func.now()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Setting.key],
            set_={"value": stmt.excluded.value, "updated_by": updated_by, "updated_at": func.now()},
        )
        await self.session.execute(stmt)
        version = await self.bump_version()
        log.info("settings.updated", key=key, version=version)
        return version

    async def set_many(
        self, values: Mapping[str, Any], *, updated_by: uuid.UUID | None = None
    ) -> int:
        """Write several settings under a single version bump.

        Used when initialization seeds the environment defaults into the
        database: one bump means workers reload once, not once per key.
        """
        if not values:
            return await self.current_version()
        stmt = pg_insert(Setting).values(
            [
                {"key": key, "value": value, "updated_by": updated_by, "updated_at": func.now()}
                for key, value in values.items()
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Setting.key],
            set_={
                "value": stmt.excluded.value,
                "updated_by": stmt.excluded.updated_by,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        version = await self.bump_version()
        log.info("settings.seeded", keys=len(values), version=version)
        return version

    async def delete(self, key: str) -> int | None:
        """Reset a setting to its environment or code default.

        Returns the new version, or None when the key was not stored at all.
        """
        stmt = delete(Setting).where(Setting.key == key).execution_options(**_NO_SYNC)
        if affected(await self.session.execute(stmt)) != 1:
            return None
        version = await self.bump_version()
        log.info("settings.deleted", key=key, version=version)
        return version

    async def current_version(self) -> int:
        stmt = select(SettingsVersion.version).where(SettingsVersion.id == 1)
        version = (await self.session.execute(stmt)).scalar_one_or_none()
        return int(version) if version is not None else 0

    async def bump_version(self) -> int:
        """Increment the singleton counter, recreating it if it went missing."""
        stmt = (
            update(SettingsVersion)
            .where(SettingsVersion.id == 1)
            .values(version=SettingsVersion.version + 1)
            .returning(SettingsVersion.version)
            .execution_options(**_NO_SYNC)
        )
        version = (await self.session.execute(stmt)).scalar_one_or_none()
        if version is None:
            # The migration seeds this row; a hand-edited database might not
            # have it, and a missing counter must not break configuration.
            insert_stmt = (
                pg_insert(SettingsVersion)
                .values(id=1, version=1)
                .on_conflict_do_update(index_elements=[SettingsVersion.id], set_={"version": 1})
                .returning(SettingsVersion.version)
            )
            version = (await self.session.execute(insert_stmt)).scalar_one()
        return int(version)


class AuditRepository(Repository):
    """Sensitive operations. Never store a credential in ``detail``."""

    async def record(
        self,
        *,
        action: str,
        user_id: uuid.UUID | None = None,
        api_key_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        detail: Mapping[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            action=action,
            user_id=user_id,
            api_key_id=api_key_id,
            target_type=target_type,
            target_id=target_id,
            detail=dict(detail) if detail is not None else None,
            ip=ip,
            user_agent=user_agent,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_recent(
        self,
        *,
        limit: int = 100,
        before: datetime | None = None,
        action: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Sequence[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)
        if before is not None:
            stmt = stmt.where(AuditLog.ts < before)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        return (await self.session.scalars(stmt)).all()


__all__ = [
    "SCHEDULABLE_STATES",
    "ApiKeyRepository",
    "AuditRepository",
    "ContentSnapshotRepository",
    "IdentityEventRepository",
    "IdentityRepository",
    "ProxyRepository",
    "RequestLogRepository",
    "SettingsRepository",
    "TaskRepository",
    "UserRepository",
    "request_log_row",
]
