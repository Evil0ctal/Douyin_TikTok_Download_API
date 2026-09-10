"""Store the demo password and demo API key so they can be shown.

Every other credential in this schema is one-way on purpose: a password is an
argon2 digest, an API key is a sha256 digest shown once at creation and never
again. That rule is right for an operator's credentials and wrong for the demo's,
because the demo pair is *published* - printed on the login page so a visitor
can click straight through, and printed on the API keys page so they can try the
scraping endpoints. A credential nobody can read cannot be published.

So two nullable columns, encrypted with the instance key and the row id as AAD,
exactly like ``proxies.url_encrypted`` and ``identities.cookies_encrypted``. A
database dump on its own still does not carry them.

They are NULL for every account and every key but the demo pair, and that is the
security property worth naming: the console cannot reveal an operator's key
because the plaintext does not exist, not because a check says no. Authentication
is unchanged - ``password_hash`` still verifies the login and ``key_hash`` still
resolves the key - so these columns are display only.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("demo_password_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column("api_keys", sa.Column("demo_secret_encrypted", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "demo_secret_encrypted")
    op.drop_column("users", "demo_password_encrypted")
