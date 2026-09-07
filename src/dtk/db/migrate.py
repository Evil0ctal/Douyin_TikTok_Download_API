"""Run the migration scripts from Python.

The ``migrate`` container and ``dtk migrate`` come through here instead of
shelling out to the Alembic CLI, so the scripts also work from an installed
wheel where alembic.ini is not on disk. The revision directory is located
relative to this module, never relative to the working directory.

These functions are synchronous and start their own event loop inside env.py.
Call them from a process entry point, not from inside a running loop.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from dtk.core.logging import get_logger

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

log = get_logger(__name__)


def alembic_config(url: str | None = None) -> Config:
    """Build an Alembic config pointing at this package's revisions.

    A URL passed here wins over the environment, so a caller that knows which
    database it means cannot be redirected by a stray DTK_DATABASE_URL.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("path_separator", "os")
    # Alembic stores options in a ConfigParser, which interpolates "%". A
    # password containing one would otherwise blow up here rather than at
    # connection time.
    config.set_main_option("sqlalchemy.url", (url or "").replace("%", "%%"))
    # Logging is configured once by the application; do not let env.py reset it.
    config.attributes["configure_logging"] = False
    return config


def head_revision() -> str | None:
    """The newest revision on disk. No database connection is made."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Apply migrations up to ``revision`` (default: everything)."""
    log.info("db.migrate.start", revision=revision)
    command.upgrade(alembic_config(url), revision)
    log.info("db.migrate.done", revision=revision)


def downgrade(url: str | None = None, revision: str = "-1") -> None:
    """Roll back. ``-1`` is one step; ``base`` removes the whole schema."""
    log.info("db.migrate.downgrade", revision=revision)
    command.downgrade(alembic_config(url), revision)


__all__ = ["MIGRATIONS_DIR", "alembic_config", "downgrade", "head_revision", "upgrade"]
