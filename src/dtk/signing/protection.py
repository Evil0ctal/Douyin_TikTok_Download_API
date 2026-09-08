"""Which endpoints the platform refuses without a browser-made signature.

Douyin does not sign-protect its whole API. Its own web SDK carries the list:
`runtime_bundler_34.js` from lf-security.bytegoofy.com registers a strategy
named ``webSign`` - the SDK labels it "API signing protection" - whose
``config.protectedHost`` names the exact host, method and paths that get a
signature attached::

    if ("webSign" === f.strategyKey && b === a.REWRITE) {
      var s = window.use("webSignUrl");
      c.args[0] = s(c.url).url          // rewrites the URL, adding the signature
    }

Everything outside that list is sent unsigned by the platform's own pages, and
so can be signed by :mod:`dtk.signing.native` alone - no browser, microseconds
instead of seconds, and it keeps working when browser-rpc is down.

Measured against live Douyin on 2026-09-08, one identity, eight requests each,
pure-Python signing only:

    /aweme/v1/web/user/profile/other/   unprotected   8/8 returned data
    /aweme/v1/web/comment/list/         unprotected   8/8 returned data
    /aweme/v1/web/aweme/detail/         PROTECTED     3/8, rest 403 Uifid Not Found
    /aweme/v1/web/aweme/post/           PROTECTED     3/8, rest 403 Uifid Not Found

The correlation is exact, which is what makes this table worth having rather
than guessing.

WHEN THIS GOES STALE it fails safe in one direction only: an endpoint Douyin
newly protects will be signed natively and refused with a 403 naming `uifid` or
the signature. That is loud, and `dtk.transport.classify` gives it its own rule
name - ``signature.refused`` - so the Logs page says the cause rather than
blaming the identity. The fix is then to add the path here. An endpoint that
stops being protected merely costs a browser call it did not need.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from dtk.core.types import Platform
from dtk.signing.native.tiktok_sign import pick_ms_token
from dtk.signing.native.websign import pick_uifid

#: Douyin paths whose GET requests the platform signs. Copied from the SDK's own
#: ``webSign.config.protectedHost["www.douyin.com"].GET`` on 2026-09-08. The POST
#: list is a subset of this one, so method is not tracked separately: treating a
#: POST-only path as protected for GET too can only cost an unnecessary browser
#: call, never a refusal.
DOUYIN_SIGNED_PATHS: frozenset[str] = frozenset(
    {
        "/aweme/v1/web/aweme/detail/",
        "/aweme/v1/web/aweme/post/",
        "/aweme/v1/web/aweme/favorite/",
        "/aweme/v1/web/aweme/listcollection/",
        "/aweme/v1/web/mix/aweme/",
        "/aweme/v1/web/tab/feed/",
        "/aweme/v1/web/mix/list/",
        "/aweme/v1/web/music/aweme/",
        "/aweme/v1/web/music/list/",
        "/aweme/v1/web/mix/detail/",
        "/aweme/v1/web/mix/listcollection/",
        "/aweme/v1/web/music/detail/",
        "/aweme/v1/web/collects/list/",
        "/aweme/v1/web/collects/video/list/",
    }
)


def requires_browser_signature(
    platform: Platform, url: str, cookies: Mapping[str, str] | None = None
) -> bool:
    """Whether this request needs a signature only a browser can produce.

    Both platforms are now False wherever the native signer has what it needs,
    and the shape of the answer is the same for each: the port can sign, but only
    a browser can mint the session value the signature has to carry.

    TikTok needs the identity's ``msToken``. `dtk.signing.native.tiktok_sign`
    produces X-Dynosaur and X-Gnarly, verified against the platform's own seal
    byte for byte and live on all four endpoints on 2026-09-08. A jar with no
    token is the case the port cannot cover, so it goes to the browser.

    Douyin needs the identity's visitor id, for the same reason:
    `dtk.signing.native.websign` computes the platform's own
    ``x-secsdk-web-signature``, and 24 of 24 live requests across all four
    endpoints returned data with no browser involved. The table below still
    decides for a jar with no visitor id in it.
    """
    if platform is Platform.TIKTOK:
        return not pick_ms_token(cookies)
    if platform is not Platform.DOUYIN:
        return True
    if _normalise(urlsplit(url).path) not in DOUYIN_SIGNED_PATHS:
        return False
    return pick_uifid(cookies) is None


def _normalise(path: str) -> str:
    """The platform matches on an exact path, trailing slash included."""
    if not path.startswith("/"):
        path = "/" + path
    return path if path.endswith("/") else path + "/"


__all__ = ["DOUYIN_SIGNED_PATHS", "requires_browser_signature"]
