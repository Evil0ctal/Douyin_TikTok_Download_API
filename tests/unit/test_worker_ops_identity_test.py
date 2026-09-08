"""Unit tests for the ``identity.test`` maintenance job.

The job is wiring, so the tests exercise it through the real collaborators it
wires together - the real identity lookup, the real URL parser, the real
endpoint registry and the real classifier - and replace exactly one thing: the
signed HTTP call itself. A test that stubbed the probe would only prove that a
dict can be built.

Two properties are worth more than the rest. The result has to match the shape
``web/src/pages/Identities.tsx`` reads field for field, because a missing field
renders as blank instead of failing. And nothing it returns may carry a
credential: an identity is a cookie jar behind a proxy, the signed URL carries
msToken and a_bogus, and the result is stored in the database and rendered in a
browser.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from dtk.core.config import Config
from dtk.core.errors import InvalidParam, NotFound
from dtk.core.types import IdentityState, Outcome, Platform
from dtk.ops import pipeline, probes
from dtk.transport.base import RawResponse, TransportFailure
from dtk.transport.classify import DEFAULT_CLASSIFIER
from dtk.worker.ops import identity_test

#: SYNTHETIC. Long enough to satisfy the bootstrap check, and never a
#: real deployment key.
TEST_SECRET_KEY = "t" * 48


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------

COOKIE_HEADER = "ttwid=SYNTHETICTTWIDVALUE; sessionid=SYNTHETICSESSIONVALUE"
PROXY_URL = "http://bob:hunter2@10.0.0.9:8080"

#: A signed URL as the transport would report it in a failure message.
SIGNED_URL = (
    "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=7298145681699622182"
    "&msToken=SUPERSECRETMSTOKENVALUE&a_bogus=DDDDDDDDDDDDDDDD"
)


@dataclass
class FakeIdentityRow:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    platform: str = Platform.DOUYIN.value
    state: str = IdentityState.ACTIVE.value
    cookies_encrypted: bytes = b"ciphertext"
    fingerprint: dict[str, Any] = field(
        default_factory=lambda: {
            "browser_family": "chrome",
            "browser_major": 130,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0.0.0",
            "platform": "Win32",
            "screen": "1920x1080",
        }
    )
    proxy_id: uuid.UUID | None = None
    authenticated: bool = True


@dataclass
class FakeProxyRow:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    url_encrypted: bytes = b"proxy-ciphertext"


class FakeSession:
    """``session.get`` is the only thing this job's lookups reach for."""

    def __init__(self, *rows: Any) -> None:
        self._rows = {row.id: row for row in rows}
        self.added: list[Any] = []
        self.committed = 0

    async def get(self, _model: Any, pk: Any) -> Any:
        return self._rows.get(pk)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.committed += 1


class FakeCipher:
    """Decrypts to the plaintext the row stands for; never used to encrypt."""

    def decrypt(self, payload: bytes, *, aad: str = "") -> str:
        return PROXY_URL if payload == b"proxy-ciphertext" else COOKIE_HEADER


class FakeTransport:
    """Classifies with the real ruleset; the request itself is patched out."""

    def classify(self, response: RawResponse) -> Any:
        return DEFAULT_CLASSIFIER.classify(response)


def make_deps(session_pool: Any = None) -> Any:
    from dtk.identity.pool import IdentityPool
    from dtk.worker.ops import OperationDeps

    cipher = FakeCipher()
    return OperationDeps(
        config=Config.defaults,
        cipher=cipher,  # type: ignore[arg-type]
        secret_key=TEST_SECRET_KEY,
        pool=session_pool or IdentityPool(cipher),  # type: ignore[arg-type]
        transport=FakeTransport(),  # type: ignore[arg-type]
        signers=object(),  # type: ignore[arg-type]
    )


def run_job(session: FakeSession, params: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(identity_test.run(make_deps(), session, params))  # type: ignore[arg-type]


def patch_call(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> list[Any]:
    """Replace the one signed HTTP call, recording what it was asked to send."""
    calls: list[Any] = []

    async def call_endpoint(
        _transport: Any, _signers: Any, identity: Any, call: Any, **kwargs: Any
    ) -> RawResponse:
        calls.append((identity, call, kwargs))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(pipeline, "call_endpoint", call_endpoint)
    return calls


def ok_response() -> RawResponse:
    return RawResponse(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps({"status_code": 0, "aweme_detail": {"aweme_id": "1"}}).encode(),
        elapsed_ms=142,
    )


# --------------------------------------------------------------------------
# the contract with the console
# --------------------------------------------------------------------------


class TestResultShape:
    def test_a_healthy_identity_reports_every_field_the_page_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = FakeIdentityRow()
        patch_call(monkeypatch, ok_response())

        result = run_job(FakeSession(row), {"identity_id": str(row.id)})

        assert set(result["data"]) == {
            "ok",
            "outcome",
            "status",
            "latency_ms",
            "rule",
            "detail",
        }
        assert result["data"]["ok"] is True
        assert result["data"]["outcome"] == Outcome.OK.value
        assert result["data"]["status"] == 200
        assert result["data"]["latency_ms"] == 142
        assert result["data"]["rule"] == "default.ok"

    def test_the_outcome_is_the_wire_value_of_the_enum(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The page compares against Outcome's wire values, not repr strings."""
        row = FakeIdentityRow()
        patch_call(monkeypatch, ok_response())

        outcome = run_job(FakeSession(row), {"identity_id": str(row.id)})["data"]["outcome"]

        assert outcome in {member.value for member in Outcome}

    def test_the_metadata_names_the_call_that_was_made(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = FakeIdentityRow()
        patch_call(monkeypatch, ok_response())

        meta = run_job(FakeSession(row), {"identity_id": str(row.id)})["meta"]

        assert meta["identity_id"] == str(row.id)
        assert meta["platform"] == Platform.DOUYIN.value
        assert meta["endpoint"] == "douyin.content_detail"

    def test_a_tiktok_identity_probes_the_tiktok_smoke_link(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = FakeIdentityRow(platform=Platform.TIKTOK.value)
        calls = patch_call(monkeypatch, ok_response())

        meta = run_job(FakeSession(row), {"identity_id": str(row.id)})["meta"]

        assert meta["endpoint"] == "tiktok.content_detail"
        assert calls[0][1].platform is Platform.TIKTOK

    def test_the_result_survives_json_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """It is written to a jsonb column, so nothing exotic may be in it."""
        row = FakeIdentityRow()
        patch_call(monkeypatch, ok_response())

        result = run_job(FakeSession(row), {"identity_id": str(row.id)})

        assert json.loads(json.dumps(result)) == result


class TestFailedProbe:
    def test_a_transport_failure_is_a_result_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = FakeIdentityRow()
        patch_call(
            monkeypatch,
            TransportFailure(
                f"GET {SIGNED_URL} failed: ConnectError",
                identity_id=str(row.id),
                url=SIGNED_URL,
                elapsed_ms=17,
            ),
        )

        data = run_job(FakeSession(row), {"identity_id": str(row.id)})["data"]

        assert data["ok"] is False
        assert data["outcome"] == Outcome.NETWORK_ERROR.value
        assert data["status"] is None
        assert data["detail"]

    def test_a_deleted_post_still_counts_as_an_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The question is whether the platform talks to this identity."""
        row = FakeIdentityRow()
        patch_call(
            monkeypatch,
            RawResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps({"status_code": 2053, "status_msg": "gone"}).encode(),
                elapsed_ms=90,
            ),
        )

        data = run_job(FakeSession(row), {"identity_id": str(row.id)})["data"]

        assert data["outcome"] == Outcome.BUSINESS_ERROR.value
        assert data["ok"] is True


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------


class TestNothingLeaks:
    def _serialized(self, monkeypatch: pytest.MonkeyPatch, outcome: Any) -> str:
        row = FakeIdentityRow(proxy_id=uuid.uuid4())
        proxy = FakeProxyRow(id=row.proxy_id)  # type: ignore[arg-type]
        patch_call(monkeypatch, outcome)
        return json.dumps(run_job(FakeSession(row, proxy), {"identity_id": str(row.id)}))

    def test_a_signed_url_in_a_failure_loses_its_signature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stored = self._serialized(
            monkeypatch,
            TransportFailure(
                f"GET {SIGNED_URL} failed: ConnectError",
                identity_id="i",
                url=SIGNED_URL,
                elapsed_ms=3,
            ),
        )

        assert "SUPERSECRETMSTOKENVALUE" not in stored
        assert "DDDDDDDDDDDDDDDD" not in stored
        # The endpoint the probe hit is the useful half, and it survives.
        assert "aweme_id=7298145681699622182" in stored

    def test_no_cookie_value_reaches_the_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stored = self._serialized(
            monkeypatch,
            TransportFailure(
                f"GET {SIGNED_URL} failed, cookies were {COOKIE_HEADER}",
                identity_id="i",
                url=SIGNED_URL,
                elapsed_ms=3,
            ),
        )

        assert "SYNTHETICSESSIONVALUE" not in stored
        assert "SYNTHETICTTWIDVALUE" not in stored

    def test_the_proxy_password_never_appears(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stored = self._serialized(monkeypatch, ok_response())

        assert "hunter2" not in stored
        # The host is what identifies the egress, and it is not a secret.
        assert "10.0.0.9:8080" in stored


# --------------------------------------------------------------------------
# refusals and side effects
# --------------------------------------------------------------------------


class TestRefusals:
    def test_a_missing_identity_id_is_refused(self) -> None:
        with pytest.raises(InvalidParam):
            run_job(FakeSession(), {})

    def test_a_blank_identity_id_is_refused(self) -> None:
        with pytest.raises(InvalidParam):
            run_job(FakeSession(), {"identity_id": "  "})

    def test_a_malformed_identity_id_is_refused(self) -> None:
        with pytest.raises(InvalidParam):
            run_job(FakeSession(), {"identity_id": "not-an-id"})

    def test_an_unknown_identity_is_a_not_found(self) -> None:
        with pytest.raises(NotFound):
            run_job(FakeSession(), {"identity_id": str(uuid.uuid4())})

    def test_a_retired_identity_is_refused_rather_than_probed(self) -> None:
        """Its cookies were wiped, so the probe would test nothing."""
        row = FakeIdentityRow(state=IdentityState.RETIRED.value)

        with pytest.raises(InvalidParam):
            run_job(FakeSession(row), {"identity_id": str(row.id)})


class TestTheProbeChangesNothing:
    def test_it_writes_no_rows_and_commits_nothing_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No lease, no bookkeeping; the runner owns the commit."""
        row = FakeIdentityRow()
        patch_call(monkeypatch, ok_response())
        session = FakeSession(row)

        run_job(session, {"identity_id": str(row.id)})

        assert session.added == []
        assert session.committed == 0

    def test_it_probes_with_the_identity_it_was_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = FakeIdentityRow()
        calls = patch_call(monkeypatch, ok_response())

        run_job(FakeSession(row), {"identity_id": str(row.id)})

        identity, call, kwargs = calls[0]
        assert identity.id == str(row.id)
        assert identity.cookies["sessionid"] == "SYNTHETICSESSIONVALUE"
        assert call.endpoint == "douyin.content_detail"
        assert kwargs["timeout"] == pipeline.REQUEST_TIMEOUT_SECONDS


class TestSmokeCoverage:
    def test_every_platform_has_a_smoke_link(self) -> None:
        """A platform without one would make the job a KeyError."""
        assert set(probes.SMOKE_URLS) == set(Platform)
