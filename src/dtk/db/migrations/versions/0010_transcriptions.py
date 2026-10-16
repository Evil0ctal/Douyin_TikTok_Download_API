"""Add durable transcript results.

One migration for the whole table. The first draft added two console indexes in
a second revision; neither had been released, so they are folded in here
rather than leaving every install to run a revision that only adds indexes.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("download_id", sa.Uuid(), nullable=True),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="queued", nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "normalization_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "normalization_applied",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("normalizer", sa.Text(), nullable=True),
        sa.Column("normalization_failure_policy", sa.Text(), nullable=True),
        sa.Column("normalization_error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.Text(), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued','running','done','failed','cancelled')",
            name="ck_transcriptions_state",
        ),
        sa.CheckConstraint(
            "normalization_failure_policy IS NULL OR "
            "normalization_failure_policy IN ('fallback','fail')",
            name="ck_transcriptions_normalization_failure_policy",
        ),
        sa.ForeignKeyConstraint(["download_id"], ["media_downloads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["api_keys.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transcriptions_download", "transcriptions", ["download_id", sa.text("created_at DESC")]
    )
    op.create_index("ix_transcriptions_task", "transcriptions", ["task_id"])
    op.create_index("ix_transcriptions_created", "transcriptions", [sa.text("created_at DESC")])
    op.create_index("ix_transcriptions_platform_state", "transcriptions", ["platform", "state"])
    op.create_index(
        "ux_transcriptions_in_flight",
        "transcriptions",
        [
            "download_id",
            "source_sha256",
            "model",
            "normalization_requested",
            sa.text("COALESCE(normalizer, '')"),
            sa.text("COALESCE(normalization_failure_policy, '')"),
        ],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("transcriptions")
