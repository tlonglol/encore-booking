"""waitlist per date

Revision ID: c91f4a7b25d0
Revises: a7b4e81c90df
Create Date: 2026-10-02

A waitlist signup now names the date it is waiting for. The old table held an email and nothing else,
which told the owner someone wanted a party but never which weekend, so there was nothing to act on.

Existing rows cannot be carried forward: there is no date to infer for them and inventing one would be
worse than losing them. They are deleted, which is also what makes slot_id NOT NULL honest.
"""

import sqlalchemy as sa
from alembic import op

revision = "c91f4a7b25d0"
down_revision = "a7b4e81c90df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Blind signups carry no date and no way to recover one.
    op.execute("DELETE FROM waitlist")

    op.add_column("waitlist", sa.Column("slot_id", sa.BigInteger(), nullable=False))
    op.add_column("waitlist", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column(
        "waitlist",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "waitlist_slot_id_fkey", "waitlist", "slots", ["slot_id"], ["id"], ondelete="CASCADE"
    )

    # One ask per person per date. The same parent waiting on two Saturdays is two asks, not a duplicate,
    # so the old email-only uniqueness has to go.
    op.drop_constraint("waitlist_email_key", "waitlist", type_="unique")
    op.create_unique_constraint("waitlist_one_per_email_per_slot", "waitlist", ["email", "slot_id"])
    op.create_index("waitlist_by_slot", "waitlist", ["slot_id"])


def downgrade() -> None:
    op.drop_index("waitlist_by_slot", table_name="waitlist")
    op.drop_constraint("waitlist_one_per_email_per_slot", "waitlist", type_="unique")
    op.drop_constraint("waitlist_slot_id_fkey", "waitlist", type_="foreignkey")
    op.drop_column("waitlist", "notified_at")
    op.drop_column("waitlist", "phone")
    op.drop_column("waitlist", "slot_id")
    op.execute("DELETE FROM waitlist")
    op.create_unique_constraint("waitlist_email_key", "waitlist", ["email"])
