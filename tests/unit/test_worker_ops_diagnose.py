"""The console's "run the self check" job.

The six steps belong to :mod:`dtk.ops.diagnose` and are tested there. What is
asserted here is the wiring, and two properties of it matter more than any step
outcome.

The report has to reach the console as the interface ``Diagnose.tsx`` reads: a
field that page wants and does not get renders as blank rather than as an error,
so it is never reported as missing.

And it has to arrive with its codes and arguments intact. The worker has no
reader - the task is fetched later, by whoever asks, in whatever language they
negotiated - so a report flattened to English sentences here can never be
anything else afterwards.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from dtk.core.config import Config
from dtk.core.errors import Internal
from dtk.core.types import Language, Platform
from dtk.ops import diagnose as ops_diagnose
from dtk.ops import pipeline
from dtk.ops.diagnose import DiagnoseContext, DiagnosticReport, StepCode, StepStatus
from dtk.ops.probes import SMOKE_URLS
from dtk.worker.ops import OperationDeps
from dtk.worker.ops import diagnose as diagnose_op

#: SYNTHETIC. Long enough to satisfy the bootstrap check, and never a
#: real deployment key.
TEST_SECRET_KEY = "t" * 48

#: Placeholders the job must pass through untouched rather than rebuild. Their
#: identity is the assertion: a second transport or a second signer registry
#: would compare unequal here and cost a second browser-rpc connection there.
SESSION = object()
CIPHER = object()
TRANSPORT = object()
SIGNERS = object()

#: A leak this job could plausibly produce: the smoke request is signed and
#: proxied, so whatever it raises has been near all three of these.
LEAKY_FAILURE = (
    "network_error: GET https://www.douyin.com/aweme/v1/web/aweme/detail/"
    "?aweme_id=1&msToken=AAAABBBBCCCCDDDD failed through "
    "http://alice:hunter2@proxy.example.com:8080 "
    "(Cookie: sessionid=abc123def456) for dtk_a1b2c3d4_TQm9xVeryLongSecretValue"
)
SECRETS = (
    "hunter2",
    "abc123def456",
    "AAAABBBBCCCCDDDD",
    "TQm9xVeryLongSecretValue",
)


class FakeRpc:
    """Only ``configured`` is read before the client is handed over."""

    def __init__(self, *, configured: bool) -> None:
        self.configured = configured


def fake_deps(*, config: Config | None = None, rpc: Any = None) -> OperationDeps:
    snapshot = config or Config.defaults()
    return OperationDeps(
        config=lambda: snapshot,
        cipher=cast(Any, CIPHER),
        secret_key=TEST_SECRET_KEY,
        pool=cast(Any, None),
        transport=cast(Any, TRANSPORT),
        signers=cast(Any, SIGNERS),
        rpc=rpc,
    )


async def run_job(deps: OperationDeps | None = None, **params: Any) -> Any:
    return await diagnose_op.run(deps or fake_deps(), cast(Any, SESSION), params)


def only_step(monkeypatch: pytest.MonkeyPatch, step: Any) -> None:
    """Run one real step. The others reach the network or the database."""
    monkeypatch.setattr(ops_diagnose, "STEPS", (step,))


def capture_context(monkeypatch: pytest.MonkeyPatch) -> list[DiagnoseContext]:
    """Record what the job assembled, without running any of it."""
    seen: list[DiagnoseContext] = []

    async def fake(ctx: DiagnoseContext | None = None) -> DiagnosticReport:
        seen.append(cast(DiagnoseContext, ctx))
        now = datetime.now(UTC)
        return DiagnosticReport(version="test", started_at=now, finished_at=now, steps=())

    monkeypatch.setattr(diagnose_op, "run_diagnostics", fake)
    return seen


# --------------------------------------------------------------------------
# what the job hands the shared implementation
# --------------------------------------------------------------------------


async def test_the_workers_own_collaborators_are_what_gets_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = capture_context(monkeypatch)

    await run_job()

    ctx = seen[0]
    assert ctx.session is SESSION
    assert ctx.cipher is CIPHER
    assert ctx.registry is SIGNERS
    assert isinstance(ctx.http, httpx.AsyncClient)


async def test_the_low_water_mark_comes_from_the_live_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = capture_context(monkeypatch)

    # Not the default: the pool step must compare against what this instance is
    # configured for, or "below minimum" is advice about somebody else's pool.
    await run_job(fake_deps(config=Config({"pool.min_size": 7})))

    assert seen[0].min_active == 7


async def test_a_process_without_an_engine_reports_rather_than_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No engine and no Redis here, which is exactly the case worth surviving."""
    seen = capture_context(monkeypatch)

    await run_job()

    assert seen[0].engine is None
    assert seen[0].redis is None


async def test_unconfigured_browser_rpc_is_absent_rather_than_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = capture_context(monkeypatch)

    await run_job(fake_deps(rpc=FakeRpc(configured=False)))
    configured = FakeRpc(configured=True)
    await run_job(fake_deps(rpc=configured))

    # Absent skips the signing step; present but unconfigured would fail it,
    # which is a red mark nobody can act on.
    assert seen[0].rpc is None
    assert seen[1].rpc is configured


# --------------------------------------------------------------------------
# the end-to-end step
# --------------------------------------------------------------------------


async def test_the_smoke_step_runs_on_the_stack_the_worker_already_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_smoke(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return {"endpoint": "douyin.content_detail", "outcome": "ok"}

    monkeypatch.setattr(pipeline, "smoke", fake_smoke)
    seen = capture_context(monkeypatch)

    await run_job(include_smoke_test=True)
    smoke = seen[0].smoke
    assert smoke is not None
    await smoke(cast(str, seen[0].smoke_url))

    assert seen[0].smoke_url == SMOKE_URLS[Platform.DOUYIN]
    assert calls[0]["url"] == SMOKE_URLS[Platform.DOUYIN]
    assert calls[0]["session"] is SESSION
    assert calls[0]["cipher"] is CIPHER
    assert calls[0]["transport"] is TRANSPORT
    assert calls[0]["signers"] is SIGNERS


async def test_declining_the_smoke_test_makes_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("the smoke step ran after being declined")

    monkeypatch.setattr(pipeline, "smoke", refuse)
    only_step(monkeypatch, ops_diagnose.check_smoke)

    result = await run_job(include_smoke_test=False)

    step = result["data"]["steps"][0]
    assert step["status"] == StepStatus.SKIP.value
    assert step["code"] == StepCode.SMOKE_NOT_CONFIGURED.value
    assert result["meta"]["smoke_test"] is False


async def test_a_successful_smoke_test_reaches_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_smoke(_url: str, **_kwargs: Any) -> dict[str, Any]:
        return {"endpoint": "douyin.content_detail", "outcome": "ok"}

    monkeypatch.setattr(pipeline, "smoke", fake_smoke)
    only_step(monkeypatch, ops_diagnose.check_smoke)

    result = await run_job(include_smoke_test=True)

    step = result["data"]["steps"][0]
    assert step["status"] == StepStatus.PASS.value
    assert step["code"] == StepCode.SMOKE_OK.value
    assert step["details"]["url"] == SMOKE_URLS[Platform.DOUYIN]


async def test_a_failed_smoke_test_cannot_carry_what_it_was_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The result is stored in the database and rendered in a browser."""

    async def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise Internal(LEAKY_FAILURE)

    monkeypatch.setattr(pipeline, "smoke", fail)
    only_step(monkeypatch, ops_diagnose.check_smoke)

    result = await run_job(include_smoke_test=True)

    step = result["data"]["steps"][0]
    assert step["status"] == StepStatus.FAIL.value
    assert step["code"] == StepCode.SMOKE_FAILED.value
    stored = json.dumps(result)
    for secret in SECRETS:
        assert secret not in stored
    # The evidence a maintainer searches for survives the masking.
    assert "network_error" in stored


# --------------------------------------------------------------------------
# the console contract
# --------------------------------------------------------------------------


async def test_result_matches_the_diagnostic_report_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_step(monkeypatch, ops_diagnose.check_components)

    result = await run_job(include_smoke_test=False)

    # web/src/pages/Diagnose.tsx reads run.data.data as the report itself.
    report = result["data"]
    assert set(report) == {
        "version",
        "started_at",
        "finished_at",
        "passed",
        "verdict",
        "steps",
        "text",
    }
    assert isinstance(report["text"], str)
    assert set(report["steps"][0]) == {
        "number",
        "step",
        "status",
        "code",
        "action_code",
        "args",
        "reason",
        "action",
        "details",
        "duration_ms",
    }


async def test_result_carries_the_meta_the_worker_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_step(monkeypatch, ops_diagnose.check_components)

    result = await run_job(include_smoke_test=False)

    assert result["meta"]["endpoint"] == "diagnose"
    assert result["meta"]["passed"] is False
    assert result["meta"]["verdict"] == "fail"
    assert result["meta"]["failures"] == 1
    assert result["meta"]["warnings"] == 0
    assert isinstance(result["meta"]["duration_ms"], float)


async def test_the_stored_report_can_still_be_read_in_another_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of running it: the codes outlive the English rendering."""
    only_step(monkeypatch, ops_diagnose.check_components)

    result = await run_job(include_smoke_test=False)

    english = result["data"]
    assert english["steps"][0]["code"] == StepCode.COMPONENTS_UNREACHABLE.value
    assert english["steps"][0]["args"]["components"]

    chinese = ops_diagnose.localize_report(english, Language.ZH)
    assert chinese["steps"][0]["reason"] != english["steps"][0]["reason"]
    assert chinese["steps"][0]["code"] == english["steps"][0]["code"]
    # The re-rendered text is what the console copies, so it moves too.
    assert chinese["text"] != english["text"]
