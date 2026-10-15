"""Add indexes used by the transcription console.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_transcriptions_created", "transcriptions", [sa.text("created_at DESC")])
    op.create_index("ix_transcriptions_platform_state", "transcriptions", ["platform", "state"])


def downgrade() -> None:
    op.drop_index("ix_transcriptions_platform_state", table_name="transcriptions")
    op.drop_index("ix_transcriptions_created", table_name="transcriptions")
