"""party requests

Revision ID: 4b9bc5052066
Revises: fc47a550b13e
Create Date: 2026-09-30 17:17:22.117359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4b9bc5052066'
down_revision: Union[str, Sequence[str], None] = 'fc47a550b13e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('requests',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('local_date', sa.Date(), nullable=False),
    sa.Column('preferred_time', sa.Time(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'new'"), nullable=False),
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
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("package IN ('standard', 'plus', 'premium')", name='requests_package_valid'),
    sa.CheckConstraint("status IN ('new', 'approved', 'declined', 'expired', 'converted')", name='requests_status_valid'),
    sa.CheckConstraint('kids BETWEEN 1 AND 30', name='requests_kids_range'),
    sa.CheckConstraint('total_cents >= 0', name='requests_total_positive'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('requests_one_new_per_email_date', 'requests', ['email', 'local_date'], unique=True, postgresql_where=sa.text("status = 'new'"))
    op.create_index('requests_one_new_per_phone_date', 'requests', ['phone', 'local_date'], unique=True, postgresql_where=sa.text("status = 'new'"))
    op.create_index('requests_status_created', 'requests', ['status', 'created_at'], unique=False)
    op.add_column('notifications', sa.Column('request_id', sa.BigInteger(), nullable=True))
    op.alter_column('notifications', 'hold_id',
               existing_type=sa.UUID(),
               nullable=True)
    op.create_index('notifications_once_per_request', 'notifications', ['request_id', 'audience', 'channel'], unique=True, postgresql_where=sa.text('request_id IS NOT NULL'))
    op.create_foreign_key('notifications_request_id_fkey', 'notifications', 'requests', ['request_id'], ['id'])
    op.create_check_constraint(
        'notifications_one_subject', 'notifications', '(hold_id IS NULL) <> (request_id IS NULL)'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('notifications_one_subject', 'notifications', type_='check')
    op.drop_constraint('notifications_request_id_fkey', 'notifications', type_='foreignkey')
    op.execute('DELETE FROM notifications WHERE request_id IS NOT NULL')
    op.drop_index('notifications_once_per_request', table_name='notifications', postgresql_where=sa.text('request_id IS NOT NULL'))
    op.alter_column('notifications', 'hold_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.drop_column('notifications', 'request_id')
    op.drop_index('requests_status_created', table_name='requests')
    op.drop_index('requests_one_new_per_phone_date', table_name='requests', postgresql_where=sa.text("status = 'new'"))
    op.drop_index('requests_one_new_per_email_date', table_name='requests', postgresql_where=sa.text("status = 'new'"))
    op.drop_table('requests')
