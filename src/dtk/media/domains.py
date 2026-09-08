"""Which hosts the downloader may be pointed at.

This list did not exist before. :data:`dtk.urls.patterns.ALLOWED_DOMAINS`
covers *page* domains - where a share link may point - and
``security.url_allowlist`` is documented as admitting one stubborn redirect
hop. Neither describes a CDN, so until this module there was no answer to "may
this instance fetch bytes from that host", which is precisely the question the
downloader asks on every transfer.

Kept separate from the page allowlist on purpose. They answer different
questions and widening one must not widen the other: ``douyinvod.com`` should
never be somewhere a share link may point, and ``www.douyin.com`` is not a
place to pull a 250 MB file from.

Provenance matters here, so it is recorded per entry: hosts marked *measured*
were observed serving media on 2026-09-08 from live parses on this machine;
the rest are sibling networks of the same operators, included because a list
that only covers the regions its author happened to test fails silently and
regionally. A host that is refused is named in the job error, so an unlisted
CDN becomes a bug report rather than a download that never explains itself.
"""

from __future__ import annotations

from typing import Final

from dtk.core.types import Platform
from dtk.urls.parse import is_private_host

#: Douyin and ByteDance media networks.
DOUYIN_MEDIA_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "douyinvod.com",  # measured: v9-v2-mps-cdn.douyinvod.com, video
        "zjcdn.com",  # measured: v5-dy-ov-experiment.zjcdn.com, video
        "douyinpic.com",  # measured: p3-pc-sign.douyinpic.com, covers and avatars
        "douyinstatic.com",  # measured: sf11-cdn-tos.douyinstatic.com, music and static
        "iesdouyin.com",  # measured: the share-page origin also serves images
        "douyin.com",  # measured: some covers are served from the apex network
        "bytecdn.cn",  # sibling network, same operator
        "byteimg.com",  # sibling network, images
        "pstatp.com",  # sibling network, legacy image and static host
        "ibyteimg.com",  # sibling network, images
        "amemv.com",  # sibling network, the older Douyin domain
        "ixigua.com",  # sibling network, video
        "bytedance.com",  # sibling network
    }
)

#: TikTok media networks.
TIKTOK_MEDIA_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "tiktokcdn-us.com",  # measured: p16-common-sign.tiktokcdn-us.com, covers
        "tiktok.com",  # measured: v16-webapp-prime.us.tiktok.com, video
        "tiktokcdn.com",  # sibling network, the global CDN
        "tiktokcdn-eu.com",  # sibling network, EU edge
        "tiktokv.com",  # sibling network
        "tiktokv.us",  # sibling network, US data-residency edge
        "muscdn.com",  # sibling network, music
        "musical.ly",  # sibling network, predates the rename and still resolves
        "ttwstatic.com",  # sibling network, static assets
        "ibytedtos.com",  # sibling network, object storage
        "isnssdk.com",  # sibling network
    }
)

MEDIA_DOMAINS: Final[frozenset[str]] = DOUYIN_MEDIA_DOMAINS | TIKTOK_MEDIA_DOMAINS

#: Per platform, because a job carries the allowlist it is entitled to rather
#: than the union. A Douyin post has no business reaching a TikTok CDN, and
#: sending the whole list would make that indistinguishable from one that does.
MEDIA_DOMAINS_BY_PLATFORM: Final[dict[Platform, frozenset[str]]] = {
    Platform.DOUYIN: DOUYIN_MEDIA_DOMAINS,
    Platform.TIKTOK: TIKTOK_MEDIA_DOMAINS,
}

#: Only these schemes are ever fetched.
ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


def registrable(host: str, domains: frozenset[str]) -> str | None:
    """The allowlisted domain ``host`` belongs to, if any.

    Matched on a label boundary. ``endswith("tiktok.com")`` alone would accept
    ``eviltiktok.com`` and ``endswith(".tiktok.com")`` alone would miss the
    apex; both mistakes are one character wide and neither is visible in a log.
    """
    name = host.strip().rstrip(".").lower()
    for domain in domains:
        if name == domain or name.endswith(f".{domain}"):
            return domain
    return None


def is_media_url(url: str, platform: Platform) -> bool:
    """Whether this URL may be handed to the downloader for ``platform``.

    Deliberately not a parser: it answers yes or no, and every caller that
    needs to say *why* uses :func:`refusal`.
    """
    return refusal(url, platform) is None


def refusal(url: str, platform: Platform) -> str | None:
    """Why this URL may not be fetched, or ``None`` if it may.

    The reason names the host, because the realistic failure is a regional CDN
    nobody here has seen yet - and "host p42-sign.example.com is not an allowed
    media domain" is a bug report, while "download failed" is a mystery.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return "the mirror is not a parseable URL"
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return f"scheme {parts.scheme or '(none)'} is not fetchable"
    if "@" in (parts.netloc or ""):
        return "a mirror carrying credentials is refused"
    host = (parts.hostname or "").lower()
    if not host:
        return "the mirror has no host"
    if is_private_host(host):
        # Belt and braces: the downloader checks the resolved address at dial
        # time, which is the check that actually holds. This one refuses the
        # obvious cases before a job is ever created, so they appear in the
        # console rather than in a container log.
        return f"host {host} names this machine or a private network"
    if registrable(host, MEDIA_DOMAINS_BY_PLATFORM.get(platform, frozenset())) is None:
        return f"host {host} is not an allowed {platform.value} media domain"
    return None


def allowed_mirrors(urls: list[str], platform: Platform) -> tuple[list[str], list[str]]:
    """Split mirrors into the ones that may be fetched and the reasons for the rest.

    Both halves are returned because dropping the refused ones silently is how
    an item ends up with no mirrors and no explanation. Order is preserved: the
    platform lists its own preference first and this is not the place to
    second-guess it.
    """
    keep: list[str] = []
    refused: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        reason = refusal(url, platform)
        if reason is None:
            keep.append(url)
        elif reason not in refused:
            refused.append(reason)
    return keep, refused


__all__ = [
    "ALLOWED_SCHEMES",
    "DOUYIN_MEDIA_DOMAINS",
    "MEDIA_DOMAINS",
    "MEDIA_DOMAINS_BY_PLATFORM",
    "TIKTOK_MEDIA_DOMAINS",
    "allowed_mirrors",
    "is_media_url",
    "refusal",
    "registrable",
]
