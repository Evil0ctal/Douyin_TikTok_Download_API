"""A download can be one part of a video uploaded in several.

Bilibili lets an upload carry several parts, and until now a download of one
stored the first part's file under the post's name, with nothing to ask for
another. ``part`` says which; NULL is the first part, which every row before
this migration is, so nothing existing is rewritten.

The in-flight index was one live download per post, because two downloads of
a post write into one directory. A part writes into its own directory, so the
index becomes one live download per post *and part* - with NULL and 1 both
meaning the first, or the two spellings could run side by side into the same
directory.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ux_media_downloads_in_flight"
LIVE = sa.text("state IN ('queued', 'running')")


def upgrade() -> None:
    op.add_column("media_downloads", sa.Column("part", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_media_downloads_part", "media_downloads", "part IS NULL OR part > 1"
    )
    op.drop_index(INDEX, table_name="media_downloads")
    op.create_index(
        INDEX,
        "media_downloads",
        ["platform", "content_id", sa.text("COALESCE(part, 1)")],
        unique=True,
        postgresql_where=LIVE,
    )


def downgrade() -> None:
    # Fails while two parts of one post are in flight, as it should: the
    # narrower index cannot hold them.
    op.drop_index(INDEX, table_name="media_downloads")
    op.create_index(
        INDEX,
        "media_downloads",
        ["platform", "content_id"],
        unique=True,
        postgresql_where=LIVE,
    )
    op.drop_constraint("ck_media_downloads_part", "media_downloads", type_="check")
    op.drop_column("media_downloads", "part")
