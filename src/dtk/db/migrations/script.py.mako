"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

TimescaleDB reminder: create_hypertable and the add_*_policy functions run
inside the migration transaction, but a continuous aggregate does not. Wrap
CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous) like this::

    with op.get_context().autocommit_block():
        op.execute("CREATE MATERIALIZED VIEW ...")

English only in this file: comments, identifiers and messages.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
