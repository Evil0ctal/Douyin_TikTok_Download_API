"""Per-endpoint scheduling policy.

Quota is keyed on ``(identity, endpoint)`` rather than on the identity alone.
Without the endpoint dimension one identity can spend its entire budget on the
single most sensitive call, which reads as "this visitor only ever does one
thing, and does it constantly" - a stronger signal than an even spread across
several endpoints. See docs/design/03-scheduler.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from dtk.core.types import Platform


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    endpoint: str
    #: Bucket depth, i.e. the burst an identity may make after idling.
    capacity: int
    #: Steady-state rate. 0.2 means one request every five seconds.
    refill_per_sec: float
    #: Concurrency is 1 by design and should stay 1. A real session does not
    #: issue parallel API calls; scale throughput with more identities instead.
    max_concurrency: int = 1
    #: Multiplies the backoff after a risk-control hit.
    risk_weight: float = 1.0


#: Conservative defaults. Sensitive list endpoints get a slow refill; single
#: detail lookups are comparatively cheap.
_POLICIES: dict[str, EndpointPolicy] = {
    p.endpoint: p
    for p in [
        EndpointPolicy("douyin.content.detail", capacity=5, refill_per_sec=0.30, risk_weight=1.0),
        EndpointPolicy("douyin.author.profile", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("douyin.author.posts", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("douyin.content.comments", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy(
            "douyin.content.comment_replies", capacity=3, refill_per_sec=0.15, risk_weight=1.5
        ),
        EndpointPolicy("tiktok.content.detail", capacity=5, refill_per_sec=0.30, risk_weight=1.0),
        EndpointPolicy("tiktok.author.profile", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("tiktok.author.posts", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("tiktok.content.comments", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy(
            "tiktok.content.comment_replies", capacity=3, refill_per_sec=0.15, risk_weight=1.5
        ),
    ]
}

#: Applied to any endpoint without an explicit entry. Deliberately strict: an
#: unregistered endpoint is more likely a mistake than a hot path.
DEFAULT_POLICY = EndpointPolicy("default", capacity=3, refill_per_sec=0.15, risk_weight=1.5)


def policy_for(endpoint: str) -> EndpointPolicy:
    return _POLICIES.get(endpoint, DEFAULT_POLICY)


def register_policy(policy: EndpointPolicy) -> None:
    """Override a policy at runtime, e.g. from the settings table."""
    _POLICIES[policy.endpoint] = policy


def known_endpoints(platform: Platform | None = None) -> list[str]:
    if platform is None:
        return sorted(_POLICIES)
    return sorted(e for e in _POLICIES if e.startswith(f"{platform.value}."))


__all__ = [
    "DEFAULT_POLICY",
    "EndpointPolicy",
    "known_endpoints",
    "policy_for",
    "register_policy",
]
