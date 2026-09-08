"""The caller-supplied egress, which is request forgery if it is got wrong.

An operator-configured proxy may point at loopback; a caller-supplied one may
not, and the two are separate code paths for exactly that reason. These tests
are written from the attacker's side: every case is a way to name an address
inside the instance's own network and have it dialled.
"""

from __future__ import annotations

import pytest

from dtk.api.request_proxy import MAX_LENGTH, RequestProxyMode, normalize
from dtk.core.errors import InvalidParam

PUBLIC = RequestProxyMode.PUBLIC


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
