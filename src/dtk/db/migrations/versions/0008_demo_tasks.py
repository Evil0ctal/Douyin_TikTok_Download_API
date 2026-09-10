"""Mark tasks started by the public demo account.

A demo instance is a machine strangers are invited to press buttons on, and the
task table is what every one of those presses writes to. The row itself cannot
be skipped - a task id has to refer to something for `202` plus polling to mean
anything - so it is marked instead, and two things read the mark:

* the worker does not archive what a demo task parsed, so a stranger's link
  does not become a row in the operator's library;
* the maintenance sweep deletes these rows on ``demo.task_retention_minutes``,
  a window of minutes, rather than the 90 days a real caller's task gets.

The column defaults to false and the index over it is partial, so an instance
that never turns the demo on carries a boolean it never reads and an index with
no entries in it.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `demo` is a new value for users.role, and the role column carries a CHECK
    # constraint listing what is allowed. Without this the account cannot be
    # inserted at all - which is how this was found: the unit tests build no
    # database and passed, and the first integration test to provision a demo
    # account failed on the constraint.
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role", "users", "role IN ('admin','operator','viewer','demo')"
    )

    op.add_column(
        "tasks",
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # Partial: the sweep only ever looks for demo rows, and on an instance with
    # the demo off this index stays empty and costs nothing to maintain.
    op.create_index(
        "ix_tasks_demo_created_at",
        "tasks",
        ["created_at"],
        postgresql_where=sa.text("is_demo"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_demo_created_at", table_name="tasks")
    op.drop_column("tasks", "is_demo")

    # Demote any demo account before narrowing the constraint again, or the
    # downgrade fails on rows the upgrade made legal.
    op.execute("DELETE FROM users WHERE role = 'demo'")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.create_check_constraint("ck_users_role", "users", "role IN ('admin','operator','viewer')")
