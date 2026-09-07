"""Request and response shapes for the RPC contract.

The contract is fixed by the two clients in the application image -
`dtk.identity.minting.client.BrowserRpcClient` and `dtk.signing.rpc.RpcSigner` -
and by docs/design/04-transport-signing.md. Field names below are the wire names
those clients read; renaming one here silently breaks minting or signing, so the
mapping is spelled out rather than inferred from attribute names.

Requests are permissive about extra keys: an older service must not reject a
newer client that sends a field it does not know yet.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MintRequest(BaseModel):
    """POST /rpc/mint."""

    model_config = ConfigDict(extra="ignore")

    platform: str
    #: None means "no proxy". Supported for local testing; a production identity
    #: always has one, because the exit is part of what makes it an identity.
    proxy_url: str | None = None
    #: Anything the pool already knows about the exit: country, timezone,
    #: locale, languages. Fields present here win over the measured exit.
    geo_hint: dict[str, Any] = Field(default_factory=dict)


class MintResponse(BaseModel):
    """The identity a session produced.

    `BrowserRpcClient.mint` reads `cookies`, `browser_family`, `browser_major`,
    `user_agent`, `platform_hint`, `screen`, `language`, `timezone` and
    `exit_ip`. `platform_hint` is `navigator.platform`; the top-level `platform`
    is the platform that was minted for, and the two must not be confused.
    """

    cookies: dict[str, str]
    platform: str
    browser_family: str = "chrome"
    browser_major: int | None = None
    user_agent: str | None = None
    platform_hint: str | None = None
    screen: str | None = None
    language: str | None = None
    timezone: str | None = None
    exit_ip: str | None = None
    country: str | None = None
    locale: str | None = None


class SignRequest(BaseModel):
    """POST /rpc/sign.

    `query` is the exact byte sequence to sign. `RpcSigner` sends it; the
    minting client sends only `params`, in which case the service builds the
    query itself. When both are present, `query` wins - the caller that built it
    knows the ordering and escaping the signature has to cover.
    """

    model_config = ConfigDict(extra="ignore")

    platform: str
    url: str
    method: str = "GET"
    query: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    user_agent: str | None = None


class SignResponse(BaseModel):
    """POST /rpc/sign.

    ``user_agent`` is not decoration. Verified live on 2026-09-07: TikTok binds
    the signature to the byte-exact User-Agent - changing Chrome 151 to 150 is
    enough for the API to return an 18-byte empty body, and changing it back
    restores the data. So the caller must send the request under exactly the UA
    the signature was computed with, and returning it here is what lets the
    caller check rather than assume.
    """

    model_config = ConfigDict(extra="ignore")

    params: dict[str, str]
    user_agent: str


class HealthResponse(BaseModel):
    """GET /rpc/health.

    `RpcSigner.health` reads `warm_contexts`, `backend_version` and `uptime` and
    treats zero warm contexts as unhealthy. `BrowserRpcClient.health` reads
    `warm_contexts`, `chromium_major` and `backend_version`; the console shows
    `chromium_major` beside the wreq profile version so the version drift from
    doc 04 stays visible.
    """

    status: str
    backend: str
    backend_version: str
    chromium_major: int | None = None
    warm_contexts: int = 0
    uptime: float = 0.0
    backend_pin: str | None = None
    error: str | None = None


__all__ = ["HealthResponse", "MintRequest", "MintResponse", "SignRequest"]
