"""The console's "create a backup" job.

Two things are worth more than the row counts. The archive has to land where a
reader looks for it, because one the console cannot list is not a backup. And
the result travels into a database and then into a browser, so it must carry the
file's name and nothing that describes the host or the master key - the archive
is allowed to hold ciphertext, the task result is not allowed to hold anything.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from importlib import util
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Select

from dtk.core.config import Config
from dtk.core.crypto import Cipher
from dtk.core.errors import Internal
from dtk.ops import backup as ops_backup
from dtk.worker import ops
from dtk.worker.ops import OperationDeps, OperationRunner
from dtk.worker.ops import backup as backup_op

SECRET = "a" * 48
IDENTITY_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
CREATED = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
COOKIES = "sessionid=super-secret-value; ttwid=another"

#: The other five maintenance jobs. dtk.worker.ops._handlers imports all six in
#: one statement, so the runner cannot be exercised until every one exists.
SIBLING_JOBS = ("diagnose", "identity_mint", "identity_test", "notify_test", "proxy_test")


def sample_rows() -> dict[str, list[dict[str, Any]]]:
    cipher = Cipher(SECRET)
    return {
        "users": [
            {
                "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
                "username": "admin",
                "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$deadbeef",
                "role": "admin",
                "created_at": CREATED,
                "last_login_at": None,
            }
        ],
        "identities": [
            {
                "id": IDENTITY_ID,
                "platform": "douyin",
                "cookies_encrypted": cipher.encrypt(COOKIES, aad=str(IDENTITY_ID)),
                "fingerprint": {"browser_family": "chrome"},
                "proxy_id": None,
                "authenticated": True,
                "source": "imported",
                "state": "active",
                "cooldown_until": None,
                "consecutive_fails": 0,
                "minted_at": CREATED,
                "last_used_at": None,
                "retired_at": None,
                "retire_reason": None,
            }
        ],
    }


class _AsyncRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)

    def __aiter__(self) -> _AsyncRows:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)


class _Stream:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _AsyncRows:
        return _AsyncRows(self._rows)


class FakeSession:
    """Serves the rows an export selects, and records the runner's commit."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._rows = rows or {}
        self.commits = 0

    async def stream(self, statement: Select[Any]) -> _Stream:
        table = statement.get_final_froms()[0].name  # type: ignore[attr-defined]
        return _Stream(self._rows.get(table, []))

    async def commit(self) -> None:
        self.commits += 1


def fake_deps() -> OperationDeps:
    """Deps a backup does not use, spelled out so the test says so.

    Nothing but the session reaches the export: no pool, no transport, no
    signers. Passing placeholders is the assertion.
    """
    return OperationDeps(
        config=Config.defaults,
        cipher=cast(Any, None),
        secret_key=SECRET,
        pool=cast(Any, None),
        transport=cast(Any, None),
        signers=cast(Any, None),
    )


@pytest.fixture(autouse=True)
def backup_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the job away from the repository's own ./backups."""
    monkeypatch.setenv("DTK_SECRET_KEY", SECRET)
    monkeypatch.setenv("DTK_BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path / "backups"


async def run_job(rows: dict[str, list[dict[str, Any]]] | None = None, **params: Any) -> Any:
    return await backup_op.run(fake_deps(), cast(Any, FakeSession(rows)), params)


# --------------------------------------------------------------------------
# where the archive goes
# --------------------------------------------------------------------------


async def test_archive_lands_where_the_listing_looks(backup_directory: Path) -> None:
    result = await run_job(sample_rows(), include_identities=False)

    listed = ops_backup.list_backups(backup_directory)
    assert [info.path.name for info in listed] == [result["data"]["path"]]
    assert listed[0].manifest is not None


async def test_result_reports_the_size_and_rows_that_were_written(
    backup_directory: Path,
) -> None:
    result = await run_job(sample_rows())

    written = backup_directory / result["data"]["path"]
    assert result["data"]["size_bytes"] == written.stat().st_size
    # users only: identities were not asked for.
    assert result["data"]["rows"] == 1


async def test_a_directory_that_cannot_be_written_is_a_dtk_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("DTK_BACKUP_DIR", str(blocker / "backups"))

    # A bare OSError would reach the console as an untranslatable crash.
    with pytest.raises(Internal):
        await run_job(sample_rows())


# --------------------------------------------------------------------------
# the console contract
# --------------------------------------------------------------------------


async def test_result_matches_the_create_result_interface() -> None:
    result = await run_job(sample_rows())

    # web/src/pages/Backup.tsx: { task_id?, path?, size_bytes?, rows?, note? }.
    # task_id is added by the API, and note is typed but never rendered.
    assert set(result["data"]) == {"path", "size_bytes", "rows"}
    assert isinstance(result["data"]["path"], str)
    assert isinstance(result["data"]["size_bytes"], int)
    assert isinstance(result["data"]["rows"], int)


async def test_result_carries_the_meta_the_worker_logs() -> None:
    result = await run_job(sample_rows())

    # The {"data": ..., "meta": ...} half of the two shapes operations.unwrap
    # accepts, so the console reads the data and worker.task.done has its
    # duration.
    assert isinstance(result["meta"], dict)
    assert result["meta"]["endpoint"] == "backup"
    assert isinstance(result["meta"]["duration_ms"], int)


# --------------------------------------------------------------------------
# what the result may say
# --------------------------------------------------------------------------


async def test_result_names_the_file_and_not_the_filesystem(backup_directory: Path) -> None:
    result = await run_job(sample_rows(), include_identities=True)

    name = result["data"]["path"]
    assert name == Path(name).name
    assert "/" not in name and "\\" not in name
    assert str(backup_directory) not in json.dumps(result)


async def test_result_never_carries_the_key_or_its_check(backup_directory: Path) -> None:
    result = await run_job(sample_rows(), include_identities=True)
    serialized = json.dumps(result)

    assert SECRET not in serialized
    # key_check proves two DTK_SECRET_KEYs match; publishing it hands out an
    # oracle for guesses at the key it fingerprints.
    assert ops_backup.key_check(SECRET) not in serialized
    assert COOKIES not in serialized


async def test_the_archive_still_holds_the_encrypted_identities(
    backup_directory: Path,
) -> None:
    result = await run_job(sample_rows(), include_identities=True)
    archive = backup_directory / result["data"]["path"]

    identities = ops_backup.read_table(archive, "identities")
    assert len(identities) == 1
    assert COOKIES not in json.dumps(identities)
    manifest = ops_backup.read_manifest(archive)
    assert manifest.include_identities is True
    assert result["data"]["rows"] == manifest.total_rows


async def test_identities_stay_out_unless_asked_for(backup_directory: Path) -> None:
    result = await run_job(sample_rows())
    archive = backup_directory / result["data"]["path"]

    assert ops_backup.read_member(archive, "identities") is None
    assert ops_backup.read_manifest(archive).include_identities is False


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not all(util.find_spec(f"dtk.worker.ops.{name}") for name in SIBLING_JOBS),
    reason="the dispatch table imports every job at once; the others land beside this one",
)
async def test_the_runner_dispatches_backup_and_commits(backup_directory: Path) -> None:
    session = FakeSession(sample_rows())

    class _Scope:
        async def __aenter__(self) -> FakeSession:
            return session

        async def __aexit__(self, *exc: object) -> bool:
            return False

    assert ops.handles("backup")
    runner = OperationRunner(fake_deps(), session_factory=_Scope)
    result = await runner.run("backup", {"include_identities": False})

    assert (backup_directory / result["data"]["path"]).is_file()
    assert session.commits == 1


def test_the_shared_default_directory_is_not_owned_by_the_cli() -> None:
    """One home for the constant, and one env var both processes read."""
    from dtk.cli import backup as cli_backup

    assert cli_backup.DEFAULT_BACKUP_DIR is ops_backup.DEFAULT_BACKUP_DIR
    # Relative, so a checkout writes into ./backups and a bind mount there puts
    # the archives on the host.
    assert not ops_backup.DEFAULT_BACKUP_DIR.is_absolute()
    assert "dtk.cli" not in Path(backup_op.__file__).read_text(encoding="utf-8")


def test_the_directory_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container image is read-only, so the archive has to go elsewhere.

    DEFAULT_BACKUP_DIR is a relative path, which is right for a checkout and
    wrong inside the image: the process cannot write its own filesystem, and the
    backup failed with a permission error the first time anyone pressed the
    button. The deployment therefore points DTK_BACKUP_DIR at a mounted volume,
    and the API must resolve the same one - it is what lists and serves them.
    """
    monkeypatch.setenv("DTK_BACKUP_DIR", "/var/lib/dtk/backups")
    assert ops_backup.default_backup_dir() == Path("/var/lib/dtk/backups")

    monkeypatch.delenv("DTK_BACKUP_DIR", raising=False)
    assert ops_backup.default_backup_dir() == ops_backup.DEFAULT_BACKUP_DIR


async def test_the_archive_is_stamped_with_the_key_it_was_encrypted_under(
    backup_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key comes from the worker, never from the environment.

    An earlier version read BootstrapSettings() at task time. That is a
    different value than the one the worker built its Cipher from whenever the
    settings were constructed programmatically or the environment moved under a
    long-lived process - so the archive held ciphertext under one key and a
    manifest fingerprinting another. The task reported success either way; the
    mismatch only surfaced at restore, which is the one moment nobody wants a
    surprise. Assert the two agree at the source.
    """
    worker_key = "w" * 48
    monkeypatch.setenv("DTK_SECRET_KEY", "e" * 48)
    deps = OperationDeps(
        config=Config.defaults,
        cipher=cast(Any, None),
        secret_key=worker_key,
        pool=cast(Any, None),
        transport=cast(Any, None),
        signers=cast(Any, None),
    )

    result = await backup_op.run(deps, cast(Any, FakeSession()), {"include_identities": False})

    manifest = ops_backup.read_manifest(backup_directory / str(result["data"]["path"]))
    assert manifest.key_check == ops_backup.key_check(worker_key)
    assert manifest.key_check != ops_backup.key_check("e" * 48), (
        "the manifest fingerprints the environment's key, not the one the worker encrypts with"
    )
