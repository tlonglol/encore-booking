"""hold guards

Revision ID: a7b4e81c90df
Revises: f0c93d6a2e15
Create Date: 2026-10-02 04:05:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7b4e81c90df'
down_revision: Union[str, Sequence[str], None] = 'f0c93d6a2e15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENTS = ("'booked', 'late_payment', 'cancelled', 'rescheduled', 'addons_changed', 'refunded',"
          " 'hold_spike', 'request'")
OLD_EVENTS = ("'booked', 'late_payment', 'cancelled', 'rescheduled', 'addons_changed', 'refunded',"
              " 'request'")


def upgrade() -> None:
    """The browser-wide session a hold belongs to, so one browser can hold one date without the client
    having to be honest about it, and so the per-date cooldown knows who it is talking to."""
    op.add_column('holds', sa.Column('session_id', sa.Text(), nullable=True))
    # The swap reads this on every hold creation; the cooldown reads it per slot.
    op.create_index(
        'holds_session_active',
        'holds',
        ['session_id'],
        postgresql_where=sa.text("session_id IS NOT NULL AND status = 'pending_payment'"),
    )
    op.create_index(
        'holds_session_slot',
        'holds',
        ['session_id', 'slot_id', 'expires_at'],
        postgresql_where=sa.text('session_id IS NOT NULL'),
    )
    # The rolling-hour creation rate reads by IP and time.
    op.create_index('holds_client_ip_created', 'holds', ['client_ip', 'created_at'])

    op.drop_constraint('notifications_event_valid', 'notifications', type_='check')
    op.create_check_constraint('notifications_event_valid', 'notifications', f"event IN ({EVENTS})")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('notifications_event_valid', 'notifications', type_='check')
    op.create_check_constraint('notifications_event_valid', 'notifications', f"event IN ({OLD_EVENTS})")
    op.drop_index('holds_client_ip_created', table_name='holds')
    op.drop_index('holds_session_slot', table_name='holds')
    op.drop_index('holds_session_active', table_name='holds')
    op.drop_column('holds', 'session_id')
