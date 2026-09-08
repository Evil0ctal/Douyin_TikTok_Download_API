"""Content archive: current-state tables beside the metrics hypertable.

`content_snapshots` is a time series of five integers per observation, and doc
05 explains why it is deliberately that small. It cannot double as an archive:
it is append-only, it carries no primary key to upsert against, and giving it
one would mean a unique index on a hypertable that has to include the
partitioning column - which `dtk.db.models` refuses on the hottest write path in
the system.

So the archive is two plain relational tables holding current state, joined to
that history on (platform, content_id). Dedup is an ordinary upsert.

Every classification column here is derived on write from what the parser
already returned, and is recomputable from `raw` if the rule changes. No model,
no extra request: docs/design/README.md records AI content analysis as a
non-goal, and doc 18 argues why "auto-classify" must not become a way around it.

Written to be safely repeatable, the same as 0001: IF NOT EXISTS everywhere, so
a half-applied run can be re-run rather than unpicked.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "archived_authors",
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("uid", sa.Text(), nullable=False),
        sa.Column("unique_id", sa.Text(), nullable=True),
        sa.Column("nickname", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("web_url", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("follower_count", sa.BigInteger(), nullable=True),
        sa.Column("following_count", sa.BigInteger(), nullable=True),
        sa.Column("content_count", sa.BigInteger(), nullable=True),
        sa.Column("total_digg", sa.BigInteger(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("platform", "uid"),
        if_not_exists=True,
    )

    op.create_table(
        "archived_contents",
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("web_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("platform_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("author_uid", sa.Text(), nullable=False),
        sa.Column("author_nickname", sa.Text(), nullable=True),
        sa.Column("music_id", sa.Text(), nullable=True),
        sa.Column("music_title", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("media", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("orientation", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column(
            "duration_bucket", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column(
            "resolution_class", sa.Text(), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("script", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("availability", sa.Text(), server_default=sa.text("'live'"), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("platform", "content_id"),
        if_not_exists=True,
    )

    op.create_index(
        "ix_archived_contents_author",
        "archived_contents",
        ["platform", "author_uid"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_archived_contents_last_seen",
        "archived_contents",
        [sa.text("last_seen_at DESC")],
        if_not_exists=True,
    )
    op.create_index(
        "ix_archived_contents_created",
        "archived_contents",
        [sa.text("platform_created_at DESC")],
        if_not_exists=True,
    )
    # GIN, so "every post tagged X" does not become a sequential scan once the
    # archive is the size an archive is meant to reach.
    op.create_index(
        "ix_archived_contents_tags",
        "archived_contents",
        ["tags"],
        postgresql_using="gin",
        if_not_exists=True,
    )
    op.create_index(
        "ix_archived_contents_music",
        "archived_contents",
        ["platform", "music_id"],
        if_not_exists=True,
    )

    # Trigram indexes for search. Postgres' built-in text search parsers do not
    # segment Chinese - to_tsvector over a Douyin description yields a handful of
    # giant lexemes and a search for a two-character word matches nothing, in
    # silence, on the primary platform. Trigram substring matching is the honest
    # answer available without adding a component the README rules out.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_archived_contents_title_trgm",
        "archived_contents",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
        if_not_exists=True,
    )
    op.create_index(
        "ix_archived_contents_description_trgm",
        "archived_contents",
        ["description"],
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
        if_not_exists=True,
    )
    op.create_index(
        "ix_archived_authors_nickname_trgm",
        "archived_authors",
        ["nickname"],
        postgresql_using="gin",
        postgresql_ops={"nickname": "gin_trgm_ops"},
        if_not_exists=True,
    )


def downgrade() -> None:
    # The extension is left in place: other things may use it, and dropping a
    # shared extension on the way down is how a downgrade breaks a neighbour.
    op.drop_table("archived_contents", if_exists=True)
    op.drop_table("archived_authors", if_exists=True)
