"""The self-check and, above all, its redaction.

A diagnostic report exists to be pasted into a public issue. Every test that
touches rendering therefore asserts on what is *absent* from the output: a
proxy password, a cookie, an API key or a signature parameter that survives
into the report is a credential leak with a friendly UI in front of it.

A step's finding is a code and its arguments, so that is what the assertions
here name. The sentence is a catalogue lookup made per reader, and asserting on
one would only pin down the English wording of the day.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from dtk.core.crypto import Cipher
from dtk.core.types import Language
from dtk.ops import diagnose
from dtk.ops.diagnose import DiagnoseContext, StepCode, StepStatus, redact_value

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


def _report(step: diagnose.StepResult) -> diagnose.DiagnosticReport:
    return diagnose.DiagnosticReport(
        version="5.0.0",
        started_at=diagnose.datetime.now(diagnose.UTC),
        finished_at=diagnose.datetime.now(diagnose.UTC),
        steps=(step,),
    )


def test_report_text_is_redacted() -> None:
    """Arguments carry upstream text, so they are a leak path of their own."""
    step = diagnose.StepResult(
        number=6,
        step="smoke",
        status=StepStatus.FAIL,
        code=StepCode.SMOKE_FAILED,
        args={"error": "ProxyError: http://alice:hunter2@proxy.example.com:8080 refused"},
        details=LEAKY_DETAILS,
    )
    report = _report(step)

    rendered = report.render_text()
    serialized = str(report.as_dict())

    for secret in SECRETS:
        assert secret not in rendered
        assert secret not in serialized
    assert "smoke" in rendered
    assert "FAIL" in rendered
    # The code is the half of the report a maintainer searches for.
    assert "smoke_failed" in serialized


# --------------------------------------------------------------------------
# codes, arguments and the language they are rendered in
# --------------------------------------------------------------------------


def _recording_t(seen: list[tuple[str, Any, dict[str, Any]]]) -> Any:
    def fake_t(key: str, language: Any = Language.EN, /, **args: Any) -> str:
        seen.append((key, language, args))
        return f"[{language}] {key}"

    return fake_t


def test_prose_is_looked_up_per_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """One finding, one code, two sentences - chosen by the caller's language."""
    seen: list[tuple[str, Any, dict[str, Any]]] = []
    monkeypatch.setattr(diagnose, "t", _recording_t(seen))
    step = diagnose.StepResult(
        number=4,
        step="pool",
        status=StepStatus.WARN,
        code=StepCode.POOL_BELOW_MINIMUM,
        args={"active": 1, "minimum": 3},
    )

    payload = step.as_dict(Language.ZH)

    assert payload["code"] == "pool_below_minimum"
    assert payload["action_code"] == "pool_below_minimum"
    assert payload["args"] == {"active": 1, "minimum": 3}
    assert {key for key, _language, _args in seen} == {
        "diagnose.reason.pool_below_minimum",
        "diagnose.action.pool_below_minimum",
    }
    assert all(language is Language.ZH for _key, language, _args in seen)
    assert all(args == {"active": 1, "minimum": 3} for _key, _language, args in seen)


def test_a_code_without_advice_renders_no_action() -> None:
    """A pass or a skip has nothing to suggest; an empty sentence is worse."""
    step = diagnose.StepResult(
        number=2, step="egress", status=StepStatus.PASS, code=StepCode.EGRESS_OK
    )

    assert step.action_code is None
    assert step.action is None
    assert step.as_dict(Language.ZH)["action"] is None


def test_the_cli_and_the_logs_stay_english() -> None:
    step = diagnose.StepResult(
        number=4, step="pool", status=StepStatus.FAIL, code=StepCode.POOL_EMPTY
    )

    assert step.reason == step.localized_reason(Language.EN)
    assert step.action == step.localized_action(Language.EN)


def test_a_warning_alone_is_not_a_failed_run() -> None:
    """A pool running thin is a note, not a broken instance.

    Folding WARN in with FAIL made a healthy instance report "verdict FAIL"
    and exit 1, and a verdict that cries wolf is one an operator stops reading.
    """
    report = _report(
        diagnose.StepResult(number=4, step="pool", status=StepStatus.WARN, code=StepCode.POOL_BELOW_MINIMUM)
    )

    assert report.verdict == "warn"
    assert report.passed is True
    assert report.failures == ()
    assert len(report.warnings) == 1
    assert "verdict  WARN" in report.render_text()


def test_a_failure_outranks_a_warning() -> None:
    report = diagnose.DiagnosticReport(
        version="5.0.0",
        started_at=diagnose.datetime.now(diagnose.UTC),
        finished_at=diagnose.datetime.now(diagnose.UTC),
        steps=(
            diagnose.StepResult(
                number=4, step="pool", status=StepStatus.WARN, code=StepCode.POOL_BELOW_MINIMUM
            ),
            diagnose.StepResult(
                number=1,
                step="components",
                status=StepStatus.FAIL,
                code=StepCode.COMPONENTS_UNREACHABLE,
            ),
        ),
    )

    assert report.verdict == "fail"
    assert report.passed is False


def test_every_step_passing_is_a_pass() -> None:
    report = _report(
        diagnose.StepResult(number=4, step="pool", status=StepStatus.PASS, code=StepCode.POOL_OK)
    )

    assert report.verdict == "pass"
    assert "verdict  PASS" in report.render_text()


def test_a_stored_report_can_be_rendered_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker stores the report; the reader's language arrives afterwards."""
    monkeypatch.setattr(diagnose, "t", _recording_t([]))
    stored = _report(
        diagnose.StepResult(number=4, step="pool", status=StepStatus.FAIL, code=StepCode.POOL_EMPTY)
    ).as_dict()

    localized = diagnose.localize_report(stored, Language.ZH)

    assert localized["steps"][0]["code"] == "pool_empty"
    assert localized["steps"][0]["reason"] == "[zh] diagnose.reason.pool_empty"
    assert localized["steps"][0]["action"] == "[zh] diagnose.action.pool_empty"
    assert "[zh] diagnose.reason.pool_empty" in localized["text"]
    # The stored payload is the worker's; re-rendering must not rewrite it.
    assert stored["steps"][0]["reason"] == "[en] diagnose.reason.pool_empty"


def test_localizing_an_unrecognized_payload_changes_nothing() -> None:
    """The endpoint that explains a broken install must not break on one."""
    assert diagnose.localize_report({"version": "5.0.0"}, Language.ZH) == {"version": "5.0.0"}


# --------------------------------------------------------------------------
# individual steps
# --------------------------------------------------------------------------


async def test_components_step_fails_without_a_database() -> None:
    result = await diagnose.check_components(DiagnoseContext())

    assert result.status is StepStatus.FAIL
    assert result.code is StepCode.COMPONENTS_UNREACHABLE
    assert result.action_code is StepCode.COMPONENTS_UNREACHABLE
    assert result.args["components"] == "postgres, redis"
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
    assert result.code is StepCode.PROXIES_NONE


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
    assert result.code is StepCode.SMOKE_FAILED
    # The upstream text is evidence: it rides along as an argument, untranslated.
    assert "IDENTITY_POOL_EXHAUSTED" in result.args["error"]


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
    assert all(isinstance(step.code, StepCode) for step in report.steps)
    assert report.render_text().endswith("\n")


async def test_a_crashing_step_becomes_a_failed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exploding(_ctx: DiagnoseContext) -> diagnose.StepResult:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(diagnose, "STEPS", (exploding,))
    report = await diagnose.run_diagnostics(DiagnoseContext())

    assert report.steps[0].status is StepStatus.FAIL
    assert report.steps[0].code is StepCode.STEP_CRASHED
    assert "ZeroDivisionError" in report.steps[0].args["error"]


def test_an_object_that_only_leaks_through_its_str_is_still_redacted():
    """The regression this guards was real, and it was introduced by a refactor.

    ``redact_value`` recognises strings, dicts and lists. Everything else used
    to be returned untouched, which was safe only because the old text renderer
    wrapped the whole thing in ``redact(str(...))`` again. Rendering from an
    already-redacted payload dropped that second pass, and an upstream driver
    error - the single most likely detail value to carry a live session - went
    out verbatim in a report whose whole purpose is to be pasted in public.
    """

    class UpstreamError:
        def __str__(self) -> str:
            return "connect failed for sessionid=deadbeefcafebabe0123456789"  # SYNTHETIC

    assert "deadbeef" not in str(redact_value(UpstreamError(), "note"))
    # Scalars are left alone: they are not a leak, and stringifying them would
    # turn a JSON number into a JSON string on the wire.
    assert redact_value(42, "n") == 42
    assert redact_value(None, "n") is None
    assert redact_value(True, "n") is True
