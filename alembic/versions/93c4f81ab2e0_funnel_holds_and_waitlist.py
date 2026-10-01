"""funnel holds and waitlist

Revision ID: 93c4f81ab2e0
Revises: 7d1e3a9c2b54
Create Date: 2026-09-30 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '93c4f81ab2e0'
down_revision: Union[str, Sequence[str], None] = '7d1e3a9c2b54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SNAPSHOT_COLUMNS = ('package', 'kids', 'addons', 'line_items', 'total_cents', 'first_name', 'last_name', 'phone', 'email')
CHECKOUT_COMPLETE = (
    "square_order_id IS NULL OR (package IS NOT NULL AND kids IS NOT NULL AND total_cents IS NOT NULL"
    " AND first_name IS NOT NULL AND phone IS NOT NULL AND email IS NOT NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    for column in SNAPSHOT_COLUMNS:
        op.alter_column('holds', column, nullable=True)
    op.create_check_constraint('holds_checkout_complete', 'holds', CHECKOUT_COMPLETE)
    for table in ('holds', 'bookings', 'requests'):
        op.add_column(table, sa.Column('kid_name', sa.Text(), nullable=True))
        op.add_column(table, sa.Column('kid_age', sa.SmallInteger(), nullable=True))
    op.create_table(
        'waitlist',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('client_ip', postgresql.INET(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_waitlist')),
        sa.UniqueConstraint('email', name=op.f('waitlist_email_key')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('waitlist')
    for table in ('holds', 'bookings', 'requests'):
        op.drop_column(table, 'kid_age')
        op.drop_column(table, 'kid_name')
    op.drop_constraint('holds_checkout_complete', 'holds', type_='check')
    for column in SNAPSHOT_COLUMNS:
        op.alter_column('holds', column, nullable=False)
