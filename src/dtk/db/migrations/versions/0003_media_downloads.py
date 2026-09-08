"""Media downloads: the index of what this instance has on its own disk.

The files live on a volume; this table is what makes them findable, and what
survives them. Eviction under the size ceiling stamps `files_removed_at` and
leaves everything else, so "I had that video and it was cleaned up" stays a
different fact from "I never fetched it" - which matters, because only the
first of the two can be undone by asking again.

No foreign key to `archived_contents`. A download is evidence that this
instance fetched something, and the archive row it came from may be re-parsed,
re-keyed or removed by a retention policy without that evidence becoming false.

Written to be safely repeatable, the same as 0001 and 0002.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_downloads",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column("author_uid", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("directory", sa.Text(), nullable=False),
        sa.Column("bytes_total", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("file_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("files_removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("task_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["requested_by"], ["api_keys.id"], ondelete="SET NULL"),
        if_not_exists=True,
    )

    op.create_index(
        "ix_media_downloads_content",
        "media_downloads",
        ["platform", "content_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_media_downloads_created",
        "media_downloads",
        [sa.text("created_at DESC")],
        if_not_exists=True,
    )
    op.create_index("ix_media_downloads_state", "media_downloads", ["state"], if_not_exists=True)
    # The eviction sweep's index: still on disk, not pinned, oldest first.
    # Partial, so everything already evicted stays out of the way of the one
    # query that runs on every maintenance tick.
    op.create_index(
        "ix_media_downloads_evictable",
        "media_downloads",
        ["pinned", sa.text("finished_at ASC")],
        postgresql_where=sa.text("files_removed_at IS NULL AND bytes_total > 0"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("media_downloads", if_exists=True)
