"""manual bookings and balance paid

Revision ID: d4a7e2b91c56
Revises: c91f4a7b25d0
Create Date: 2026-10-02

Two owner operations that have no online equivalent.

`origin` says how a booking came to exist. Every row that already exists came through the funnel and a
real Square payment, so the backfill is 'online' and the column is NOT NULL from the start. It matters
beyond reporting: a manual booking's square_payment_id is synthetic, so the refund path has to be able to
tell the two apart before it calls Square.

`balance_paid_at` is the owner marking the money they took in person at the party. It is a timestamp and
nothing else: no amount, because the amount is already the booking's own balance, and no second row,
because there is nothing to reconcile against.
"""

import sqlalchemy as sa
from alembic import op

revision = "d4a7e2b91c56"
down_revision = "c91f4a7b25d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("origin", sa.Text(), nullable=False, server_default="online"),
    )
    op.create_check_constraint("bookings_origin_valid", "bookings", "origin IN ('online', 'manual')")
    op.add_column("bookings", sa.Column("balance_paid_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bookings", "balance_paid_at")
    op.drop_constraint("bookings_origin_valid", "bookings", type_="check")
    op.drop_column("bookings", "origin")
