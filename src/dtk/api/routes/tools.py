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

import hashlib
from collections import Counter
from collections.abc import Mapping
from typing import Any, Final

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
from dtk.identity.importing import parse_cookies
from dtk.identity.minting import BrowserRpcClient, BrowserRpcUnavailable
from dtk.signing import SigningSession, native_signers
from dtk.signing.base import MS_TOKEN_PARAM, SignedParams, StaticFingerprint
from dtk.signing.base import RequestSpec as SigningRequest
from dtk.signing.native import decoding, tiktok_sign, websign
from dtk.urls import first_url, identify, read_content_id

log = get_logger(__name__)

#: Ceiling on one batch. A column pasted out of a spreadsheet is the intended
#: input and runs to hundreds; past this the caller wants a script, not a form.
MAX_BATCH_LINES: Final = 1000
#: The text that carries them. Generous per line, because a share-sheet paste
#: is a caption and a link together.
MAX_BATCH_TEXT: Final = 512 * 1024

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
    cookies: str | None = Field(
        default=None,
        max_length=8192,
        description=(
            "The jar the request will be sent with, in any paste format. "
            "Douyin's own signature is computed over the visitor id inside it, "
            "so without it the signature headers cannot be produced."
        ),
    )


# --------------------------------------------------------------------------
# The signing pipeline, described
# --------------------------------------------------------------------------
#
# The seven fields /tools/sign has always returned say WHAT the signature is.
# They cannot say how it was reached, so the layers - a business query, a
# session token, a seal over both, and on Douyin a second seal over the visitor
# - arrive flattened into one query string, and a caller whose request is
# refused has no way to see which layer is missing. `stages` unflattens them.
#
# Everything below is reconstructed from what the signer returned. Nothing in
# dtk.signing reports it, and nothing there was changed to: that subtree is the
# load-bearing part of this project and its return types are worth more than
# this endpoint's ergonomics.

#: Stage names, in the order the layers run. Wire values - the console keys its
#: diagram off them - so they are as stable as the seven fields beside them.
STAGE_BUSINESS = "business"
STAGE_SESSION = "session"
STAGE_SIGNATURE = "signature"
STAGE_WEBSIGN = "websign"

#: Why a layer contributed nothing. Slugs rather than sentences, for the reason
#: error codes are: the console renders them in the reader's language, and a
#: caller may branch on them.
SKIP_NO_UIFID = "no_uifid_cookie"
SKIP_NO_MS_TOKEN = "no_ms_token_supplied"

#: Where the session token came from. Only ``generated`` is a value the caller
#: did not supply, and only Douyin ever produces one: TikTok verifies a token
#: when one is present but accepts its absence, so its signer leaves the
#: parameter empty rather than inventing a value that would fail verification
#: (see :func:`dtk.signing.native.tiktok_sign.sign`).
MS_TOKEN_FROM_URL = "url"
MS_TOKEN_FROM_COOKIES = "cookies"
MS_TOKEN_GENERATED = "generated"
MS_TOKEN_ABSENT = "absent"

#: What each signature parameter is for. ``algorithm`` reports ``X-Bogus`` on
#: TikTok because that is the stable name of the scheme on the wire, but the
#: X-Bogus TikTok Web sends is the constant ``1``: X-Gnarly is the seal over the
#: query and X-Dynosaur the environment report it covers. A diagram drawn from
#: ``algorithm`` alone would point at the one parameter that seals nothing.
ROLE_SEAL = "seal"
ROLE_ENVIRONMENT = "environment"
ROLE_CONSTANT = "constant"

#: Role of each parameter TikTok's SDK appends, spelled as it spells them.
TIKTOK_SIGNATURE_ROLES: Mapping[str, str] = {
    tiktok_sign.DYNOSAUR_PARAM: ROLE_ENVIRONMENT,
    tiktok_sign.BOGUS_PARAM: ROLE_CONSTANT,
    tiktok_sign.GNARLY_PARAM: ROLE_SEAL,
}


def _stage(
    name: str,
    *,
    ran: bool,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    skipped_reason: str | None = None,
) -> dict[str, Any]:
    """One layer of the pipeline, in the shape every layer reports.

    A layer that did not run is still a box: an empty ``websign`` is how a
    reader learns that the jar they sent carried no visitor id, and that this -
    rather than the algorithm - is why the sign-protected endpoints refuse them.
    """
    return {
        "name": name,
        "ran": ran,
        "skipped_reason": skipped_reason,
        "params": dict(params or {}),
        "headers": dict(headers or {}),
    }


def _business_stage(params: Mapping[str, str]) -> dict[str, Any]:
    """What the caller put in the URL, minus the one parameter that is not theirs.

    ``msToken`` is a session value wherever it was written, and TikTok's signer
    treats it as one: it takes it out of the business parameters and re-appends
    it between X-Dynosaur and X-Bogus. Listing it here as well would count it
    twice in a diagram whose stages are meant to add up to ``query``.
    """
    return _stage(
        STAGE_BUSINESS,
        ran=True,
        params={name: value for name, value in params.items() if name != MS_TOKEN_PARAM},
    )


def _session_stage(
    platform: Platform,
    url_params: Mapping[str, str],
    signed: SignedParams,
) -> dict[str, Any]:
    """The session token, and which side of the platform asymmetry it came from.

    Douyin's signer invents one when the URL carries none, and reports it among
    the parameters it added. TikTok's takes the caller's - URL first, then the
    jar - and reports whatever it found, empty included; it never invents,
    because a fabricated token is verified and fails, while a missing one is
    accepted. Reporting both as "msToken" would hide the only difference that
    matters here.
    """
    from_url = url_params.get(MS_TOKEN_PARAM, "")
    added = signed.params.get(MS_TOKEN_PARAM, "")
    if platform is Platform.DOUYIN:
        token = added or from_url
        source = MS_TOKEN_GENERATED if added else MS_TOKEN_FROM_URL
    else:
        token = added
        source = MS_TOKEN_FROM_URL if added == from_url else MS_TOKEN_FROM_COOKIES
    if not token:
        source = MS_TOKEN_ABSENT
    stage = _stage(
        STAGE_SESSION,
        ran=bool(token),
        params={MS_TOKEN_PARAM: token} if token else None,
        skipped_reason=None if token else SKIP_NO_MS_TOKEN,
    )
    stage["ms_token_source"] = source
    return stage


def _signature_stage(platform: Platform, signed: SignedParams) -> dict[str, Any]:
    """The seal layer, and which of its parameters is actually the seal.

    ``input_reconstructible`` is false and stays false. The seal is computed
    over the query as its own encoder produced it, and the string finally sent
    is built by a different one - ``urlencode`` against a URLSearchParams-
    compatible quote - so for some inputs the bytes signed are not the bytes
    sent. A preimage that is right for most queries would be read as
    documentation of the algorithm, which is worse than not offering one.
    """
    if platform is Platform.TIKTOK:
        params = {
            name: signed.params[name] for name in TIKTOK_SIGNATURE_ROLES if name in signed.params
        }
        roles = {name: TIKTOK_SIGNATURE_ROLES[name] for name in params}
        seal = tiktok_sign.GNARLY_PARAM
    else:
        seal = signed.algorithm.value
        params = {seal: signed.params[seal]} if seal in signed.params else {}
        roles = dict.fromkeys(params, ROLE_SEAL)
    stage = _stage(STAGE_SIGNATURE, ran=True, params=params)
    stage["seal_param"] = seal
    stage["roles"] = roles
    stage["input_reconstructible"] = False
    return stage


def _websign_stage(signed: SignedParams) -> dict[str, Any]:
    """Douyin's own signature over the visitor, or the reason there is none.

    Every value is recovered from what the signer returned: the parameters are
    in ``signed.params``, the timestamp is the expire header, and the preimage
    is rebuilt from ``signed.query``.
    """
    headers = dict(signed.headers or {})
    uifid = headers.get(websign.UIFID_PARAM, "")
    signature = signed.params.get(websign.SIGNATURE_PARAM, "")
    if not uifid or not signature:
        return _stage(STAGE_WEBSIGN, ran=False, skipped_reason=SKIP_NO_UIFID)
    stamp = headers.get(websign.EXPIRE_HEADER, "")
    params = {
        name: signed.params[name] for name in websign.VERIFY_FP_PARAMS if name in signed.params
    }
    params[websign.UIFID_PARAM] = uifid
    params[websign.TIMESTAMP_PARAM] = stamp
    params[websign.SIGNATURE_PARAM] = signature
    stage = _stage(STAGE_WEBSIGN, ran=True, params=params, headers=headers)
    stage["salt"] = websign.SALT
    stage["preimage"] = _websign_preimage(
        signed.query, uifid=uifid, stamp=stamp, signature=signature
    )
    return stage


def _websign_preimage(query: str, *, uifid: str, stamp: str, signature: str) -> str | None:
    """The exact string Douyin's md5 covers, or None when it cannot be proven.

    The signer does not return it, so it is rebuilt here - the signed query
    without its trailing signature parameter - and then checked by recomputing
    the md5. A preimage that does not reproduce the signature beside it would be
    read as documentation of the algorithm and derived from, so an unverifiable
    one is dropped rather than published.
    """
    covered, separator, _ = query.rpartition(f"&{websign.SIGNATURE_PARAM}=")
    if not separator:
        return None
    preimage = f"{uifid}_{stamp}_{websign.SALT}_{covered}"
    if hashlib.md5(preimage.encode()).hexdigest() != signature:
        return None
    return preimage


def _stages(
    platform: Platform,
    url_params: Mapping[str, str],
    signed: SignedParams,
) -> list[dict[str, Any]]:
    """Every layer that ran, and on Douyin the one that may not have.

    TikTok gets no ``websign`` box. The layer does not exist on that platform,
    and a stage permanently marked skipped would read as a step that could have
    run if the caller had sent something more.
    """
    stages = [
        _business_stage(url_params),
        _session_stage(platform, url_params, signed),
        _signature_stage(platform, signed),
    ]
    if platform is Platform.DOUYIN:
        stages.append(_websign_stage(signed))
    return stages


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
    - `cookies` - the jar the request will be sent with, in any paste format
      (`Cookie:` header, DevTools JSON, Netscape). Douyin computes
      `x-secsdk-web-signature` over the visitor id inside it, so without it
      `headers` comes back empty and the sign-protected endpoints refuse the
      request naming `uifid`. `/tools/identity` mints a jar that has it.

    **Returns**

    The signed query string, the parameters that were added, any headers the
    signature requires, and the User-Agent the signature was computed with.

    `stages` is that same result unflattened: one entry per layer of the
    pipeline, in the order the layers ran, each naming the parameters and
    headers it contributed and whether it ran at all.

    - `business` - what you put in the URL.
    - `session` - the `msToken`. `ms_token_source` says where it came from;
      `generated` means Douyin invented one, which is the only value here you
      did not supply. TikTok never invents: with no token it is skipped with
      `no_ms_token_supplied` and an empty `msToken` is sent, because a
      fabricated one is verified and fails while a missing one is accepted.
    - `signature` - the seal. `seal_param` names the parameter that is actually
      it, which on TikTok is `X-Gnarly`: `algorithm` reports `X-Bogus` because
      that is the stable name of the scheme, but the X-Bogus sent is the
      constant `1`. `roles` says the same per parameter.
      `input_reconstructible` is false - the bytes this layer signed are not
      always the bytes finally sent, so this endpoint will not guess at them.
    - `websign` - Douyin only. When the jar carried no visitor id it is present
      and skipped with `no_uifid_cookie`, which is the reason `headers` came
      back empty and the reason a sign-protected endpoint will refuse you. When
      it ran it also carries the `salt` and the `preimage` its md5 covers,
      verified against the returned signature before being returned.

    Send `query` byte for byte - re-encoding it changes the bytes the signature
    covers - and send every header in `headers` alongside it.
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

    # The jar is an input to the signature, not decoration. Douyin's
    # x-secsdk-web-signature is an md5 over the visitor id from these cookies,
    # so a caller who sends none gets a_bogus and nothing else - which is what
    # this endpoint used to do unconditionally, while its own documentation
    # told the reader a jar was required to send the result anywhere.
    cookies: dict[str, str] = {}
    if body.cookies:
        _fmt, cookies = parse_cookies(body.cookies)
    if body.ms_token:
        cookies["msToken"] = body.ms_token

    signer = native_signers()[platform]
    signed = await signer.sign(
        SigningRequest(method="GET", url=base, params=params),
        StaticFingerprint(user_agent=user_agent, browser_platform="Win32"),
        SigningSession(cookies=cookies),
    )
    # Never the cookie names, never their values: this is the one endpoint a
    # caller hands a live jar to, and a log line is the easiest place to lose it.
    log.info(
        "tools.sign",
        platform=platform.value,
        algorithm=signed.algorithm.value,
        with_cookies=bool(cookies),
    )
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
            "stages": _stages(platform, params, signed),
        },
    )


#: Ceiling on one value. An a_bogus runs to a few hundred characters and a whole
#: signed URL to a couple of thousand; this is generous for both and small
#: enough that no amount of it is worth arithmetic.
MAX_DECODE_VALUE: Final = 8192

#: What the caller gave us, as the response reports it back.
SOURCE_URL = "url"
SOURCE_PARAMETER = "parameter"


class DecodeRequest(BaseModel):
    """One thing to take apart."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(
        max_length=MAX_DECODE_VALUE,
        description=(
            "A signed URL, a query string, a `name=value` pair, or one "
            "parameter's value on its own."
        ),
    )
    parameter: str | None = Field(
        default=None,
        max_length=64,
        description="Which parameter the value is, when its shape is ambiguous.",
    )
    user_agent: str | None = Field(
        default=None,
        max_length=512,
        description="A candidate User-Agent, to check against the one that was signed.",
    )


def _decode_target(body: DecodeRequest) -> tuple[str, list[decoding.Decoded]]:
    """Work out what was pasted, then decode it. Order is cheapest-first.

    An explicit ``parameter`` wins over every guess: the caller knows, and a
    value whose shape is ambiguous is exactly when they would say so.
    """
    text = body.value.strip()
    if body.parameter:
        return SOURCE_PARAMETER, [
            decoding.decode_parameter(body.parameter, text, user_agent=body.user_agent)
        ]
    if "?" in text or "&" in text:
        found = decoding.decode_url(text, user_agent=body.user_agent)
        if found:
            return SOURCE_URL, found
    name, separator, raw = text.partition("=")
    if separator and name.lower() in decoding.PARAMETERS:
        return SOURCE_PARAMETER, [decoding.decode_parameter(name, raw, user_agent=body.user_agent)]
    guess = decoding.identify(text)
    if guess is None:
        raise InvalidParam(
            "value is not a signature parameter this build recognises",
            details={"field": "value", "known": sorted(set(decoding.PARAMETERS.values()))},
        )
    return SOURCE_PARAMETER, [decoding.decode_parameter(guess, text, user_agent=body.user_agent)]


@router.post(
    "/decode",
    summary="Read a signature parameter back",
    openapi_extra={I18N_KEY: "tools_decode"},
)
async def decode(
    request: Request,
    body: DecodeRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Take a signature parameter apart and say what is inside it.

    The inverse of `/tools/sign`, and the reason this project reversed these
    algorithms itself rather than vendoring somebody's port: an implementation
    you own can be explained. Paste a whole signed URL and every parameter in it
    is decoded against the exact string that parameter seals.

    Pure arithmetic. Nothing is fetched, no identity is spent, and the result
    depends only on what you send.

    **What "decode" means here** - three different things, and every field says
    which one it is, because running them together is how a tool like this
    starts lying.

    - **Recovered** (`plain`, `time`, `environment`). In the payload, and it
      comes back exactly: clocks, `aid`, `page_id`, the screen geometry the page
      reported, SDK and SCM versions, call counters, nonces.
    - **Bound but not recoverable** (`digest`). The payload carries a hash *of*
      something - three SM3 bytes of the query in `a_bogus`, a whole md5 of it
      in `X-Gnarly`. A hash does not run backwards and this endpoint will not
      pretend it does. Instead it *checks*: send `user_agent`, or a URL that
      carries the query, and `checks` says whether that candidate is the one the
      signature was computed over, and how many bits say so.
    - **Not computed at all**. `msToken` and the visitor tokens are issued or
      drawn, not derived. There is no plaintext under them; `reason` says
      `not_computed` rather than reporting a failure.

    **Parameters**

    - `value` - a signed URL, a query string, a `name=value` pair, or a bare
      parameter value. A URL is much the most useful form: only then is the
      covered string known exactly, so only then can the checks run.
    - `parameter` - name it yourself when the shape is ambiguous. Otherwise it
      is identified by properties no other parameter in the set has.
    - `user_agent` - a candidate to check against the signature. Both platforms
      hash the UA in, so this is how to find out whether the UA you are sending
      is the one you signed with.

    **Returns**

    One entry per parameter found, each with `fields` (what is inside),
    `checks` (whether your candidate inputs are the ones it sealed, with the
    exact `covered` string when they are) and `notes` (internal checksums this
    endpoint recomputed).

    A `checks` entry is `match`, `differs`, or `not_supplied` - and the third is
    not the second. Nothing is reported as wrong because you did not send it.

    Fields are labelled, not translated: `name` is a stable slug, so a client
    may key off it.
    """
    principal.require(Scope.DOUYIN_READ, Scope.TIKTOK_READ)
    source, decoded = _decode_target(body)
    platform = next((item.platform for item in decoded if item.platform), None)
    # Never the value, never a cookie: this endpoint is handed live signatures
    # and a log line is the easiest place to lose one.
    log.info(
        "tools.decode",
        source=source,
        platform=platform,
        parameters=[item.parameter for item in decoded],
    )
    return ok(
        request,
        {
            "source": source,
            "platform": platform,
            "user_agent": body.user_agent,
            "parameters": [item.as_dict() for item in decoded],
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
    # The same second pass /parse makes. Both apps put a caption, a numeric
    # code and the link on the clipboard together, and that whole string is
    # what a paste into the console sends - so an endpoint documented as
    # accepting "text with one inside it" has to actually look inside it. The
    # extracted candidate goes back through identify(), which keeps the
    # allowlist the only thing deciding what is recognized.
    kind = identify(url)
    if not kind.allowed:
        candidate = first_url(url)
        if candidate is not None:
            kind = identify(candidate)
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


class BatchParseRequest(BaseModel):
    """A blob of text with links and ids in it, one per line."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        max_length=MAX_BATCH_TEXT,
        description=(
            "Links, post ids, or whole share-sheet paste, one item per line. "
            "Blank lines and duplicates are dropped."
        ),
    )


@router.post(
    "/parse-batch",
    summary="Identify many links and ids at once",
    openapi_extra={I18N_KEY: "tools_parse_batch"},
)
async def parse_batch(
    request: Request,
    body: BatchParseRequest,
    principal: Principal = Depends(enforce_rate_limit),
) -> Any:
    """Work out what a list of links and ids point at, without fetching anything.

    `/tools/parse-url` answers one at a time, which is the wrong shape for the
    thing people actually have: a column pasted out of a spreadsheet, or the
    output of a scrape. Nothing here touches the network, so a thousand lines
    cost one request and no identity.

    Each line is tried as a link first and as a bare post id second. An id is
    checked against its own embedded timestamp - both platforms mint ids whose
    high 32 bits are the Unix second they were issued - so a typo is named as
    one here instead of becoming an upstream request that spends an identity to
    be told the same thing.

    That check cannot tell a post that never existed from one that has been
    deleted: a plausible id is a plausible id. It rejects text that cannot be an
    id at all, which is the part worth doing for free.

    **Parameters**

    - `text` - the lines, separated by newlines. Duplicates and blanks go.

    **Returns**

    One entry per distinct line in the order given, each with what it was
    recognised as, and a summary count by outcome.
    """
    principal.require(Scope.DOUYIN_READ, Scope.TIKTOK_READ)

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for raw in body.text.splitlines():
        line = raw.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        items.append(_identify_line(line))
        if len(items) >= MAX_BATCH_LINES:
            break

    counts = Counter(item["kind"] for item in items)
    return ok(
        request,
        {
            "items": items,
            "total": len(items),
            "counts": dict(counts),
            # Said out loud rather than left as a short list: a caller who
            # pasted 5000 lines and got 1000 back should not have to count.
            "truncated": len(seen) > len(items),
        },
    )


def _identify_line(line: str) -> dict[str, Any]:
    """One line, as a link if it is one and as a post id otherwise.

    Links win. A bare 19-digit number inside a URL is the post id either way,
    and going through `identify` keeps the host allowlist the only thing that
    decides what is recognised.
    """
    entry: dict[str, Any] = {
        "input": line,
        "kind": "unknown",
        "platform": None,
        "resource": None,
        "resource_id": None,
        "handle": None,
        "url": None,
        "needs_expansion": False,
        "minted_at": None,
    }

    kind = identify(line)
    if not kind.allowed:
        candidate = first_url(line)
        if candidate is not None:
            kind = identify(candidate)
    if kind.allowed:
        entry.update(
            kind="link",
            platform=kind.platform.value if kind.platform else None,
            resource=kind.resource.value,
            resource_id=kind.resource_id,
            handle=kind.handle,
            url=kind.url,
            needs_expansion=kind.needs_expansion,
        )
        if kind.needs_expansion:
            # A short link resolves only by following it, which is a network
            # call this endpoint promises not to make.
            entry["kind"] = "short_link"
        elif kind.resource_id:
            parsed = read_content_id(kind.resource_id)
            if parsed is not None:
                entry["minted_at"] = parsed.minted_at.isoformat()
        return entry

    parsed = read_content_id(line)
    if parsed is not None:
        entry.update(
            kind="content_id",
            resource_id=parsed.value,
            minted_at=parsed.minted_at.isoformat(),
        )
        return entry

    # Named specifically when it looks like somebody meant an id and mistyped,
    # because "unknown" for a line of digits is an unhelpful answer.
    if line.strip().isdigit():
        entry["kind"] = "bad_id"
    return entry


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
