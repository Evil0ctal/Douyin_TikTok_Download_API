"""Availability tracking: when a post's existence was last verified.

`archived_contents.availability` has been set from real observations since
0002, and nothing ever re-checked it. A post deleted the day after it was
archived stayed `live` forever, because a deleted post answers BUSINESS_ERROR -
the fetch raises, the archive write never happens, and the row keeps saying
what it said.

`last_seen_at` cannot stand in for this. A post that has been deleted is not
seen at all, and the check that discovered that still happened; conflating the
two would leave the sweep unable to order its own work.

The index is partial on purpose: rows already known to be gone are exactly the
ones a timer-driven query never needs to look at again.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "archived_contents",
        sa.Column("availability_checked_at", sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.create_index(
        "ix_archived_contents_recheck",
        "archived_contents",
        [sa.text("availability_checked_at ASC NULLS FIRST")],
        postgresql_where=sa.text("availability = 'live'"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_archived_contents_recheck", table_name="archived_contents", if_exists=True)
    op.drop_column("archived_contents", "availability_checked_at", if_exists=True)
