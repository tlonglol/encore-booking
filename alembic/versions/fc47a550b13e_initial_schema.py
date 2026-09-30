"""initial schema

Revision ID: fc47a550b13e
Revises: 
Create Date: 2026-09-30 00:53:38.947534

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fc47a550b13e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('slots',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('blocked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('starts_at')
    )
    op.create_index(op.f('ix_slots_local_date'), 'slots', ['local_date'], unique=False)
    op.create_table('square_events',
    sa.Column('event_id', sa.Text(), nullable=False),
    sa.Column('event_type', sa.Text(), nullable=False),
    sa.Column('payment_id', sa.Text(), nullable=True),
    sa.Column('order_id', sa.Text(), nullable=True),
    sa.Column('outcome', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("outcome IN ('booked', 'duplicate', 'duplicate_payment', 'expired_hold', 'unmatched', 'amount_mismatch')", name='square_events_outcome_valid'),
    sa.PrimaryKeyConstraint('event_id')
    )
    op.create_table('holds',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('slot_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('package', sa.Text(), nullable=False),
    sa.Column('kids', sa.SmallInteger(), nullable=False),
    sa.Column('addons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('line_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_cents', sa.Integer(), nullable=False),
    sa.Column('deposit_cents', sa.Integer(), nullable=False),
    sa.Column('first_name', sa.Text(), nullable=False),
    sa.Column('last_name', sa.Text(), nullable=False),
    sa.Column('phone', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('client_ip', postgresql.INET(), nullable=True),
    sa.Column('square_order_id', sa.Text(), nullable=True),
    sa.Column('square_payment_link_id', sa.Text(), nullable=True),
    sa.Column('checkout_url', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("package IN ('standard', 'plus', 'premium')", name='holds_package_valid'),
    sa.CheckConstraint("status IN ('pending_payment', 'booked', 'paid_after_expiry', 'expired', 'cancelled')", name='holds_status_valid'),
    sa.CheckConstraint('kids BETWEEN 1 AND 30', name='holds_kids_range'),
    sa.CheckConstraint('total_cents >= 0 AND deposit_cents > 0', name='holds_money_positive'),
    sa.ForeignKeyConstraint(['slot_id'], ['slots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('holds_one_active_per_slot', 'holds', ['slot_id'], unique=True, postgresql_where=sa.text("status IN ('pending_payment', 'booked')"))
    op.create_index('holds_pending_client_ip', 'holds', ['client_ip'], unique=False, postgresql_where=sa.text("status = 'pending_payment'"))
    op.create_index('holds_pending_email', 'holds', ['email'], unique=False, postgresql_where=sa.text("status = 'pending_payment'"))
    op.create_index('holds_pending_phone', 'holds', ['phone'], unique=False, postgresql_where=sa.text("status = 'pending_payment'"))
    op.create_index('holds_square_order_id_key', 'holds', ['square_order_id'], unique=True, postgresql_where=sa.text('square_order_id IS NOT NULL'))
    op.create_table('bookings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('hold_id', sa.UUID(), nullable=False),
    sa.Column('slot_id', sa.BigInteger(), nullable=False),
    sa.Column('confirmation_code', sa.Text(), nullable=False),
    sa.Column('package', sa.Text(), nullable=False),
    sa.Column('kids', sa.SmallInteger(), nullable=False),
    sa.Column('addons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('line_items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_cents', sa.Integer(), nullable=False),
    sa.Column('deposit_cents', sa.Integer(), nullable=False),
    sa.Column('first_name', sa.Text(), nullable=False),
    sa.Column('last_name', sa.Text(), nullable=False),
    sa.Column('phone', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('square_payment_id', sa.Text(), nullable=False),
    sa.Column('square_order_id', sa.Text(), nullable=False),
    sa.Column('paid_amount_cents', sa.Integer(), nullable=False),
    sa.Column('event_starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("package IN ('standard', 'plus', 'premium')", name='bookings_package_valid'),
    sa.CheckConstraint('kids BETWEEN 1 AND 30', name='bookings_kids_range'),
    sa.ForeignKeyConstraint(['hold_id'], ['holds.id'], ),
    sa.ForeignKeyConstraint(['slot_id'], ['slots.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('confirmation_code'),
    sa.UniqueConstraint('hold_id'),
    sa.UniqueConstraint('square_payment_id')
    )
    op.create_index('bookings_one_live_per_slot', 'bookings', ['slot_id'], unique=True, postgresql_where=sa.text('cancelled_at IS NULL'))
    op.create_table('payment_flags',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('hold_id', sa.UUID(), nullable=True),
    sa.Column('square_payment_id', sa.Text(), nullable=False),
    sa.Column('square_order_id', sa.Text(), nullable=True),
    sa.Column('amount_cents', sa.Integer(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('paid_after_expiry', 'amount_mismatch', 'unmatched_order', 'duplicate_payment')", name='payment_flags_kind_valid'),
    sa.ForeignKeyConstraint(['hold_id'], ['holds.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('square_payment_id')
    )
    op.create_table('notifications',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('hold_id', sa.UUID(), nullable=False),
    sa.Column('booking_id', sa.BigInteger(), nullable=True),
    sa.Column('audience', sa.Text(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('recipient', sa.Text(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('provider_message_id', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("audience IN ('parent', 'owner')", name='notifications_audience_valid'),
    sa.CheckConstraint("channel IN ('sms', 'discord')", name='notifications_channel_valid'),
    sa.CheckConstraint("status IN ('pending', 'sent', 'failed', 'logged')", name='notifications_status_valid'),
    sa.ForeignKeyConstraint(['booking_id'], ['bookings.id'], ),
    sa.ForeignKeyConstraint(['hold_id'], ['holds.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('notifications_once_per_hold', 'notifications', ['hold_id', 'audience', 'channel'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('notifications_once_per_hold', table_name='notifications')
    op.drop_table('notifications')
    op.drop_table('payment_flags')
    op.drop_index('bookings_one_live_per_slot', table_name='bookings', postgresql_where=sa.text('cancelled_at IS NULL'))
    op.drop_table('bookings')
    op.drop_index('holds_square_order_id_key', table_name='holds', postgresql_where=sa.text('square_order_id IS NOT NULL'))
    op.drop_index('holds_pending_phone', table_name='holds', postgresql_where=sa.text("status = 'pending_payment'"))
    op.drop_index('holds_pending_email', table_name='holds', postgresql_where=sa.text("status = 'pending_payment'"))
    op.drop_index('holds_pending_client_ip', table_name='holds', postgresql_where=sa.text("status = 'pending_payment'"))
    op.drop_index('holds_one_active_per_slot', table_name='holds', postgresql_where=sa.text("status IN ('pending_payment', 'booked')"))
    op.drop_table('holds')
    op.drop_table('square_events')
    op.drop_index(op.f('ix_slots_local_date'), table_name='slots')
    op.drop_table('slots')
