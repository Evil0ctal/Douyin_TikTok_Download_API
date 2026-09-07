"""The self-check and, above all, its redaction.

A diagnostic report exists to be pasted into a public issue. Every test that
touches rendering therefore asserts on what is *absent* from the output: a
proxy password, a cookie, an API key or a signature parameter that survives
into the report is a credential leak with a friendly UI in front of it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from dtk.core.crypto import Cipher
from dtk.ops import diagnose
from dtk.ops.diagnose import DiagnoseContext, StepStatus

SECRET = "c" * 48
PROXY_ID = "22222222-2222-2222-2222-222222222222"

LEAKY_DETAILS = {
    "proxy": "http://alice:hunter2@proxy.example.com:8080",
    "headers": "Cookie: sessionid=abc123def456; ttwid=1%7Cxyz",  # SYNTHETIC fixture
    "api_key": "dtk_a1b2c3d4_TQm9xVeryLongSecretValue",
    "url": "https://www.douyin.com/aweme/v1/web/aweme/detail/?msToken=AAAABBBBCCCCDDDD",
    "nested": {"authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"},
}

SECRETS = (
    "hunter2",
    "abc123def456",
    "TQm9xVeryLongSecretValue",
    "AAAABBBBCCCCDDDD",
    "eyJhbGciOiJIUzI1NiJ9.payload.signature",
)


def mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _AsyncCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.returns_rows = True

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, answers: dict[str, list[Any]]) -> None:
        self._answers = answers

    def begin_nested(self) -> _AsyncCtx:
        return _AsyncCtx()

    async def execute(self, statement: Any, _params: Any = None) -> FakeResult:
        rendered = str(statement)
        for fragment, rows in self._answers.items():
            if fragment in rendered:
                return FakeResult(rows)
        raise RuntimeError("no answer configured")


class ProxyRow:
    def __init__(self, url_encrypted: bytes) -> None:
        self.id = PROXY_ID
        self.label = "residential-1"
        self.country = "JP"
        self.healthy = True
        self.url_encrypted = url_encrypted


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,gone",
    [
        ("proxy http://alice:hunter2@host.example.com:8080 down", "hunter2"),
        ("Cookie: sessionid=abc123def456", "abc123def456"),
        ("x-api-key: dtk_a1b2c3d4_TQm9xVerySecret", "TQm9xVerySecret"),
        ("?msToken=AAAABBBBCCCCDDDD&count=20", "AAAABBBBCCCCDDDD"),
        ("sid_guard=deadbeefdeadbeef", "deadbeefdeadbeef"),  # SYNTHETIC fixture
    ],
)
def test_every_credential_shape_is_masked(raw: str, gone: str) -> None:
    redacted = diagnose.redact(raw)
    assert gone not in redacted
    assert diagnose.MASK in redacted


def test_redaction_keeps_the_identifying_half() -> None:
    """A masked report still has to be readable enough to debug from."""
    redacted = diagnose.redact("http://alice:hunter2@proxy.example.com:8080")

    assert "alice" in redacted
    assert "proxy.example.com" in redacted
    assert "hunter2" not in redacted

    key = diagnose.redact("dtk_a1b2c3d4_TQm9xVeryLongSecretValue")
    assert key.startswith("dtk_a1b2c3d4_")
    assert "TQm9xVeryLongSecretValue" not in key


def test_report_text_is_redacted() -> None:
    step = diagnose.StepResult(
        number=3,
        step="proxies",
        status=StepStatus.FAIL,
        reason="proxy http://alice:hunter2@proxy.example.com:8080 refused the probe",
        action="rotate the credentials for dtk_a1b2c3d4_TQm9xVeryLongSecretValue",
        details=LEAKY_DETAILS,
    )
    report = diagnose.DiagnosticReport(
        version="5.0.0",
        started_at=diagnose.datetime.now(diagnose.UTC),
        finished_at=diagnose.datetime.now(diagnose.UTC),
        steps=(step,),
    )

    rendered = report.render_text()
    serialized = str(report.as_dict())

    for secret in SECRETS:
        assert secret not in rendered
        assert secret not in serialized
    assert "proxies" in rendered
    assert "FAIL" in rendered


# --------------------------------------------------------------------------
# individual steps
# --------------------------------------------------------------------------


async def test_components_step_fails_without_a_database() -> None:
    result = await diagnose.check_components(DiagnoseContext())

    assert result.status is StepStatus.FAIL
    assert result.action is not None
    assert "postgres" in result.details


async def test_egress_step_grades_partial_reachability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "tiktok" in str(request.url):
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200)

    async with mock_client(handler) as client:
        result = await diagnose.check_egress(DiagnoseContext(http=client))

    assert result.status is StepStatus.WARN
    assert any("error" in str(value) for value in result.details.values())


async def test_egress_step_fails_when_nothing_is_reachable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with mock_client(handler) as client:
        result = await diagnose.check_egress(DiagnoseContext(http=client))

    assert result.status is StepStatus.FAIL


async def test_egress_step_passes_when_everything_answers() -> None:
    async with mock_client(lambda _r: httpx.Response(200)) as client:
        result = await diagnose.check_egress(DiagnoseContext(http=client))

    assert result.status is StepStatus.PASS


async def test_proxy_step_is_skipped_without_a_cipher() -> None:
    result = await diagnose.check_proxies(DiagnoseContext(session=FakeSession({})))  # type: ignore[arg-type]
    assert result.status is StepStatus.SKIP


async def test_proxy_step_warns_when_none_are_configured() -> None:
    ctx = DiagnoseContext(session=FakeSession({"FROM proxies": []}), cipher=Cipher(SECRET))  # type: ignore[arg-type]
    result = await diagnose.check_proxies(ctx)

    assert result.status is StepStatus.WARN
    assert "no proxies" in result.reason


async def test_proxy_step_never_prints_the_proxy_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = Cipher(SECRET)
    row = ProxyRow(cipher.encrypt("http://alice:hunter2@proxy.example.com:8080", aad=PROXY_ID))
    ctx = DiagnoseContext(session=FakeSession({"FROM proxies": [row]}), cipher=cipher)  # type: ignore[arg-type]

    async def fake_probe(proxy_url: str, probe_url: str) -> tuple[str | None, str | None]:
        assert proxy_url.startswith("http://alice:")
        del probe_url
        return "203.0.113.7", None

    monkeypatch.setattr(diagnose, "_probe_proxy", fake_probe)
    result = await diagnose.check_proxies(ctx)

    assert result.status is StepStatus.PASS
    assert result.details["residential-1"]["exit_ip"] == "203.0.113.7"
    assert "hunter2" not in str(result.as_dict())


async def test_proxy_step_fails_when_every_proxy_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cipher = Cipher(SECRET)
    row = ProxyRow(cipher.encrypt("http://alice:hunter2@proxy.example.com:8080", aad=PROXY_ID))
    ctx = DiagnoseContext(session=FakeSession({"FROM proxies": [row]}), cipher=cipher)  # type: ignore[arg-type]

    async def fake_probe(_proxy_url: str, _probe_url: str) -> tuple[str | None, str | None]:
        return None, "ConnectTimeout"

    monkeypatch.setattr(diagnose, "_probe_proxy", fake_probe)
    result = await diagnose.check_proxies(ctx)

    assert result.status is StepStatus.FAIL
    assert "unreachable" in str(result.details["residential-1"])


async def test_pool_step_fails_on_an_empty_pool() -> None:
    session = FakeSession(
        {
            "FROM identities GROUP BY state": [("cooling", 2)],
            "max(ts)": [(None,)],
            "min(last_used_at)": [(None,)],
        }
    )
    result = await diagnose.check_pool(DiagnoseContext(session=session))  # type: ignore[arg-type]

    assert result.status is StepStatus.FAIL
    assert result.details["active"] == 0


async def test_pool_step_warns_below_the_low_water_mark() -> None:
    session = FakeSession(
        {
            "FROM identities GROUP BY state": [("active", 1)],
            "max(ts)": [(None,)],
            "min(last_used_at)": [(None,)],
        }
    )
    result = await diagnose.check_pool(DiagnoseContext(session=session, min_active=3))  # type: ignore[arg-type]

    assert result.status is StepStatus.WARN


async def test_pool_step_passes_with_a_healthy_pool() -> None:
    session = FakeSession(
        {
            "FROM identities GROUP BY state": [("active", 12), ("cooling", 1)],
            "max(ts)": [(None,)],
            "min(last_used_at)": [(None,)],
        }
    )
    result = await diagnose.check_pool(DiagnoseContext(session=session, min_active=3))  # type: ignore[arg-type]

    assert result.status is StepStatus.PASS
    assert result.details["active"] == 12


async def test_signing_step_is_skipped_without_browser_rpc() -> None:
    result = await diagnose.check_signing(DiagnoseContext())
    assert result.status is StepStatus.SKIP


async def test_smoke_step_is_skipped_without_a_link() -> None:
    result = await diagnose.check_smoke(DiagnoseContext())
    assert result.status is StepStatus.SKIP


async def test_smoke_step_reports_a_failed_fetch() -> None:
    async def failing(_url: str) -> Any:
        raise RuntimeError("IDENTITY_POOL_EXHAUSTED")

    result = await diagnose.check_smoke(
        DiagnoseContext(smoke=failing, smoke_url="https://www.douyin.com/video/1")
    )

    assert result.status is StepStatus.FAIL
    assert "IDENTITY_POOL_EXHAUSTED" in result.reason


async def test_smoke_step_passes() -> None:
    async def ok(_url: str) -> dict[str, str]:
        return {"content_id": "1", "title": "x"}

    result = await diagnose.check_smoke(
        DiagnoseContext(smoke=ok, smoke_url="https://www.douyin.com/video/1")
    )

    assert result.status is StepStatus.PASS
    assert result.details["result"] == "dict with 2 keys"


# --------------------------------------------------------------------------
# the whole run
# --------------------------------------------------------------------------


async def test_run_produces_all_six_steps_in_order() -> None:
    async with mock_client(lambda _r: httpx.Response(200)) as client:
        report = await diagnose.run_diagnostics(DiagnoseContext(http=client))

    assert [step.number for step in report.steps] == [1, 2, 3, 4, 5, 6]
    assert [step.step for step in report.steps] == [
        "components",
        "egress",
        "proxies",
        "pool",
        "signing",
        "smoke",
    ]
    assert report.passed is False  # no database in a unit test
    assert all(step.reason for step in report.steps)
    assert report.render_text().endswith("\n")


async def test_a_crashing_step_becomes_a_failed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exploding(_ctx: DiagnoseContext) -> diagnose.StepResult:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(diagnose, "STEPS", (exploding,))
    report = await diagnose.run_diagnostics(DiagnoseContext())

    assert report.steps[0].status is StepStatus.FAIL
    assert "ZeroDivisionError" in report.steps[0].reason
