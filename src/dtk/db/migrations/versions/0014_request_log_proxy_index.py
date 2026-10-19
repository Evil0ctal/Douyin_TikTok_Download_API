"""Index the request log by proxy, for the Logs page's proxy filter.

``GET /admin/logs/requests`` learned ``proxy_id`` so an operator can see which
egress a platform is refusing - a Bilibili 412 is a verdict on an address, and
every identity behind one proxy shares it. Without this index that filter
falls off the ``ts`` ordering the way the outcome filter once did, and scans
every chunk in its window with no early stop.

``(proxy_id, ts DESC)``, the shape of the endpoint and identity indexes beside
it. Created on the hypertable, so TimescaleDB builds it on every chunk.

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

NAME = "ix_request_log_proxy_ts"


def upgrade() -> None:
    op.create_index(
        NAME,
        "request_log",
        ["proxy_id", sa.text("ts DESC")],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(NAME, table_name="request_log", if_exists=True)
