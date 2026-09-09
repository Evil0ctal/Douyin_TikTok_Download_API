"""At most one download of a post may be in flight at a time.

Two downloads of one post write into one directory - it is keyed by platform,
author and post - and the sidecar renames ``name.part`` to ``name`` as each file
finishes. The loser of that race renames a file the winner has already moved and
comes back ``partial`` with a file missing. Measured on a live instance on
2026-09-09: three requests for one post finished within 10ms of each other and
one lost its cover to exactly that.

The API checks for a live download before starting one, and that check is not
enough on its own: three simultaneous requests all read "none in flight" before
any of them writes. A partial unique index is the only place the guarantee can
actually live, because it is the only place that sees all three.

Partial on purpose. Settled rows are not covered, so a post can be downloaded
again as often as anyone likes - re-downloading is how you refresh a post, and
"the first one failed" is the usual reason for a second request. What it forbids
is two at once.

Pre-existing violations are settled rather than deleted: the oldest live row per
post keeps going and the others are marked cancelled, which is what they
effectively were.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE = "('queued', 'running')"


def upgrade() -> None:
    # Collapse what is already there, or the index cannot be created. The
    # oldest is kept because it is the one the worker is most likely to have
    # started on already.
    op.execute(
        sa.text(f"""
        UPDATE media_downloads
           SET state = 'cancelled',
               error = 'superseded by another download of the same post',
               finished_at = now()
         WHERE state IN {LIVE}
           AND id NOT IN (
               SELECT DISTINCT ON (platform, content_id) id
                 FROM media_downloads
                WHERE state IN {LIVE}
                ORDER BY platform, content_id, created_at ASC
           )
        """)
    )

    op.create_index(
        "ux_media_downloads_in_flight",
        "media_downloads",
        ["platform", "content_id"],
        unique=True,
        postgresql_where=sa.text(f"state IN {LIVE}"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ux_media_downloads_in_flight", table_name="media_downloads", if_exists=True)
