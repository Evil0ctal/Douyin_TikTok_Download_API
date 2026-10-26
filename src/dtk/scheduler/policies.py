"""Per-endpoint scheduling policy.

Endpoint names here MUST match the keys the platform adapters register. They are
one namespace shared by the scheduler, the worker registry, MCP routing, the CLI
and request_log, and a mismatch does not raise - it silently falls through to
DEFAULT_POLICY, so every tuned quota and risk weight would quietly stop applying.
`tests/unit/test_policy_coverage.py` fails if the two ever drift apart.

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
        # The session checks read nothing and page nothing, so the quota is
        # the guard that matters: nobody needs to ask whose session this is
        # five times a second. The risk weight stays at the floor every other
        # endpoint sits at or above - a probe records no outcome against the
        # identity it measures, so a lighter weight here would protect nothing
        # and would quietly discount a real refusal if the endpoint were ever
        # called through the ordinary pipeline.
        EndpointPolicy("douyin.session_check", capacity=3, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("tiktok.session_check", capacity=3, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("douyin.content_detail", capacity=5, refill_per_sec=0.30, risk_weight=1.0),
        EndpointPolicy("douyin.author_profile", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("douyin.author_posts", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("douyin.comments", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("douyin.comment_replies", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("douyin.author_likes", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("douyin.mix_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        # Owner-only: it can only ever be asked with an imported identity, so
        # every call spends the one identity an operator cannot replace.
        EndpointPolicy(
            "douyin.author_collections", capacity=3, refill_per_sec=0.12, risk_weight=1.8
        ),
        EndpointPolicy("douyin.author_bookmarks", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("douyin.collection_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        # One batch per post, never paged, but a recommender: what a page asks
        # once per post opened, so faster than a list and slower than a detail.
        EndpointPolicy("douyin.related_posts", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        # A sound is one small object, like a profile; its posts page like a mix.
        EndpointPolicy("douyin.music_detail", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("douyin.music_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("douyin.hashtag_detail", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("douyin.author_mixes", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("douyin.live_room", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("douyin.live_room_by_id", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        # One call answers for up to a hundred rooms, so a poller spends one
        # token where it would have spent a hundred on /live.
        EndpointPolicy("douyin.live_status", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        # 1.2 MB for a full room's 200 viewers, so a slower bucket than the
        # room's own.
        EndpointPolicy("douyin.live_ranking", capacity=3, refill_per_sec=0.15, risk_weight=1.2),
        # The same board for everyone, cached; asked rarely, like Bilibili's.
        EndpointPolicy("douyin.trending", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("tiktok.content_detail", capacity=5, refill_per_sec=0.30, risk_weight=1.0),
        EndpointPolicy("tiktok.author_profile", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("tiktok.author_posts", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("tiktok.comments", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("tiktok.comment_replies", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("tiktok.author_likes", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy(
            "tiktok.author_collections", capacity=3, refill_per_sec=0.12, risk_weight=1.8
        ),
        EndpointPolicy("tiktok.author_bookmarks", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        # Keyed by a folder rather than an account, and a public folder is
        # guest-readable, so this sits with mix_posts rather than with the
        # owner-only pair above it.
        EndpointPolicy("tiktok.collection_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("tiktok.mix_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("tiktok.author_reposts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        # One small object, not a page: closer to author_profile than to a list.
        EndpointPolicy(
            "tiktok.collection_detail", capacity=4, refill_per_sec=0.20, risk_weight=1.2
        ),
        EndpointPolicy("tiktok.author_followers", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("tiktok.author_following", capacity=3, refill_per_sec=0.12, risk_weight=1.8),
        EndpointPolicy("tiktok.related_posts", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("tiktok.music_detail", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("tiktok.music_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("tiktok.hashtag_detail", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("tiktok.hashtag_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        # Search is the endpoint platforms watch hardest (doc 11 says so of
        # all of them), so it gets the slowest bucket here and the heaviest
        # weight a list carries.
        EndpointPolicy("tiktok.search_posts", capacity=2, refill_per_sec=0.08, risk_weight=1.8),
        EndpointPolicy("tiktok.search_authors", capacity=2, refill_per_sec=0.08, risk_weight=1.8),
        EndpointPolicy("tiktok.author_mixes", capacity=3, refill_per_sec=0.15, risk_weight=1.5),
        EndpointPolicy("tiktok.live_room", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("tiktok.live_room_by_id", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        # One call answers for up to a hundred rooms, so a poller spends one
        # token where it would have spent a hundred on /live.
        EndpointPolicy("tiktok.live_status", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("bilibili.session_check", capacity=3, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("bilibili.content_detail", capacity=5, refill_per_sec=0.30, risk_weight=1.0),
        # Asked once per detail, by the fetch layer, for the file itself. Same
        # rate as the detail it follows, or every detail would wait on it.
        EndpointPolicy(
            "bilibili.content_streams", capacity=5, refill_per_sec=0.30, risk_weight=1.0
        ),
        EndpointPolicy("bilibili.author_profile", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        # Slower than the other platforms' feeds: a burst of a dozen requests
        # from one egress was enough to turn this endpoint into 412 pages for a
        # few minutes on 2026-10-18.
        EndpointPolicy("bilibili.author_posts", capacity=2, refill_per_sec=0.08, risk_weight=1.8),
        EndpointPolicy("bilibili.comments", capacity=3, refill_per_sec=0.15, risk_weight=1.2),
        EndpointPolicy(
            "bilibili.comment_replies", capacity=3, refill_per_sec=0.15, risk_weight=1.2
        ),
        EndpointPolicy(
            "bilibili.author_collections", capacity=3, refill_per_sec=0.15, risk_weight=1.2
        ),
        EndpointPolicy(
            "bilibili.collection_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.2
        ),
        EndpointPolicy("bilibili.content_parts", capacity=5, refill_per_sec=0.30, risk_weight=1.0),
        # The boards are the same lists for everyone and are cached, so they
        # are asked rarely; unsigned and light, they sit with the detail.
        EndpointPolicy("bilibili.popular", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        EndpointPolicy("bilibili.popular_weekly", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        # Asked once per weekly page, right after the index. Same rate, or
        # every page would wait on it.
        EndpointPolicy(
            "bilibili.popular_weekly_issue", capacity=4, refill_per_sec=0.20, risk_weight=1.0
        ),
        EndpointPolicy(
            "bilibili.popular_precious", capacity=4, refill_per_sec=0.20, risk_weight=1.0
        ),
        # Guarded like the uploader space - without the dm report it answers
        # -352 - so it gets the comment endpoints' slower bucket.
        EndpointPolicy("bilibili.ranking", capacity=3, refill_per_sec=0.15, risk_weight=1.2),
        EndpointPolicy("bilibili.related_posts", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("bilibili.search_posts", capacity=2, refill_per_sec=0.08, risk_weight=1.5),
        EndpointPolicy("bilibili.search_authors", capacity=2, refill_per_sec=0.08, risk_weight=1.5),
        # Unsigned and from the space page, like the folder list beside them.
        EndpointPolicy("bilibili.author_mixes", capacity=3, refill_per_sec=0.15, risk_weight=1.2),
        EndpointPolicy("bilibili.mix_posts", capacity=3, refill_per_sec=0.15, risk_weight=1.2),
        EndpointPolicy("bilibili.live_room", capacity=4, refill_per_sec=0.20, risk_weight=1.2),
        EndpointPolicy("bilibili.trending", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
        # Asked right after a room that is on, as content_streams follows a
        # detail. The same rate, or every live room would wait on it.
        EndpointPolicy("bilibili.live_streams", capacity=4, refill_per_sec=0.20, risk_weight=1.0),
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


def snapshot_policies() -> dict[str, EndpointPolicy]:
    """Copy the current table.

    Paired with :func:`restore_policies` so a caller that registers a policy -
    a test, or a settings reload that later fails - can put the table back. The
    registry is process-global mutable state, and leaving it modified made one
    test's endpoint look like dead configuration to another.
    """
    return dict(_POLICIES)


def restore_policies(saved: dict[str, EndpointPolicy]) -> None:
    _POLICIES.clear()
    _POLICIES.update(saved)


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
    "restore_policies",
    "snapshot_policies",
]
