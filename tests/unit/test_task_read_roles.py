"""What reading a task back costs a console session.

A session carries no scopes and passes every scope check, so the scope table in
`scopes_for_task` bounds keys and nothing else. These pin the role half, and the
one place two checks for "may handle a jar" have to give the same answer.
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from dtk.api.deps import Principal
from dtk.api.routes.operations import Maintenance, Operation, role_for_task
from dtk.api.routes.support import handles_credentials, manage_pool
from dtk.core.errors import ForbiddenScope
from dtk.core.types import Scope, UserRole


@pytest.mark.parametrize(
    ("endpoint", "floor"),
    [
        (Maintenance.IDENTITY_MINT.value, UserRole.OPERATOR),
        (Maintenance.IDENTITY_TEST.value, UserRole.OPERATOR),
        (Maintenance.IDENTITY_SESSION.value, UserRole.OPERATOR),
        (Maintenance.PROXY_TEST.value, UserRole.OPERATOR),
        (Maintenance.DIAGNOSE.value, UserRole.OPERATOR),
        (Maintenance.NOTIFY_TEST.value, UserRole.OPERATOR),
        (Maintenance.BACKUP.value, UserRole.ADMIN),
        (Maintenance.BACKUP_RESTORE.value, UserRole.ADMIN),
    ],
)
def test_maintenance_needs_the_role_that_starts_it(endpoint: str, floor: UserRole) -> None:
    assert role_for_task(endpoint) is floor


@pytest.mark.parametrize(
    "endpoint",
    [
        Operation.PARSE.value,
        "douyin.content_detail",
        Maintenance.MEDIA_DOWNLOAD.value,
        Maintenance.MEDIA_TRANSCRIBE.value,
        Maintenance.ARCHIVE_AVAILABILITY.value,
        Maintenance.ARCHIVE_BACKFILL.value,
    ],
)
def test_what_a_viewer_may_start_adds_no_floor(endpoint: str) -> None:
    """Reads, downloads, transcriptions and library jobs are a viewer's to run."""
    assert role_for_task(endpoint) is UserRole.DEMO


def test_an_endpoint_this_build_does_not_know_needs_an_admin() -> None:
    """A row written by a newer version: the safe direction, as for its scope."""
    assert role_for_task("something.from.the.future") is UserRole.ADMIN


def test_every_maintenance_job_is_placed() -> None:
    """A new job must be a decision here, not a fall-through to no floor."""
    placed = {
        Maintenance.MEDIA_DOWNLOAD,
        Maintenance.MEDIA_TRANSCRIBE,
        Maintenance.ARCHIVE_AVAILABILITY,
        Maintenance.ARCHIVE_BACKFILL,
    }
    for job in Maintenance:
        if job not in placed:
            assert role_for_task(job.value) is not UserRole.DEMO, job


def _principals() -> list[Principal]:
    scope_sets: list[frozenset[Scope]] = [
        frozenset(),
        frozenset({Scope.DOUYIN_READ}),
        frozenset({Scope.IDENTITY_MANAGE}),
        frozenset({Scope.ADMIN}),
    ]
    out = []
    for role, scopes, keyed in itertools.product(UserRole, scope_sets, (False, True)):
        if not keyed and scopes:
            continue  # a session holds no scopes
        out.append(
            Principal(
                user_id=uuid.uuid4(),
                role=role,
                scopes=scopes,
                api_key_id=uuid.uuid4() if keyed else None,
                rate_limit_per_min=None,
            )
        )
    return out


@pytest.mark.parametrize("principal", _principals(), ids=lambda p: f"{p.role}-{sorted(p.scopes)}")
async def test_the_inline_check_agrees_with_the_route_guard(principal: Principal) -> None:
    """`handles_credentials` gates results and parameters inside routes that
    are open to readers; `manage_pool` gates whole routes. The first once kept
    the scopes and lost the role. Called directly, the guard skips its rate
    limiter, which is not what is being compared."""
    try:
        await manage_pool(principal)
        admitted = True
    except ForbiddenScope:
        admitted = False

    assert handles_credentials(principal) is admitted
