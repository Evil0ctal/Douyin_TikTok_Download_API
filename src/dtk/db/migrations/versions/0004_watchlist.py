"""Watchlist: what this instance re-collects on a timer.

`content_snapshots` has existed since 0001 and, until now, held whatever a
human happened to parse at whatever moment they happened to do it. That is not
a time series - it is a scatter of unrelated observations, and the growth curves
built on it have gaps wherever nobody was looking. This table is what makes the
hypertable mean what doc 05 says it means.

The unique index is the design: one entry per (platform, kind, target). Adding
the same author twice is a mistake, not a way to collect twice as often, and
refusing it in the schema means no caller has to remember.

Written to be safely repeatable, the same as every migration before it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("pages", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_result_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("runs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        if_not_exists=True,
    )

    op.create_index(
        "ix_watchlist_target",
        "watchlist",
        ["platform", "kind", "target_id"],
        unique=True,
        if_not_exists=True,
    )
    # The due query runs every minute for the life of the process, so it gets
    # an index rather than a sequential scan that grows with the watchlist.
    op.create_index(
        "ix_watchlist_due",
        "watchlist",
        ["enabled", sa.text("next_run_at ASC")],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("watchlist", if_exists=True)
