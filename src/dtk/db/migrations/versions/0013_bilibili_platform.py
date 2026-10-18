"""Let the identity pool hold Bilibili identities.

The platform enum grew a member, and nothing in Python would have said that
this CHECK had not: ``PLATFORM_VALUES`` follows the enum, the constraint is a
string, and the first Bilibili identity minted would have been refused by
Postgres. Widened here, narrowed again on the way down - which fails, as it
should, while any Bilibili identity still exists.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "ck_identities_platform"


def upgrade() -> None:
    op.drop_constraint(NAME, "identities", type_="check")
    op.create_check_constraint(NAME, "identities", "platform IN ('douyin','tiktok','bilibili')")


def downgrade() -> None:
    op.drop_constraint(NAME, "identities", type_="check")
    op.create_check_constraint(NAME, "identities", "platform IN ('douyin','tiktok')")
