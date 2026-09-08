"""CLI tests.

Three things are worth testing about a command line and they are all here:

* every command has help and it renders - a broken help page is the first thing
  an operator hits and the last thing anyone notices;
* the exit codes follow the contract, because scripts branch on them: 0 worked,
  1 ran and failed, 2 called wrong;
* nothing that touches a credential prints one.

No test here needs PostgreSQL or Redis. Commands reach the outside world through
exactly one seam, :func:`dtk.cli.runtime.with_context`, and these tests replace
it with a context over a fake session - so the command bodies, including their
masking, really do run.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from dtk.cli import identities, output, proxies, runtime, settings_cmd, users
from dtk.cli.main import app
from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher
from dtk.core.types import IdentityState, Platform, UserRole
from dtk.db.models import Identity, Proxy, User
from dtk.ops.masking import (
    ABSENT,
    MASK,
    mask_api_key,
    mask_cookies,
    mask_endpoint,
    mask_secret,
    mask_url,
    scrub,
    short_id,
)

#: Long enough for Cipher, and obviously not a real key.
TEST_SECRET = "test-secret-key-that-is-long-enough-0123456789"

PROXY_URL = "http://bob:hunter2@10.0.0.9:3128"

#: Every command in the tree. Used for the help sweep, so a command added
#: without help is a failing test rather than a surprise in production.
ALL_COMMANDS: tuple[tuple[str, ...], ...] = (
    (),
    ("user",),
    ("user", "create"),
    ("user", "passwd"),
    ("user", "list"),
    ("backup",),
    ("backup", "create"),
    ("backup", "list"),
    ("backup", "restore"),
    ("config",),
    ("config", "get"),
    ("config", "set"),
    ("config", "list"),
    ("identity",),
    ("identity", "list"),
    ("identity", "mint"),
    ("identity", "retire"),
    ("identity", "test"),
    ("proxy",),
    ("proxy", "list"),
    ("proxy", "add"),
    ("proxy", "import"),
    ("proxy", "test"),
    ("fetch",),
    ("diagnose",),
    ("worker",),
    ("migrate",),
    ("serve",),
)


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop rich from wrapping table cells mid-assertion."""
    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.setenv("TERM", "dumb")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cipher() -> Cipher:
    return Cipher(TEST_SECRET)


def settings() -> BootstrapSettings:
    return BootstrapSettings(
        secret_key=TEST_SECRET,
        database_url="postgresql+asyncpg://dtk:dtk@nowhere:5432/dtk",
        redis_url="redis://nowhere:6379/0",
        browser_rpc_url="",
    )


class FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeSession:
    """Just enough AsyncSession for the repositories the CLI uses."""

    def __init__(self, *, scalars: list[list[Any]] | None = None, rowcount: int = 1) -> None:
        self._scalars = list(scalars or [])
        self.rowcount = rowcount
        self.added: list[Any] = []
        self.executed = 0

    async def scalars(self, _statement: Any) -> FakeScalars:
        return FakeScalars(self._scalars.pop(0) if self._scalars else [])

    async def execute(self, _statement: Any) -> FakeResult:
        self.executed += 1
        return FakeResult(self.rowcount)

    async def get(self, _model: Any, _pk: Any) -> Any:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def install_context(
    monkeypatch: pytest.MonkeyPatch,
    session: FakeSession,
    *,
    config: Config | None = None,
) -> None:
    """Replace the one seam that opens a database and a Redis connection."""
    context = runtime.Context(
        settings=settings(),
        cipher=Cipher(TEST_SECRET),
        session=session,  # type: ignore[arg-type]
        config=config or Config.defaults(),
    )

    def fake_with_context(operation: Any, **_kwargs: Any) -> Any:
        return asyncio.run(operation(context))

    monkeypatch.setattr(runtime, "with_context", fake_with_context)


# ---------------------------------------------------------------------------
# help and exit codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ALL_COMMANDS, ids=lambda c: " ".join(c) or "root")
def test_every_command_has_help(runner: CliRunner, command: tuple[str, ...]) -> None:
    result = runner.invoke(app, [*command, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    if command:
        assert command[-1] in result.output


def test_version_flag(runner: CliRunner) -> None:
    from dtk import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_bare_invocation_shows_help(runner: CliRunner) -> None:
    """No command at all is a usage error, and it prints the usage."""
    result = runner.invoke(app, [])
    assert result.exit_code == output.EXIT_USAGE
    assert "Usage:" in result.output


def test_unknown_command_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(app, ["nonsense"])
    assert result.exit_code == 2


def test_missing_argument_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(app, ["user", "create"])
    assert result.exit_code == 2


def test_invalid_enum_value_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(app, ["user", "create", "alice", "--role", "wizard"])
    assert result.exit_code == 2


def test_unknown_setting_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(app, ["config", "get", "cache.does_not_exist"])
    assert result.exit_code == 2
    assert "unknown setting" in result.output


def test_bad_setting_value_is_a_usage_error(runner: CliRunner) -> None:
    result = runner.invoke(app, ["config", "set", "cache.content_ttl", "not-a-number"])
    assert result.exit_code == 2


def test_missing_backup_file_is_a_usage_error(runner: CliRunner, tmp_path: Any) -> None:
    result = runner.invoke(app, ["backup", "restore", str(tmp_path / "absent.tar.gz")])
    assert result.exit_code == 2


def test_serve_rejects_reload_with_workers_before_reading_the_environment(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad invocation is exit 2 even on a box with no DTK_SECRET_KEY."""
    from dtk.core.crypto import SecretKeyMissing

    def refuse() -> BootstrapSettings:
        raise SecretKeyMissing("DTK_SECRET_KEY must be set")

    monkeypatch.setattr(runtime, "BootstrapSettings", refuse)
    result = runner.invoke(app, ["serve", "--reload", "--workers", "4"])
    assert result.exit_code == output.EXIT_USAGE


def test_exit_codes_are_the_documented_three() -> None:
    assert (output.EXIT_OK, output.EXIT_FAILURE, output.EXIT_USAGE) == (0, 1, 2)


# ---------------------------------------------------------------------------
# masking
# ---------------------------------------------------------------------------


class TestMasking:
    def test_absent_values_render_as_the_absent_marker(self) -> None:
        assert mask_secret(None) == ABSENT
        assert mask_url(None) == ABSENT
        assert mask_cookies(None) == ABSENT
        assert mask_api_key(None) == ABSENT
        assert short_id(None) == ABSENT

    def test_short_secrets_are_masked_whole(self) -> None:
        assert mask_secret("abc") == MASK
        assert mask_secret("abcd") == MASK

    def test_long_secrets_keep_only_a_prefix(self) -> None:
        masked = mask_secret("abcdefghijklmno")
        assert masked == f"abcd{MASK}"
        assert "efghij" not in masked

    def test_proxy_password_never_survives(self) -> None:
        masked = mask_url(PROXY_URL)
        assert "hunter2" not in masked
        assert "10.0.0.9:3128" in masked
        assert masked.startswith("http://")

    def test_proxy_without_credentials_is_unchanged(self) -> None:
        assert mask_url("socks5://10.0.0.9:1080") == "socks5://10.0.0.9:1080"

    def test_cookies_are_reduced_to_their_names(self) -> None:
        rendered = mask_cookies({"sessionid": "abc123", "ttwid": "xyz789"})
        assert rendered == "sessionid, ttwid"
        assert "abc123" not in rendered

    def test_api_key_shows_only_its_prefix(self) -> None:
        assert mask_api_key("a1b2c3") == f"dtk_a1b2c3_{MASK}"

    def test_scrub_removes_a_database_password(self) -> None:
        scrubbed = scrub("could not connect to postgresql+asyncpg://dtk:s3cr3t@db:5432/dtk")
        assert "s3cr3t" not in scrubbed
        assert "dtk:" in scrubbed

    def test_scrub_is_not_fooled_by_scheme_casing(self) -> None:
        assert "s3cr3t" not in scrub("PostgreSQL://dtk:s3cr3t@db:5432/dtk")

    def test_scrub_truncates_signature_parameters(self) -> None:
        scrubbed = scrub("GET /aweme/detail?msToken=AAAABBBBCCCCDDDD&aweme_id=1")
        assert "AAAABBBBCCCCDDDD" not in scrubbed
        assert "aweme_id=1" in scrubbed

    def test_endpoint_url_keeps_only_the_host(self) -> None:
        masked = mask_endpoint("https://api.telegram.org/bot123:ABCDEF/sendMessage")
        assert masked == f"https://api.telegram.org/{MASK}"

    def test_endpoint_url_without_a_path_is_kept(self) -> None:
        assert mask_endpoint("https://hooks.example.com/") == "https://hooks.example.com"

    def test_endpoint_url_that_is_not_a_url_is_masked_whole(self) -> None:
        assert mask_endpoint("bot123:ABCDEF") == MASK

    def test_short_id_is_a_stable_prefix(self) -> None:
        identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert short_id(identifier) == "12345678"


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class TestUsers:
    def test_password_hash_is_argon2id(self) -> None:
        digest = users.hash_password("correct horse battery")
        assert digest.startswith("$argon2id$")

    def test_short_password_fails_without_writing(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = FakeSession(scalars=[[]])
        install_context(monkeypatch, session)
        result = runner.invoke(app, ["user", "create", "alice"], input="short\nshort\n")
        assert result.exit_code == 1
        assert "at least" in result.output
        assert session.added == []

    def test_create_stores_a_hash_and_never_echoes_the_password(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = FakeSession(scalars=[[]])
        install_context(monkeypatch, session)
        result = runner.invoke(
            app,
            ["user", "create", "alice", "--role", "operator"],
            input="correct horse battery\ncorrect horse battery\n",
        )
        assert result.exit_code == 0, result.output
        assert "correct horse battery" not in result.output
        assert len(session.added) == 1
        created = session.added[0]
        assert created.username == "alice"
        assert created.role == UserRole.OPERATOR.value
        assert created.password_hash.startswith("$argon2id$")
        assert created.password_hash not in result.output

    def test_create_refuses_an_existing_username(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = User(
            id=uuid.uuid4(), username="alice", password_hash="$argon2id$x", role="admin"
        )
        install_context(monkeypatch, FakeSession(scalars=[[existing]]))
        result = runner.invoke(
            app, ["user", "create", "alice"], input="a-good-password\na-good-password\n"
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_passwd_reports_an_unknown_user(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession(scalars=[[]]))
        result = runner.invoke(
            app, ["user", "passwd", "ghost"], input="a-good-password\na-good-password\n"
        )
        assert result.exit_code == 1
        assert "no user named ghost" in result.output

    def test_passwd_reads_stdin_without_a_prompt(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = User(
            id=uuid.uuid4(), username="alice", password_hash="$argon2id$old", role="admin"
        )
        session = FakeSession(scalars=[[existing]])
        install_context(monkeypatch, session)
        result = runner.invoke(
            app, ["user", "passwd", "alice", "--stdin"], input="a-brand-new-password\n"
        )
        assert result.exit_code == 0, result.output
        assert "a-brand-new-password" not in result.output
        assert session.executed == 1

    def test_stdin_password_ignores_a_carriage_return(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A password piped from a CRLF file must hash the same as a typed one."""
        from argon2 import PasswordHasher

        existing = User(
            id=uuid.uuid4(), username="alice", password_hash="$argon2id$old", role="admin"
        )
        session = FakeSession(scalars=[[existing]])
        install_context(monkeypatch, session)
        recorded: dict[str, str] = {}
        real = users.hash_password

        def spy(password: str) -> str:
            recorded["password"] = password
            return real(password)

        monkeypatch.setattr(users, "hash_password", spy)
        result = runner.invoke(
            app, ["user", "passwd", "alice", "--stdin"], input="a-brand-new-password\r\n"
        )
        assert result.exit_code == 0, result.output
        assert recorded["password"] == "a-brand-new-password"
        PasswordHasher().verify(real(recorded["password"]), "a-brand-new-password")

    def test_list_shows_no_digest(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        row = User(
            id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            username="alice",
            password_hash="$argon2id$v=19$secret-digest",
            role="admin",
        )
        row.created_at = None
        row.last_login_at = None
        install_context(monkeypatch, FakeSession(scalars=[[row]]))
        result = runner.invoke(app, ["user", "list"])
        assert result.exit_code == 0, result.output
        assert "alice" in result.output
        assert "argon2" not in result.output
        assert "secret-digest" not in result.output

    def test_list_says_so_when_there_are_no_accounts(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession(scalars=[[]]))
        result = runner.invoke(app, ["user", "list"])
        assert result.exit_code == 0
        assert "no accounts yet" in result.output


# ---------------------------------------------------------------------------
# proxies
# ---------------------------------------------------------------------------


class TestProxies:
    def test_validate_accepts_the_usual_schemes(self) -> None:
        for url in ("http://h:1", "https://h:2", "socks5://h:3", "socks5h://user:pw@h:4"):
            assert proxies.validate_proxy_url(url) == url

    @pytest.mark.parametrize(
        "url", ["", "ftp://host:21", "http://host", "not a url", "http://:8080"]
    )
    def test_validate_rejects_the_rest(self, url: str) -> None:
        with pytest.raises(ValueError):
            proxies.validate_proxy_url(url)

    def test_add_rejects_a_bad_url_with_exit_two(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["proxy", "add", "ftp://host:21"])
        assert result.exit_code == 2

    def test_add_encrypts_and_masks(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, cipher: Cipher
    ) -> None:
        session = FakeSession()
        install_context(monkeypatch, session)
        result = runner.invoke(app, ["proxy", "add", PROXY_URL, "--label", "vendor-a"])
        assert result.exit_code == 0, result.output
        assert "hunter2" not in result.output
        assert len(session.added) == 1
        stored = session.added[0]
        assert stored.url_encrypted != PROXY_URL.encode()
        assert cipher.decrypt(stored.url_encrypted, aad=str(stored.id)) == PROXY_URL

    def test_list_masks_the_password(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, cipher: Cipher
    ) -> None:
        proxy_id = uuid.uuid4()
        row = Proxy(
            id=proxy_id,
            url_encrypted=cipher.encrypt(PROXY_URL, aad=str(proxy_id)),
            label="vendor-a",
            country="US",
            timezone="America/Los_Angeles",
            healthy=True,
        )
        row.last_check_at = None
        install_context(monkeypatch, FakeSession(scalars=[[row]]))
        result = runner.invoke(app, ["proxy", "list"])
        assert result.exit_code == 0, result.output
        assert "hunter2" not in result.output
        assert "vendor-a" in result.output
        assert "10.0.0.9" in result.output

    def test_import_file_parsing(self) -> None:
        text = "\n".join(
            [
                "# vendor list",
                "",
                "http://a:1 first",
                "socks5://user:pw@b:2",
                "ftp://c:3",
                "garbage",
            ]
        )
        entries, rejected = proxies.parse_import_file(text)
        assert entries == [("http://a:1", "first"), ("socks5://user:pw@b:2", None)]
        assert [number for number, _ in rejected] == [5, 6]

    @pytest.mark.parametrize(
        "line",
        [
            "ftp://user:hunter2@host:21",
            # No scheme at all: urlsplit reads "user" as the scheme, and in the
            # other paste order it reads the password as the scheme.
            "user:hunter2@10.0.0.9:3128",
            "hunter2:user@10.0.0.9:3128",
        ],
    )
    def test_import_never_reports_a_rejected_lines_contents(self, line: str) -> None:
        _entries, rejected = proxies.parse_import_file(line)
        assert rejected
        for _number, reason in rejected:
            assert "hunter2" not in reason
            assert "user" not in reason
            assert "10.0.0.9" not in reason

    def test_import_skips_duplicates(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, cipher: Cipher, tmp_path: Any
    ) -> None:
        proxy_id = uuid.uuid4()
        existing = Proxy(
            id=proxy_id,
            url_encrypted=cipher.encrypt(PROXY_URL, aad=str(proxy_id)),
            label="already-there",
        )
        listing = tmp_path / "proxies.txt"
        listing.write_text(f"{PROXY_URL}\nhttp://new:8080\n", encoding="utf-8")

        session = FakeSession(scalars=[[existing]])
        install_context(monkeypatch, session)
        result = runner.invoke(app, ["proxy", "import", str(listing)])
        assert result.exit_code == 0, result.output
        assert "hunter2" not in result.output
        assert len(session.added) == 1
        assert "skipped 1 duplicate" in result.output

    def test_import_needs_an_existing_file(self, runner: CliRunner, tmp_path: Any) -> None:
        result = runner.invoke(app, ["proxy", "import", str(tmp_path / "absent.txt")])
        assert result.exit_code == 2

    def test_test_reports_an_unknown_proxy(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession(scalars=[[]]))
        result = runner.invoke(app, ["proxy", "test", "abcd1234"])
        assert result.exit_code == 1
        assert "no proxy" in result.output


class TestProbes:
    """A probe reports; it never raises, and it never quotes a credential."""

    def test_a_socks_proxy_reports_instead_of_raising(self) -> None:
        """socks5h is an accepted scheme, and httpx needs an optional extra."""
        from dtk.ops import probes

        url = "socks5h://bob:hunter2@10.0.0.9:1080"
        result = asyncio.run(probes.probe_proxy(url, timeout=1.0))
        assert result.ok is False
        assert result.detail
        assert "hunter2" not in result.detail

    def test_an_unusable_proxy_url_reports_instead_of_raising(self) -> None:
        from dtk.ops import probes

        result = asyncio.run(probes.probe_proxy("http://bob:hunter2@h:notaport", timeout=1.0))
        assert result.ok is False
        assert "hunter2" not in (result.detail or "")

    def test_a_json_array_from_the_probe_url_is_a_failed_probe(self) -> None:
        """`.get` on a decoded list would be an AttributeError, not a result."""
        import httpx

        from dtk.ops import probes

        real_client = httpx.AsyncClient

        def array_client(**_kwargs: Any) -> httpx.AsyncClient:
            transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[1, 2, 3]))
            return real_client(transport=transport)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(httpx, "AsyncClient", array_client)
            result = asyncio.run(probes.probe_proxy("http://p:1", probe_url="https://x/y"))
        assert result.ok is False
        assert "no JSON object" in (result.detail or "")

    def test_a_transport_failure_does_not_echo_the_signed_query(self) -> None:
        """The failure message quotes the request URL; a signed one carries msToken."""
        from dtk.ops import pipeline, probes
        from dtk.transport.base import TransportFailure

        signed = (
            "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=1"
            "&msToken=SUPERSECRETMSTOKENVALUE&a_bogus=DDDDDDDDDDDD"
        )

        async def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise TransportFailure(
                f"GET {signed} failed: ConnectError",
                identity_id="i",
                url=signed,
                elapsed_ms=1,
            )

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(pipeline, "call_endpoint", boom)
            probe = asyncio.run(probes.probe_identity(object(), None, None, None))

        assert probe.ok is False
        assert probe.detail is not None
        assert "SUPERSECRETMSTOKENVALUE" not in probe.detail
        assert "aweme_id=1" in probe.detail


class TestSigningStack:
    def test_browser_rpc_is_behind_the_native_signers(self) -> None:
        """Configured browser-rpc must actually reach the registry as a fallback."""
        from dtk.ops.pipeline import signing_stack

        async def check() -> Any:
            async with signing_stack("http://browser-rpc:8000") as (_transport, signers):
                return signers._rpc

        assert type(asyncio.run(check())).__name__ == "RpcSigner"

    def test_no_browser_rpc_means_native_only(self) -> None:
        from dtk.ops.pipeline import signing_stack

        async def check() -> Any:
            async with signing_stack("") as (_transport, signers):
                return signers._rpc

        assert asyncio.run(check()) is None

    def test_building_a_registry_without_a_client_is_refused(self) -> None:
        """Silently dropping the RPC signer is what this used to do."""
        from dtk.ops.pipeline import build_registry

        with pytest.raises(ValueError, match="no HTTP client"):
            build_registry("http://browser-rpc:8000")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class TestConfig:
    def test_list_shows_keys_and_scopes(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession())
        result = runner.invoke(app, ["config", "list", "--scope", "sensitive"])
        assert result.exit_code == 0, result.output
        assert "security.url_allowlist" in result.output
        assert "cache.content_ttl" not in result.output

    def test_get_prints_json(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        install_context(monkeypatch, FakeSession())
        result = runner.invoke(app, ["config", "get", "cache.content_ttl"])
        assert result.exit_code == 0, result.output
        assert "cache.content_ttl" in result.output
        assert "1800" in result.output

    def test_set_coerces_and_reports(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dtk.services.settings_store as store

        recorded: dict[str, Any] = {}

        async def fake_set_value(key: str, value: Any, **_kwargs: Any) -> Any:
            recorded["key"], recorded["value"] = key, value
            return value

        monkeypatch.setattr(store, "set_value", fake_set_value)
        install_context(monkeypatch, FakeSession())
        result = runner.invoke(app, ["config", "set", "cache.content_ttl", "60"])
        assert result.exit_code == 0, result.output
        assert recorded == {"key": "cache.content_ttl", "value": 60}

    def test_sensitive_setting_asks_first(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession())
        result = runner.invoke(
            app, ["config", "set", "security.enable_task_webhook", "true"], input="n\n"
        )
        assert result.exit_code == 1
        assert "sensitive setting" in result.output
        assert "cancelled" in result.output

    def test_channel_credentials_are_redacted(self) -> None:
        value = [{"type": "telegram", "url": "https://api.telegram.org/bot123:ABCDEF/send"}]
        rendered = settings_cmd.render(settings_cmd.redact("notify.channels", value))
        assert "ABCDEF" not in rendered
        assert "api.telegram.org" in rendered

    def test_nested_tokens_are_redacted(self) -> None:
        redacted = settings_cmd.redact("notify.channels", {"token": "super-secret-token"})
        assert "super-secret-token" not in settings_cmd.render(redacted)


# ---------------------------------------------------------------------------
# identities
# ---------------------------------------------------------------------------


class TestIdentities:
    def _identity(self) -> Identity:
        row = Identity(
            id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
            platform=Platform.DOUYIN.value,
            cookies_encrypted=b"ciphertext",
            fingerprint={"browser_family": "chrome", "browser_major": 130},
            source="minted",
            state=IdentityState.ACTIVE.value,
            authenticated=False,
        )
        row.proxy_id = None
        row.consecutive_fails = 0
        row.cooldown_until = None
        row.last_used_at = None
        return row

    def test_list_shows_state_but_no_cookies(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession(scalars=[[self._identity()], []]))
        result = runner.invoke(app, ["identity", "list"])
        assert result.exit_code == 0, result.output
        assert "11111111" in result.output
        assert "active" in result.output
        assert "ciphertext" not in result.output

    def test_list_reports_an_empty_pool(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession(scalars=[[], []]))
        result = runner.invoke(app, ["identity", "list"])
        assert result.exit_code == 0
        assert "the pool is empty" in result.output

    def test_mint_without_browser_rpc_fails_cleanly(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession())
        result = runner.invoke(app, ["identity", "mint", "--platform", "douyin"])
        assert result.exit_code == 1
        assert "browser-rpc is not configured" in result.output

    def test_retire_needs_a_known_identity(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_context(monkeypatch, FakeSession(scalars=[[]]))
        result = runner.invoke(app, ["identity", "retire", "deadbeef"])
        assert result.exit_code == 1
        assert "no identity" in result.output

    def test_retire_refuses_an_already_retired_identity(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retiring twice would overwrite the original reason with the CLI's."""
        row = self._identity()
        row.state = IdentityState.RETIRED.value
        install_context(monkeypatch, FakeSession(scalars=[[row]]))
        result = runner.invoke(app, ["identity", "retire", "11111111"])
        assert result.exit_code == 1
        assert "already retired" in result.output

    def test_retire_rejects_an_ambiguous_prefix(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first, second = self._identity(), self._identity()
        second.id = uuid.UUID("11111111-9999-3333-4444-555555555555")
        install_context(monkeypatch, FakeSession(scalars=[[first, second]]))
        result = runner.invoke(app, ["identity", "retire", "1111"])
        assert result.exit_code == 1
        assert "start with" in result.output


# ---------------------------------------------------------------------------
# url to endpoint mapping
# ---------------------------------------------------------------------------


class TestTargets:
    def test_a_douyin_video_maps_to_content_detail(self) -> None:
        from dtk.ops.pipeline import target_for
        from dtk.urls import identify

        target = target_for(identify("https://www.douyin.com/video/7298145681699622182"))
        assert target.endpoint == "douyin.content_detail"
        assert target.params == {"content_id": "7298145681699622182"}

    def test_a_tiktok_user_maps_to_author_profile(self) -> None:
        from dtk.ops.pipeline import target_for
        from dtk.urls import identify

        target = target_for(identify("https://www.tiktok.com/@owlcitymusic"))
        assert target.endpoint == "tiktok.author_profile"
        assert target.params == {"unique_id": "owlcitymusic"}

    def test_canonical_params_survive_the_shared_registry(self) -> None:
        """The CLI hands the registry canonical names; it returns platform ones."""
        from dtk.ops.pipeline import target_for
        from dtk.urls import identify
        from dtk.worker.registry import resolve

        target = target_for(identify("https://www.douyin.com/video/7298145681699622182"))
        call = resolve(target.endpoint, target.params, Config.defaults())
        assert call.params["aweme_id"] == "7298145681699622182"
        assert call.cache_ttl == Config.defaults().get("cache.content_ttl")
        assert callable(call.parse)

    def test_an_unsupported_resource_is_rejected(self) -> None:
        from dtk.core.errors import UnsupportedContent
        from dtk.ops.pipeline import target_for
        from dtk.urls import identify

        kind = identify("https://live.douyin.com/123456789")
        with pytest.raises(UnsupportedContent):
            target_for(kind)


# ---------------------------------------------------------------------------
# the runtime seam
# ---------------------------------------------------------------------------


class TestRuntime:
    def test_domain_errors_become_exit_one(self) -> None:
        from dtk.core.errors import NotFound

        async def boom() -> None:
            raise NotFound("no such thing")

        with pytest.raises(typer.Exit) as raised:
            runtime.run(boom)
        assert raised.value.exit_code == output.EXIT_FAILURE

    def test_database_errors_are_scrubbed_before_printing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from sqlalchemy.exc import OperationalError

        async def boom() -> None:
            raise OperationalError(
                "connect to postgresql+asyncpg://dtk:s3cr3t@db:5432/dtk", {}, Exception("refused")
            )

        with pytest.raises(typer.Exit) as raised:
            runtime.run(boom)
        assert raised.value.exit_code == output.EXIT_FAILURE
        printed = capsys.readouterr()
        assert "s3cr3t" not in printed.out + printed.err

    def test_network_errors_become_exit_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        """httpx errors are not OSError; short-link expansion runs on httpx."""
        import httpx

        async def boom() -> None:
            raise httpx.ConnectError("All connection attempts failed")

        with pytest.raises(typer.Exit) as raised:
            runtime.run(boom)
        assert raised.value.exit_code == output.EXIT_FAILURE
        assert "ConnectError" in capsys.readouterr().err

    def test_a_missing_secret_key_explains_itself(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from dtk.core.crypto import SecretKeyMissing

        def refuse() -> BootstrapSettings:
            raise SecretKeyMissing("DTK_SECRET_KEY must be set")

        monkeypatch.setattr(runtime, "BootstrapSettings", refuse)
        with pytest.raises(typer.Exit) as raised:
            runtime.load_settings()
        assert raised.value.exit_code == output.EXIT_FAILURE
        assert "DTK_SECRET_KEY" in capsys.readouterr().err


class TestOutput:
    def test_pairs_scrub_free_text_values(self, capsys: pytest.CaptureFixture[str]) -> None:
        output.print_pairs([("detail", "GET https://x/y?msToken=SUPERSECRETMSTOKENVALUE failed")])
        assert "SUPERSECRETMSTOKENVALUE" not in capsys.readouterr().out

    def test_pairs_scrub_a_dsn_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        output.print_pairs([("dsn", "postgresql+asyncpg://dtk:s3cr3t@db:5432/dtk")])
        assert "s3cr3t" not in capsys.readouterr().out

    def test_square_brackets_survive_rendering(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Upstream text is data, not rich markup; the extra name must survive."""
        output.print_pairs([("detail", "install httpx[socks]")])
        assert "httpx[socks]" in capsys.readouterr().out.replace("\n", "").replace(" ", "")

    def test_a_stray_closing_tag_does_not_abort_the_command(self) -> None:
        """A MarkupError here would crash the command an operator runs mid-incident."""
        output.print_pairs([("detail", "unexpected [/] in the body")])
        output.error("unexpected [/] in the body")

    def test_a_never_probed_proxy_is_not_reported_healthy(
        self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, cipher: Cipher
    ) -> None:
        """The healthy column defaults to true; "last check: never" is the truth."""
        proxy_id = uuid.uuid4()
        row = Proxy(
            id=proxy_id,
            url_encrypted=cipher.encrypt(PROXY_URL, aad=str(proxy_id)),
            label="fresh",
            healthy=True,
        )
        row.last_check_at = None
        install_context(monkeypatch, FakeSession(scalars=[[row]]))
        result = runner.invoke(app, ["proxy", "list"])
        assert result.exit_code == 0, result.output
        assert "unchecked" in result.output
        assert "healthy" not in result.output


class TestSigningView:
    def test_screen_geometry_is_split(self) -> None:
        from dtk.ops.pipeline import signing_view
        from dtk.transport.base import Fingerprint

        view = signing_view(Fingerprint(user_agent="UA", platform="Win32", screen="1920x1080"))
        assert (view.screen_width, view.screen_height) == (1920, 1080)
        assert view.user_agent == "UA"

    def test_a_missing_screen_stays_absent(self) -> None:
        from dtk.ops.pipeline import signing_view
        from dtk.transport.base import Fingerprint

        view = signing_view(Fingerprint(user_agent="UA"))
        assert view.screen_width is None
        assert view.screen_height is None


# ---------------------------------------------------------------------------
# backup listing needs no database
# ---------------------------------------------------------------------------


def test_backup_list_on_an_empty_directory(runner: CliRunner, tmp_path: Any) -> None:
    result = runner.invoke(app, ["backup", "list", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no archives" in result.output


def test_backup_restore_rejects_a_file_that_is_not_an_archive(
    runner: CliRunner, tmp_path: Any
) -> None:
    """A wrong path is a usage error; a wrong file is a runtime failure."""
    junk = tmp_path / "notes.tar.gz"
    junk.write_text("this is not a tar archive", encoding="utf-8")
    result = runner.invoke(app, ["backup", "restore", str(junk)])
    assert result.exit_code == 1
    assert "cannot read" in result.output


def test_migrate_show_needs_no_database(runner: CliRunner) -> None:
    result = runner.invoke(app, ["migrate", "--show"])
    assert result.exit_code == 0, result.output
    assert "revision on disk" in result.output


def test_identities_module_exposes_its_app() -> None:
    assert identities.app is not None
