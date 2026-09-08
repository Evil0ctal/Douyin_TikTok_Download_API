"""The console's proxy Test button, as the worker runs it.

Two things are worth this much test weight. The payload is a contract with
web/src/pages/Proxies.tsx - a field the page reads and the job never sends is
blank on screen with nothing in any log to say why - and the proxy row holds a
password, while this result is stored in ``tasks.result`` and rendered in a
browser. The last test in the file is the one that matters: a real
:class:`ProxyProber`, a probe client that quotes the whole proxy URL back in
its error the way ``httpx`` does, and the credentials nowhere in the answer.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dtk.api.routes.operations import unwrap
from dtk.core.errors import DtkError, ErrorCode
from dtk.worker.ops import proxy_test
from dtk.worker.proxy_prober import ProberConfig, ProbeResult, ProxyProber

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web" / "src"

#: The proxy URL every fake in this file decrypts to. The password is what the
#: assertions hunt for.
PROXY_URL = "http://acct-42:sup3rsecret@eu-1.example:8080"
PASSWORD = "sup3rsecret"


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeTaskSession:
    """Enough of ``AsyncSession`` for the existence check the job makes."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.gets: list[tuple[Any, Any]] = []

    async def get(self, model: Any, pk: Any) -> Any:
        self.gets.append((model, pk))
        return self.row


class FakeProber:
    """Records the call and answers with whatever the test wants back."""

    def __init__(self, result: ProbeResult | None) -> None:
        self.result = result
        self.calls: list[tuple[uuid.UUID, bool]] = []

    async def probe_one(self, proxy_id: uuid.UUID, *, force: bool = False) -> ProbeResult | None:
        self.calls.append((proxy_id, force))
        return self.result


def a_deps(prober: Any) -> Any:
    """An ``OperationDeps`` with only the member this job reads populated."""
    return SimpleNamespace(
        config=lambda: None,
        cipher=object(),
        pool=object(),
        transport=object(),
        signers=object(),
        filler=None,
        prober=prober,
        notifier=None,
        rpc=None,
    )


def a_proxy_row(proxy_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=proxy_id,
        url_encrypted=b"blob",
        label="eu-1",
        country=None,
        timezone=None,
        healthy=True,
    )


async def run_job(prober: Any, params: Mapping[str, Any], *, row: Any = ...) -> dict[str, Any]:
    proxy_id = params.get("proxy_id")
    if row is ...:
        row = a_proxy_row(uuid.UUID(str(proxy_id).strip())) if _is_uuid(proxy_id) else None
    return await proxy_test.run(a_deps(prober), FakeTaskSession(row), params)  # type: ignore[arg-type]


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return False
    return True


# --------------------------------------------------------------------------
# the payload the console reads
# --------------------------------------------------------------------------


async def test_a_probe_answers_every_field_the_console_reads() -> None:
    proxy_id = uuid.uuid4()
    prober = FakeProber(
        ProbeResult(
            ok=True,
            exit_ip="203.0.113.7",
            country="DE",
            timezone="Europe/Berlin",
            latency_ms=142,
        )
    )

    result = await run_job(prober, {"proxy_id": str(proxy_id)})

    data, meta = unwrap(result)
    assert data == {
        "ok": True,
        "latency_ms": 142,
        "exit_ip": "203.0.113.7",
        "country": "DE",
        "timezone": "Europe/Berlin",
        "detail": None,
    }
    assert meta == {"proxy_id": str(proxy_id)}


async def test_a_failed_probe_is_a_finished_task_not_a_failed_one() -> None:
    """A dead egress is an answer. Only an unrunnable probe is an error."""
    prober = FakeProber(ProbeResult(ok=False, latency_ms=10_002, detail="ConnectTimeout: "))

    data, _meta = unwrap(await run_job(prober, {"proxy_id": str(uuid.uuid4())}))

    assert data["ok"] is False
    assert data["detail"] == "ConnectTimeout: "
    assert data["exit_ip"] is None


async def test_the_probe_is_forced_because_a_person_asked_for_it() -> None:
    proxy_id = uuid.uuid4()
    prober = FakeProber(ProbeResult(ok=True))

    await run_job(prober, {"proxy_id": f"  {proxy_id}  "})

    assert prober.calls == [(proxy_id, True)]


@pytest.mark.skipif(not WEB.exists(), reason="console not present")
def test_the_payload_matches_the_interface_the_console_declares() -> None:
    """Both pages declare their own copy; neither may read a field we omit."""
    sent = {"ok", "latency_ms", "exit_ip", "country", "timezone", "detail"}
    for page in ("Proxies.tsx", "Setup.tsx"):
        source = (WEB / "pages" / page).read_text(encoding="utf-8")
        block = re.search(r"interface ProxyProbeResult \{(.*?)\}", source, re.S)
        assert block is not None, f"{page} no longer declares ProxyProbeResult"
        read = set(re.findall(r"^\s*(\w+)\??:", block.group(1), re.M))
        assert read <= sent, f"{page} reads fields this job never sends: {sorted(read - sent)}"


# --------------------------------------------------------------------------
# what a stale console tab sends
# --------------------------------------------------------------------------


@pytest.mark.parametrize("params", [{}, {"proxy_id": ""}, {"proxy_id": "not-a-uuid"}])
async def test_an_unusable_proxy_id_is_a_parameter_error(params: dict[str, Any]) -> None:
    prober = FakeProber(ProbeResult(ok=True))

    with pytest.raises(DtkError) as raised:
        await run_job(prober, params, row=None)

    assert raised.value.code is ErrorCode.INVALID_PARAM
    assert prober.calls == []


async def test_a_proxy_that_no_longer_exists_is_not_found() -> None:
    prober = FakeProber(ProbeResult(ok=True))

    with pytest.raises(DtkError) as raised:
        await run_job(prober, {"proxy_id": str(uuid.uuid4())}, row=None)

    assert raised.value.code is ErrorCode.NOT_FOUND
    # Nothing is probed on the strength of an id that names no row.
    assert prober.calls == []


async def test_a_row_that_could_not_be_probed_fails_rather_than_reporting_a_verdict() -> None:
    """``probe_one`` returns None when it never reached the egress.

    An undecryptable URL or a scheme the client refuses measured nothing and
    left the row's health alone; answering ``ok: false`` would put a verdict on
    screen next to a proxy the page still lists as healthy.
    """
    prober = FakeProber(None)

    with pytest.raises(DtkError) as raised:
        await run_job(prober, {"proxy_id": str(uuid.uuid4())})

    assert raised.value.code is ErrorCode.INTERNAL


async def test_a_worker_built_without_a_prober_says_so() -> None:
    with pytest.raises(DtkError) as raised:
        await run_job(None, {"proxy_id": str(uuid.uuid4())})

    assert raised.value.code is ErrorCode.INTERNAL


# --------------------------------------------------------------------------
# the password
# --------------------------------------------------------------------------


class QuotingProbeClient:
    """Fails the way ``httpx`` does: with the proxy URL inside the message."""

    async def probe(self, proxy_url: str | None) -> ProbeResult:
        return ProbeResult(
            ok=False,
            latency_ms=17,
            detail=f"ProxyError: unable to connect to proxy {proxy_url!r}",
        )

    async def aclose(self) -> None:
        return None


class FakeCipher:
    def decrypt(self, blob: bytes, *, aad: str) -> str:
        return PROXY_URL


class FakePool:
    async def cool_all_on_proxy(self, session: Any, proxy_id: uuid.UUID, *, seconds: int) -> int:
        return 0


class WritableSession:
    """Absorbs the health write-back ``_apply`` performs."""

    async def get(self, model: Any, pk: Any) -> Any:
        return a_proxy_row(pk)

    async def execute(self, statement: Any) -> Any:
        return SimpleNamespace(rowcount=1)


def _session_factory(session: Any) -> Any:
    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[Any]:
        yield session

    return factory


async def test_a_probe_detail_can_never_carry_the_proxy_password() -> None:
    """The whole result, through the real prober, with a talkative client.

    ``ProbeResult.detail`` is the one field built from an exception message,
    and this job hands it to a browser through a stored task result.
    """
    proxy_id = uuid.uuid4()
    prober = ProxyProber(
        cipher=FakeCipher(),  # type: ignore[arg-type]
        pool=FakePool(),  # type: ignore[arg-type]
        client=QuotingProbeClient(),
        options=ProberConfig(),
        session_factory=_session_factory(WritableSession()),
    )

    result = await run_job(prober, {"proxy_id": str(proxy_id)})

    serialized = json.dumps(result)
    assert PASSWORD not in serialized
    assert "acct-42" not in serialized
    # The host and port survive, because telling two egresses apart is the
    # point of showing the detail at all.
    data, _meta = unwrap(result)
    assert "eu-1.example:8080" in str(data["detail"])
    assert data["ok"] is False
