"""Let an identity be kept out of rotation, wholly or per endpoint.

NULL for every existing row, which is what every identity did before: serve
any endpoint the scheduler hands it. See ``Identity.rotation_endpoints``.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "identities",
        sa.Column("rotation_endpoints", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("identities", "rotation_endpoints")
