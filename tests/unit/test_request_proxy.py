"""The caller-supplied egress, which is request forgery if it is got wrong.

An operator-configured proxy may point at loopback; a caller-supplied one may
not, and the two are separate code paths for exactly that reason. These tests
are written from the attacker's side: every case is a way to name an address
inside the instance's own network and have it dialled.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from dtk.api import request_proxy
from dtk.api.request_proxy import MAX_LENGTH, RequestProxyMode, Resolver, normalize, vet
from dtk.core.errors import InvalidParam

PUBLIC = RequestProxyMode.PUBLIC

#: A globally routable address to stand in for "a real proxy's A record".
#: Not one of the documentation ranges: those are not global, and the check
#: under test would refuse them.
PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


def _reason(excinfo: pytest.ExceptionInfo[Exception]) -> str:
    """The stable machine-readable half of the refusal."""
    return str(excinfo.value.details.get("reason"))  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# The default is off
# --------------------------------------------------------------------------


def test_the_parameter_is_refused_by_default() -> None:
    with pytest.raises(InvalidParam) as excinfo:
        normalize("http://proxy.example:8080", mode=RequestProxyMode.DENY)
    assert _reason(excinfo) == "request_proxy_disabled"


def test_a_disabled_feature_refuses_rather_than_ignores() -> None:
    """Silently dropping it is worse than an error.

    The request would leave from the instance's own address while the caller
    believed it went through theirs, and they would only learn otherwise from
    whoever received it.
    """
    with pytest.raises(InvalidParam):
        normalize("http://proxy.example:8080", mode=RequestProxyMode.DENY)


@pytest.mark.parametrize("mode", list(RequestProxyMode))
def test_no_proxy_is_always_fine(mode: RequestProxyMode) -> None:
    assert normalize(None, mode=mode) is None
    assert normalize("   ", mode=mode) is None


# --------------------------------------------------------------------------
# Reaching the instance's own network
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://[::1]:8080",
        "http://10.0.0.5:3128",
        "http://192.168.1.1:3128",
        "http://172.16.0.1:3128",
        # Cloud instance metadata, the highest-value target of the lot.
        "http://169.254.169.254:80",
        # A bare label is an intranet name however innocent it looks.
        "http://intranet:3128",
        # Spellings that exist only to get past a string comparison.
        "http://2130706433:8080",
        "http://0x7f000001:8080",
        "http://127.0.0.1.:8080",
        "http://[::ffff:127.0.0.1]:8080",
    ],
)
def test_public_mode_refuses_everything_inside_the_perimeter(target: str) -> None:
    with pytest.raises(InvalidParam) as excinfo:
        normalize(target, mode=PUBLIC)
    assert _reason(excinfo) == "host_not_public"


def test_any_mode_is_the_operators_explicit_choice() -> None:
    """The escape hatch exists, and it is spelled out rather than implied."""
    assert normalize("http://127.0.0.1:8080", mode=RequestProxyMode.ANY) == "http://127.0.0.1:8080"


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_a_public_proxy_is_returned_unchanged() -> None:
    assert normalize("http://proxy.example.com:8080", mode=PUBLIC) == (
        "http://proxy.example.com:8080"
    )


def test_credentials_survive_because_providers_require_them() -> None:
    url = "http://user:secret@proxy.example.com:8080"
    assert normalize(url, mode=PUBLIC) == url


@pytest.mark.parametrize("scheme", ["http", "https", "socks5", "socks5h"])
def test_every_dialable_scheme_is_accepted(scheme: str) -> None:
    assert normalize(f"{scheme}://proxy.example.com:8080", mode=PUBLIC)


@pytest.mark.parametrize(
    "target",
    ["file:///etc/passwd", "gopher://proxy.example.com:70", "ftp://proxy.example.com:21"],
)
def test_other_schemes_are_refused(target: str) -> None:
    with pytest.raises(InvalidParam) as excinfo:
        normalize(target, mode=PUBLIC)
    assert _reason(excinfo) == "scheme_not_supported"


def test_a_bare_host_and_port_is_refused_rather_than_guessed() -> None:
    """Assuming a scheme here would mean assuming one for `localhost:8080` too."""
    with pytest.raises(InvalidParam) as excinfo:
        normalize("proxy.example.com:8080", mode=PUBLIC)
    assert _reason(excinfo) == "scheme_missing"


def test_an_absurd_length_is_refused_before_parsing() -> None:
    with pytest.raises(InvalidParam) as excinfo:
        normalize("http://" + "a" * MAX_LENGTH + ".example.com:8080", mode=PUBLIC)
    assert _reason(excinfo) == "too_long"


@pytest.mark.parametrize("target", ["http://proxy.example.com:0", "http://proxy.example.com:99999"])
def test_a_port_outside_the_range_is_refused(target: str) -> None:
    with pytest.raises(InvalidParam) as excinfo:
        normalize(target, mode=PUBLIC)
    assert _reason(excinfo) == "port_invalid"


def test_the_rejected_value_is_never_echoed_back() -> None:
    """A rejection is when someone is most likely to have pasted a real one."""
    secret = "http://user:hunter2@127.0.0.1:8080"
    with pytest.raises(InvalidParam) as excinfo:
        normalize(secret, mode=PUBLIC)
    rendered = str(excinfo.value) + repr(excinfo.value.details)
    assert "hunter2" not in rendered
    assert secret not in rendered


# --------------------------------------------------------------------------
# Names that point inside the perimeter (GHSA-q3h8-73xx-gwqx)
#
# A name is the caller's to point anywhere, so judging the text of one proves
# nothing about where it leads. `127-0-0-1.sslip.io` is the public spelling of
# that, and an attacker's own domain with an A record of 127.0.0.1 is the one
# no string check can ever catch.
# --------------------------------------------------------------------------


def _answers(*addresses: str) -> Resolver:
    async def resolve(host: str) -> Sequence[str]:
        return addresses

    return resolve


async def _must_not_resolve(host: str) -> Sequence[str]:
    raise AssertionError(f"{host} should have been decided without a lookup")


@pytest.mark.parametrize(
    "target",
    [
        "http://127-0-0-1.sslip.io:8099",
        "http://169-254-169-254.sslip.io:80",
        "socks5://app-10-0-0-1.nip.io:1080",
    ],
)
async def test_a_name_spelling_a_private_address_is_refused_without_a_lookup(target: str) -> None:
    with pytest.raises(InvalidParam) as excinfo:
        await vet(target, mode=PUBLIC, resolve=_must_not_resolve)
    assert _reason(excinfo) == "host_not_public"


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param(("127.0.0.1",), id="loopback"),
        pytest.param(("169.254.169.254",), id="cloud-metadata"),
        pytest.param(("10.0.0.5",), id="private-range"),
        pytest.param(("::1",), id="v6-loopback"),
        pytest.param(("::ffff:127.0.0.1",), id="v4-mapped"),
        # One bad answer among good ones is enough: which of them gets dialled
        # is up to the resolver's ordering, not ours.
        pytest.param((PUBLIC_V4, "127.0.0.1"), id="mixed"),
    ],
)
async def test_a_name_that_resolves_inside_is_refused(answers: tuple[str, ...]) -> None:
    secret = "http://user:hunter2@innocent.example.com:8080"
    with pytest.raises(InvalidParam) as excinfo:
        await vet(secret, mode=PUBLIC, resolve=_answers(*answers))
    assert _reason(excinfo) == "host_not_public"
    rendered = str(excinfo.value) + repr(excinfo.value.details)
    assert "hunter2" not in rendered


@pytest.mark.parametrize(
    ("target", "answers", "expected"),
    [
        pytest.param(
            "http://user:secret@proxy.example.com:8080",
            (PUBLIC_V4,),
            f"http://user:secret@{PUBLIC_V4}:8080",
            id="credentials-and-port-kept",
        ),
        pytest.param(
            "http://proxy.example.com",
            (PUBLIC_V4,),
            f"http://{PUBLIC_V4}",
            id="no-port-stays-no-port",
        ),
        pytest.param(
            "socks5h://proxy.example.com:1080",
            (PUBLIC_V6,),
            f"socks5h://[{PUBLIC_V6}]:1080",
            id="v6-bracketed",
        ),
        pytest.param(
            "socks5://proxy.example.com:1080",
            (PUBLIC_V6, PUBLIC_V4),
            f"socks5://{PUBLIC_V4}:1080",
            id="v4-preferred",
        ),
    ],
)
async def test_a_public_name_is_pinned_to_the_address_that_was_checked(
    target: str, answers: tuple[str, ...], expected: str
) -> None:
    """Checking a name and then letting the client resolve it again is a race.

    The dial goes to the address that passed, so a second answer - rebinding,
    or a round-robin record with a private member - never gets asked for.
    """
    assert await vet(target, mode=PUBLIC, resolve=_answers(*answers)) == expected


async def test_an_https_proxy_keeps_its_name_but_is_still_checked() -> None:
    """Pinning would break the TLS handshake with the proxy, which is the defence.

    Certificate verification is against the name: a rebind to an internal
    address cannot produce a certificate for the caller's hostname.
    """
    target = "https://proxy.example.com:8443"
    assert await vet(target, mode=PUBLIC, resolve=_answers(PUBLIC_V4)) == target
    with pytest.raises(InvalidParam) as excinfo:
        await vet(target, mode=PUBLIC, resolve=_answers("127.0.0.1"))
    assert _reason(excinfo) == "host_not_public"


async def test_a_name_that_does_not_resolve_is_refused() -> None:
    async def fails(host: str) -> Sequence[str]:
        raise OSError("Name or service not known")

    with pytest.raises(InvalidParam) as excinfo:
        await vet("http://nowhere.example.com:8080", mode=PUBLIC, resolve=fails)
    assert _reason(excinfo) == "host_unresolvable"

    with pytest.raises(InvalidParam) as excinfo:
        await vet("http://nowhere.example.com:8080", mode=PUBLIC, resolve=_answers())
    assert _reason(excinfo) == "host_unresolvable"


async def test_a_lookup_that_hangs_is_given_up_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is holding an API request open, and no answer is not a yes."""
    monkeypatch.setattr(request_proxy, "RESOLVE_TIMEOUT_SECONDS", 0.01)

    async def hangs(host: str) -> Sequence[str]:
        await asyncio.sleep(10)
        return (PUBLIC_V4,)

    with pytest.raises(InvalidParam) as excinfo:
        await vet("http://slow.example.com:8080", mode=PUBLIC, resolve=hangs)
    assert _reason(excinfo) == "host_unresolvable"


async def test_a_public_literal_needs_no_lookup() -> None:
    target = f"http://{PUBLIC_V4}:8080"
    assert await vet(target, mode=PUBLIC, resolve=_must_not_resolve) == target


async def test_any_mode_neither_resolves_nor_rewrites() -> None:
    target = "http://127-0-0-1.sslip.io:8099"
    assert await vet(target, mode=RequestProxyMode.ANY, resolve=_must_not_resolve) == target


@pytest.mark.parametrize("mode", list(RequestProxyMode))
async def test_vet_passes_no_proxy_through(mode: RequestProxyMode) -> None:
    assert await vet(None, mode=mode, resolve=_must_not_resolve) is None


async def test_vet_refuses_by_default_before_any_lookup() -> None:
    with pytest.raises(InvalidParam) as excinfo:
        await vet(
            "http://proxy.example.com:8080", mode=RequestProxyMode.DENY, resolve=_must_not_resolve
        )
    assert _reason(excinfo) == "request_proxy_disabled"


@pytest.mark.parametrize(
    "target",
    [
        "http://proxy.example.com:8080\\@other.example.com",
        "http://user\\name@proxy.example.com:8080",
        "http://proxy.example.com :8080",
        "http://proxy.exa^mple.com:8080",
    ],
)
def test_an_authority_parsers_could_disagree_about_is_refused(target: str) -> None:
    """The host checked here must be the host the HTTP client dials.

    Python's parser and the WHATWG one wreq and the browser use do not agree
    on every character, so anything outside RFC 3986's authority set is refused
    before either of them is asked what the host is.
    """
    with pytest.raises(InvalidParam) as excinfo:
        normalize(target, mode=PUBLIC)
    assert _reason(excinfo) == "authority_invalid"


def test_percent_encoded_credentials_are_still_fine() -> None:
    url = "http://user:p%40ss@proxy.example.com:8080"
    assert normalize(url, mode=PUBLIC) == url
