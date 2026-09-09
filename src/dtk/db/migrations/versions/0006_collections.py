"""Collections: sets of archived posts made by hand.

Every other grouping the library offers is derived - by author, by platform, by
when the post was collected. This is the one that is not, which is exactly why
it needs a table: "things I am keeping" cannot be recomputed from the post.

Membership is a join table rather than an array on ``archived_contents``
because both directions are asked for: everything in a collection, and every
collection a post is in. The composite foreign key cascades, so removing a post
from the archive removes it from every collection without leaving a row
pointing at nothing.

The name is unique case-insensitively. Two collections called "Keep" and "keep"
are a mistake every time, and refusing it in the schema means no caller has to
remember to check.

Written to be safely repeatable, the same as every migration before it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_collections_name",
        "collections",
        [sa.text("lower(name)")],
        unique=True,
        if_not_exists=True,
    )

    op.create_table(
        "collection_items",
        sa.Column("collection_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("collection_id", "platform", "content_id"),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        # Composite, matching the archive's own primary key. Without it a
        # deleted post would leave membership rows behind, and the collection
        # would keep counting something that is gone.
        sa.ForeignKeyConstraint(
            ["platform", "content_id"],
            ["archived_contents.platform", "archived_contents.content_id"],
            ondelete="CASCADE",
        ),
        if_not_exists=True,
    )
    # The other direction: which collections is this post in. Asked once per
    # card on the library wall, so it is not a sequential scan.
    op.create_index(
        "ix_collection_items_content",
        "collection_items",
        ["platform", "content_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("collection_items", if_exists=True)
    op.drop_table("collections", if_exists=True)
