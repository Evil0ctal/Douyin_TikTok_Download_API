"""The media allowlist: which CDN hosts the downloader may be pointed at.

This list is a security boundary rather than a convenience, so the tests are
written around the ways a host check goes wrong quietly: a suffix match that
accepts a lookalike domain, an apex that falls through the subdomain rule, and
a platform's own list widening to cover the other platform's.
"""

from __future__ import annotations

import pytest

from dtk.core.types import Platform
from dtk.media import domains


@pytest.mark.parametrize(
    "url",
    [
        # Measured on 2026-09-08 from live parses on this machine.
        "https://v9-v2-mps-cdn.douyinvod.com/abc/video/tos/cn/x/?a=6383",
        "https://v5-dy-ov-experiment.zjcdn.com/x/video/tos/cn/y/",
        "https://p3-pc-sign.douyinpic.com/aweme/cover.jpeg",
        "https://sf11-cdn-tos.douyinstatic.com/obj/x.mp3",
    ],
)
def test_measured_douyin_hosts_are_allowed(url: str) -> None:
    assert domains.refusal(url, Platform.DOUYIN) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://v16-webapp-prime.us.tiktok.com/video/tos/useast5/x/",
        "https://p16-common-sign.tiktokcdn-us.com/obj/cover",
    ],
)
def test_measured_tiktok_hosts_are_allowed(url: str) -> None:
    assert domains.refusal(url, Platform.TIKTOK) is None


def test_a_lookalike_domain_is_refused() -> None:
    # The failure a plain endswith() makes, and the reason matching is on a
    # label boundary.
    reason = domains.refusal("https://evildouyinvod.com/video.mp4", Platform.DOUYIN)
    assert reason is not None
    assert "evildouyinvod.com" in reason


def test_a_domain_prefix_attack_is_refused() -> None:
    reason = domains.refusal("https://douyinvod.com.attacker.example/x", Platform.DOUYIN)
    assert reason is not None


def test_one_platform_cannot_reach_the_others_cdn() -> None:
    # Sending the union of both lists would make this indistinguishable from a
    # normal job, which is why the allowlist is narrowed per platform.
    assert domains.refusal("https://v16-webapp-prime.us.tiktok.com/x", Platform.DOUYIN)
    assert domains.refusal("https://v9-v2-mps-cdn.douyinvod.com/x", Platform.TIKTOK)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/video.mp4",
        "https://localhost/video.mp4",
        "https://192.168.1.10/video.mp4",
        "https://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@v9-v2-mps-cdn.douyinvod.com/x",
    ],
)
def test_the_obvious_ssrf_shapes_are_refused(url: str) -> None:
    assert domains.refusal(url, Platform.DOUYIN) is not None


def test_a_refusal_names_the_host() -> None:
    # The realistic failure is a regional CDN nobody here has seen yet, and the
    # difference between a bug report and a mystery is whether the host is in
    # the message.
    reason = domains.refusal("https://p42-sign.example.com/x.jpg", Platform.TIKTOK)
    assert reason is not None
    assert "p42-sign.example.com" in reason


def test_allowed_mirrors_keeps_order_and_reports_the_rest() -> None:
    keep, refused = domains.allowed_mirrors(
        [
            "https://v16-webapp-prime.us.tiktok.com/a",
            "https://evil.example/b",
            "https://p16-common-sign.tiktokcdn-us.com/c",
            "https://v16-webapp-prime.us.tiktok.com/a",  # duplicate
        ],
        Platform.TIKTOK,
    )
    assert keep == [
        "https://v16-webapp-prime.us.tiktok.com/a",
        "https://p16-common-sign.tiktokcdn-us.com/c",
    ]
    assert refused and "evil.example" in refused[0]


def test_the_page_allowlist_and_the_media_allowlist_are_not_the_same_list() -> None:
    # They answer different questions and widening one must not widen the
    # other: a share link has no business pointing at a video CDN.
    from dtk.urls.patterns import ALLOWED_DOMAINS

    assert "douyinvod.com" not in ALLOWED_DOMAINS
    assert "zjcdn.com" not in ALLOWED_DOMAINS
    assert domains.MEDIA_DOMAINS - ALLOWED_DOMAINS
