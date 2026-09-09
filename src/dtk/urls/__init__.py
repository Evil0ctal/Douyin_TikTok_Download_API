"""URL recognition, normalization and short-link expansion.

The single place that answers "is this URL ours, and what does it point at".
Because it is also the SSRF chokepoint for every caller-supplied URL
(docs/design/08-security.md), nothing outside this package should parse a
platform URL by hand.

Typical use::

    kind = await resolve(pasted_share_text, fetcher)
    if kind.resource is ResourceKind.VIDEO:
        await fetch_video(kind.platform, kind.resource_id)
"""

from dtk.urls.expand import MAX_REDIRECTS, RedirectFetcher, expand, resolve
from dtk.urls.ids import ContentId, read_content_id, require_content_id
from dtk.urls.parse import (
    UrlKind,
    extract_urls,
    first_url,
    identify,
    is_allowed_host,
    is_private_host,
    normalize,
    require_supported,
    sanitize_extra_hosts,
)
from dtk.urls.patterns import ResourceKind

__all__ = [
    "MAX_REDIRECTS",
    "ContentId",
    "RedirectFetcher",
    "ResourceKind",
    "UrlKind",
    "expand",
    "extract_urls",
    "first_url",
    "identify",
    "is_allowed_host",
    "is_private_host",
    "normalize",
    "read_content_id",
    "require_content_id",
    "require_supported",
    "resolve",
    "sanitize_extra_hosts",
]
