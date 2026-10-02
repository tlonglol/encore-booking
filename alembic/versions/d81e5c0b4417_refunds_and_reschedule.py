"""refunds and reschedule

Revision ID: d81e5c0b4417
Revises: c3f1a7d4e902
Create Date: 2026-10-02 02:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd81e5c0b4417'
down_revision: Union[str, Sequence[str], None] = 'c3f1a7d4e902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REFUND_STATUSES = "'pending_approval', 'pending', 'completed', 'failed', 'rejected'"
NOTIFICATION_EVENTS = "'booked', 'late_payment', 'cancelled', 'rescheduled', 'addons_changed', 'request'"
# A booking notice must be sent once however many times Square redelivers; the rest are single requests.
CLAIMED_ONCE = "event IN ('booked', 'late_payment')"


def upgrade() -> None:
    """The refund record, the reschedule counters, and room in the notifications table for the new notices."""
    op.add_column('bookings', sa.Column('reschedule_count', sa.SmallInteger(), nullable=False, server_default='0'))
    op.add_column('bookings', sa.Column('rescheduled_at', sa.DateTime(timezone=True), nullable=True))
    # The one courtesy move promised inside the 7-day window. Moves made outside it are free and never spend it.
    op.add_column(
        'bookings',
        sa.Column('courtesy_reschedule_used', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('bookings', sa.Column('cancel_source', sa.Text(), nullable=True))
    op.create_check_constraint(
        'bookings_cancel_source_valid', 'bookings', "cancel_source IS NULL OR cancel_source IN ('parent', 'owner')"
    )

    op.create_table(
        'refunds',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('booking_id', sa.BigInteger(), sa.ForeignKey('bookings.id'), nullable=False),
        sa.Column('square_payment_id', sa.Text(), nullable=False),
        sa.Column('square_refund_id', sa.Text(), nullable=True),
        # Square caps the idempotency key at 45 characters, so it is derived from the booking id, not a uuid.
        sa.Column('idempotency_key', sa.Text(), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('queued_reason', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN ({REFUND_STATUSES})", name='refunds_status_valid'),
        sa.CheckConstraint('amount_cents > 0', name='refunds_amount_positive'),
    )
    # One deposit, one refund: the cap and the webhook both rely on a booking never having two refund rows.
    op.create_index('refunds_one_per_booking', 'refunds', ['booking_id'], unique=True)
    op.create_index(
        'refunds_square_refund_id_key',
        'refunds',
        ['square_refund_id'],
        unique=True,
        postgresql_where=sa.text('square_refund_id IS NOT NULL'),
    )
    op.create_index('refunds_status_created', 'refunds', ['status', 'created_at'])

    # Notifications learn what they are announcing, so a cancellation does not collide with the booking notice.
    op.add_column('notifications', sa.Column('event', sa.Text(), nullable=False, server_default='booked'))
    op.execute("UPDATE notifications SET event = 'request' WHERE request_id IS NOT NULL")
    op.create_check_constraint(
        'notifications_event_valid', 'notifications', f"event IN ({NOTIFICATION_EVENTS})"
    )
    op.drop_index('notifications_once_per_hold', table_name='notifications')
    op.create_index(
        'notifications_once_per_hold',
        'notifications',
        ['hold_id', 'audience', 'channel'],
        unique=True,
        postgresql_where=sa.text(CLAIMED_ONCE),
    )

    # Refund events share the event log with payments, so its outcomes have to admit them.
    op.drop_constraint('square_events_outcome_valid', 'square_events', type_='check')
    op.create_check_constraint(
        'square_events_outcome_valid',
        'square_events',
        "outcome IN ('booked', 'duplicate', 'duplicate_payment', 'expired_hold', 'unmatched',"
        " 'amount_mismatch', 'refund_settled', 'refund_unmatched')",
    )
    op.add_column('square_events', sa.Column('refund_id', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('square_events', 'refund_id')
    op.drop_constraint('square_events_outcome_valid', 'square_events', type_='check')
    op.create_check_constraint(
        'square_events_outcome_valid',
        'square_events',
        "outcome IN ('booked', 'duplicate', 'duplicate_payment', 'expired_hold', 'unmatched', 'amount_mismatch')",
    )
    op.drop_index('notifications_once_per_hold', table_name='notifications')
    op.create_index(
        'notifications_once_per_hold', 'notifications', ['hold_id', 'audience', 'channel'], unique=True
    )
    op.drop_constraint('notifications_event_valid', 'notifications', type_='check')
    op.drop_column('notifications', 'event')
    op.drop_index('refunds_status_created', table_name='refunds')
    op.drop_index('refunds_square_refund_id_key', table_name='refunds')
    op.drop_index('refunds_one_per_booking', table_name='refunds')
    op.drop_table('refunds')
    op.drop_constraint('bookings_cancel_source_valid', 'bookings', type_='check')
    op.drop_column('bookings', 'cancel_source')
    op.drop_column('bookings', 'courtesy_reschedule_used')
    op.drop_column('bookings', 'rescheduled_at')
    op.drop_column('bookings', 'reschedule_count')
