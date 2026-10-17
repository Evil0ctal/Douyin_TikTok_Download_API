"""Keep the reason a proxy's last probe failed.

NULL for every existing row: no failure has been recorded yet, and the next
probe fills it in or leaves it empty. See ``Proxy.last_error``.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proxies", sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("proxies", "last_error")
