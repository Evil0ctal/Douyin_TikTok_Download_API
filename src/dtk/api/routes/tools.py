"""Building blocks, exposed because this is an open-source project.

Everything here is something the service does for itself on every request:
recognise a link, compute a signature, mint a guest identity. V4 published the
equivalents as two dozen separate routes and people built on them, so they are
offered again - as three endpoints rather than twenty-four, and behind the same
credential as everything else.

The three differ enormously in what they cost, and the guards follow the cost
rather than the shape:

* ``/tools/parse-url`` is a pure function over a string. It touches no network
  and spends nothing.
* ``/tools/sign`` is arithmetic. It spends nothing either, but it is the part of
  this project that took the longest to get right, so it is the one most worth
  publishing.
* ``/tools/identity`` launches a real browser against a real platform. It costs
  tens of seconds and a slot in a warm-context pool, and what it returns is a
  usable credential. It requires ``identity:manage``, which ordinary read keys
  do not carry.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dtk.api.deps import Principal, enforce_rate_limit
from dtk.api.request_proxy import RequestProxyMode
from dtk.api.request_proxy import normalize as normalize_proxy
from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import ok
from dtk.core.errors import InvalidParam, NotConfigured, UpstreamRiskControl
from dtk.core.logging import get_logger
from dtk.core.types import Platform, Scope
from dtk.identity.minting import BrowserRpcClient, BrowserRpcUnavailable
from dtk.signing import SigningSession, native_signers
from dtk.signing.base import RequestSpec as SigningRequest
from dtk.signing.base import StaticFingerprint
from dtk.urls import identify

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])

#: What the signers assume when a caller gives no User-Agent. Both platforms
#: hash the UA into the signature, so this has to be a real string rather than
#: an empty one - and the caller has to send the SAME string with the request,
#: which is why it is echoed back in the response.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class SignRequest(BaseModel):
    """One request to sign."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    url: str = Field(max_length=4096, description="The API URL to sign, query included.")
    user_agent: str | None = Field(
        default=None,
        max_length=512,
        description="The User-Agent the request will be sent with.",
    )
    ms_token: str | None = Field(
        default=None,
        max_length=512,
        description="TikTok session token to seal into the signature.",
    )


@router.post(
    "/sign",
    summary="Sign a platform API URL",
    openapi_extra={I18N_KEY: "tools_sign"},
)
async def sign(
    request: Request,
    body: SignRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Compute the signature parameters a platform API URL needs.

    Pure arithmetic: nothing is fetched, no identity is spent, and the result
    depends only on what you send. Give it the full URL including its query and
    it returns that query with the signature appended, ready to send.

    Three things have to line up between signing and sending, or the platform
    refuses the request. Measured 2026-09-08 by sending this endpoint's own
    output: with all three, 2537 bytes and the expected profile; with no cookies
    at all, 200 and an empty body.

    - **The User-Agent.** Both platforms hash it into the signature. The one
      used is echoed back; send that exact string.
    - **The TLS fingerprint.** TikTok checks that it agrees with the User-Agent,
      so a Chrome UA has to travel over a Chrome TLS profile. Plain `curl` is
      refused whatever the signature says.
    - **A cookie jar.** A correct signature with no cookies still gets nothing:
      the platform answers a *session*, and `msToken` and `UIFID_TEMP` are
      issued to a browser rather than computed. `/tools/identity` is how to get
      one.

    **Parameters**

    - `platform` - `douyin` or `tiktok`.
    - `url` - the API URL to sign, query included.
    - `user_agent` - what the request will be sent with; a Chrome default is
      used if you omit it.
    - `ms_token` - TikTok only. Sealed into the signature, so it must be the
      token the request will carry. An invented value is worse than none.

    **Returns**

    The signed query string, the parameters that were added, any headers the
    signature requires, and the User-Agent the signature was computed with.
    """
    principal.require(Scope.DOUYIN_READ, Scope.TIKTOK_READ)
    platform = body.platform
    user_agent = (body.user_agent or DEFAULT_USER_AGENT).strip()
    if not user_agent:
        raise InvalidParam("user_agent must not be blank", details={"field": "user_agent"})

    base, _, query = body.url.partition("?")
    if not base.lower().startswith(("http://", "https://")):
        raise InvalidParam("url must be an http or https URL", details={"field": "url"})

    params: dict[str, str] = {}
    for pair in query.split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        params[name] = value

    signer = native_signers()[platform]
    signed = await signer.sign(
        SigningRequest(method="GET", url=base, params=params),
        StaticFingerprint(user_agent=user_agent, browser_platform="Win32"),
        SigningSession(cookies={"msToken": body.ms_token} if body.ms_token else {}),
    )
    log.info("tools.sign", platform=platform.value, algorithm=signed.algorithm.value)
    return ok(
        request,
        {
            "platform": platform.value,
            "signed_url": signed.signed_url(base),
            "query": signed.query,
            "params": dict(signed.params),
            "headers": dict(signed.headers or {}),
            "user_agent": user_agent,
            "algorithm": signed.algorithm.value,
        },
    )


@router.get(
    "/parse-url",
    summary="Identify a share link",
    openapi_extra={I18N_KEY: "tools_parse_url"},
)
async def parse_url(
    request: Request,
    url: str = Query(
        max_length=4096,
        description="A share link, or text with one inside it.",
    ),
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Work out what a link points at, without fetching anything.

    This is the id extraction v4 published as a dozen `get_*_id` routes, as one
    endpoint: it reports the platform, what kind of resource the link names, and
    the id that names it.

    Which id that is differs by platform, because the endpoints differ. A Douyin
    profile link yields a `sec_user_id`; a TikTok one yields the `@handle`,
    because that is what TikTok's user-detail call accepts. Both are returned
    under `resource_id`, with `handle` alongside when there is one.

    A short link (`v.douyin.com`, `vm.tiktok.com`) cannot be resolved without
    following it, which is a network call. Those come back with
    `needs_expansion: true` and no id; send them to `/parse` instead.

    **Parameters**

    - `url` - a share link, or the whole clipboard text with a link in it.

    **Returns**

    The platform, resource kind, id, handle, canonical URL, whether it needs
    expanding, and whether it is a supported target at all.
    """
    principal.require(Scope.DOUYIN_READ, Scope.TIKTOK_READ)
    kind = identify(url)
    return ok(
        request,
        {
            "allowed": kind.allowed,
            "platform": kind.platform.value if kind.platform else None,
            "resource": kind.resource.value,
            "resource_id": kind.resource_id,
            "handle": kind.handle,
            "content_kind": kind.content_kind.value if kind.content_kind else None,
            "url": kind.url,
            "needs_expansion": kind.needs_expansion,
        },
    )


class IdentityRequest(BaseModel):
    """One guest identity to mint and hand back."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    proxy: str | None = Field(
        default=None,
        max_length=512,
        description="Mint behind this egress, so the jar matches the address you will use it from.",
    )


@router.post(
    "/identity",
    summary="Mint a guest identity and return it",
    openapi_extra={I18N_KEY: "tools_identity"},
)
async def mint_identity(
    request: Request,
    body: IdentityRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Drive a real browser to the platform and hand back the cookies it was given.

    This is the half of the work that cannot be computed. `msToken` on TikTok
    and `UIFID_TEMP` on Douyin are issued by the platform to a browser that
    loaded its page; no algorithm produces them, which is why a signature alone
    is not enough to make a request that works.

    Expect it to take tens of seconds: it launches a browser, loads the page and
    waits for the scripts to finish setting cookies. It needs the
    `identity:manage` scope, which ordinary read keys do not carry.

    Nothing is stored. The jar is returned to you and forgotten - use
    `/api/v1/admin/identities/mint` instead if you want it added to this
    instance's own pool.

    **Parameters**

    - `platform` - `douyin` or `tiktok`.
    - `proxy` - mint behind this egress. Subject to the same
      `security.request_proxy` setting as every other proxy parameter, and worth
      setting: cookies minted at one address and used from another are the
      mismatch both platforms look for.

    **Returns**

    The cookie jar, the browser fingerprint it was minted with - User-Agent,
    platform, screen, language, timezone - and the exit address it was seen
    from. Send all of it together; a jar used with a different User-Agent is a
    weaker identity than no identity.
    """
    principal.require(Scope.IDENTITY_MANAGE)

    settings = request.app.state.settings
    base_url = getattr(settings, "browser_rpc_url", "")
    if not base_url:
        raise NotConfigured(
            "this instance has no browser-rpc service configured, so it cannot mint",
            details={"setting": "DTK_BROWSER_RPC_URL"},
        )

    # The caller's egress goes through exactly the same gate as ?proxy= on the
    # data endpoints: minting is an outbound connection like any other, and a
    # tool endpoint must not be the way around that setting.
    raw_mode = str(request.app.state.config.get("security.request_proxy") or "")
    try:
        mode = RequestProxyMode(raw_mode)
    except ValueError:
        mode = RequestProxyMode.DENY
    egress = normalize_proxy(body.proxy, mode=mode)

    client = BrowserRpcClient(base_url)
    try:
        minted = await client.mint(body.platform, proxy_url=egress)
    except BrowserRpcUnavailable as exc:
        raise UpstreamRiskControl(
            "the browser service could not mint an identity right now",
            details={"platform": body.platform.value},
        ) from exc
    finally:
        await client.aclose()

    fingerprint = minted.fingerprint
    log.info(
        "tools.identity_minted",
        platform=body.platform.value,
        cookies=len(minted.cookies),
        proxied=egress is not None,
    )
    return ok(
        request,
        {
            "platform": body.platform.value,
            "cookies": minted.cookies,
            "fingerprint": {
                "user_agent": fingerprint.user_agent,
                "browser_family": (
                    fingerprint.browser_family.value if fingerprint.browser_family else None
                ),
                "browser_major": fingerprint.browser_major,
                "platform": fingerprint.platform,
                "screen": fingerprint.screen,
                "language": fingerprint.language,
                "timezone": fingerprint.timezone,
            },
            "exit_ip": minted.exit_ip,
        },
    )


__all__ = ["router"]
