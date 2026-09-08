"""Endpoint names are one namespace shared across six modules.

The scheduler, the worker registry, MCP routing, the CLI, request_log and the
platform adapters all key on the same string. A mismatch does not raise: the
scheduler simply falls through to DEFAULT_POLICY, so every tuned quota and risk
weight stops applying and nothing says so. That silent-failure shape is why this
file exists.
"""

from __future__ import annotations

import pytest

from dtk.platforms import available_platforms, get_adapter
from dtk.scheduler.policies import DEFAULT_POLICY, known_endpoints, policy_for


def registered_endpoints() -> list[str]:
    return sorted(
        name
        for platform in available_platforms()
        for name in get_adapter(platform).endpoints.names()
    )


def test_there_are_endpoints_to_check():
    assert registered_endpoints()


@pytest.mark.parametrize("endpoint", registered_endpoints())
def test_every_registered_endpoint_has_an_explicit_policy(endpoint):
    policy = policy_for(endpoint)
    assert policy is not DEFAULT_POLICY, (
        f"{endpoint} has no explicit policy and silently inherits the default; "
        "its tuned quota and risk weight are not being applied"
    )
    assert policy.endpoint == endpoint


def test_no_policy_names_an_endpoint_that_does_not_exist():
    """A stale policy is dead configuration that reads as live."""
    registered = set(registered_endpoints())
    orphans = [e for e in known_endpoints() if e not in registered]
    assert not orphans, f"policies for unregistered endpoints: {orphans}"


def test_policies_are_conservative():
    for endpoint in known_endpoints():
        policy = policy_for(endpoint)
        # One in-flight request per identity. Throughput scales by pool size,
        # not by making a single identity behave like several.
        assert policy.max_concurrency == 1
        assert 0 < policy.refill_per_sec <= 1.0, f"{endpoint} refills faster than 1/s"
        assert 1 <= policy.capacity <= 10, f"{endpoint} allows an implausible burst"
        assert policy.risk_weight >= 1.0


def test_list_endpoints_are_throttled_harder_than_detail_lookups():
    """Enumerating an author's catalogue is more sensitive than one lookup."""
    for platform in available_platforms():
        detail = policy_for(f"{platform}.content_detail")
        posts = policy_for(f"{platform}.author_posts")
        assert posts.refill_per_sec < detail.refill_per_sec
        assert posts.risk_weight > detail.risk_weight


# --------------------------------------------------------------------------
# The other endpoint namespace
#
# The console submits six jobs that are not platform reads. They travel as task
# endpoints through the same field, so the same silent-failure shape applies -
# and it fired: the worker had only the platform registry, so every one of these
# failed with "unknown endpoint" while the console showed a spinner and then a
# generic error. Nothing connected the enum the routes submit to the table the
# worker dispatches on, which is what these assert.
# --------------------------------------------------------------------------


def test_every_maintenance_endpoint_the_routes_submit_has_a_handler():
    from dtk.api.routes.operations import Maintenance
    from dtk.worker.ops import ENDPOINTS

    submitted = {member.value for member in Maintenance}
    missing = sorted(submitted - ENDPOINTS)
    assert not missing, (
        f"the console submits {missing} but the worker has no handler; each will "
        "fail with 'unknown endpoint' after the user waits for it"
    )


def test_no_handler_exists_for_an_endpoint_nothing_submits():
    """The other direction: a handler left behind by a renamed operation."""
    from dtk.api.routes.operations import Maintenance
    from dtk.worker.ops import ENDPOINTS

    submitted = {member.value for member in Maintenance}
    orphaned = sorted(ENDPOINTS - submitted)
    assert not orphaned, f"handlers no route submits any more: {orphaned}"


def test_the_two_dispatch_tables_do_not_overlap():
    """A name in both would be ambiguous, and the worker checks one of them first."""
    from dtk.worker import registry
    from dtk.worker.ops import ENDPOINTS

    overlap = sorted(ENDPOINTS & set(registry.ENDPOINTS))
    assert not overlap, f"claimed by both the platform registry and maintenance: {overlap}"


def test_the_handler_table_matches_the_declared_endpoint_set():
    """ENDPOINTS is what the worker asks; the table is what actually runs."""
    from dtk.worker.ops import ENDPOINTS, _handlers

    assert set(_handlers()) == ENDPOINTS
