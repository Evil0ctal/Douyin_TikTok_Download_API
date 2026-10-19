"""Give an identity a note, and remember the account its session named.

An operator who imported three logins could tell them apart only by UUID,
which says nothing about whose account each one is. ``note`` is theirs to
write - the account it signed in as, where the cookie came from - and is
bounded so a paste of something else cannot land in it.

``account_id`` is what the platform said the cookies belong to the last time
the session was checked, and ``account_seen_at`` when. A note can be wrong or
stale; the platform's own answer is what tells two notes apart that say the
same name, and what shows one login imported twice.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Mirrors dtk.db.models.IDENTITY_NOTE_MAX.
NOTE_MAX = 200


def upgrade() -> None:
    op.add_column("identities", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("identities", sa.Column("account_id", sa.Text(), nullable=True))
    op.add_column(
        "identities", sa.Column("account_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_identities_note_length",
        "identities",
        f"note IS NULL OR char_length(note) BETWEEN 1 AND {NOTE_MAX}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_identities_note_length", "identities", type_="check")
    op.drop_column("identities", "account_seen_at")
    op.drop_column("identities", "account_id")
    op.drop_column("identities", "note")
