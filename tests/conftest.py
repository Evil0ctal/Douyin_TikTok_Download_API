"""Shared fixtures.

The scheduler's policy table is process-global mutable state. A test that
registers an endpoint leaves it there, where the next test sees it as
configuration for an endpoint that does not exist. Snapshotting around every
test keeps that from turning into a false failure somewhere else.
"""

from __future__ import annotations

import pytest

from dtk.scheduler.policies import restore_policies, snapshot_policies


@pytest.fixture(autouse=True)
def _isolated_policy_registry():
    saved = snapshot_policies()
    try:
        yield
    finally:
        restore_policies(saved)
