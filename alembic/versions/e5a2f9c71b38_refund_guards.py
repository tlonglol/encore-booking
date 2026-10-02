"""refund guards

Revision ID: e5a2f9c71b38
Revises: d81e5c0b4417
Create Date: 2026-10-02 02:55:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5a2f9c71b38'
down_revision: Union[str, Sequence[str], None] = 'd81e5c0b4417'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Who paid, so repeat cancellations can be counted across a changed email or phone.

    Square reports a fingerprint derived from the card number (never the number itself), stable across
    payments made with the same card, so it is the one identifier a parent cannot trivially rotate.
    """
    op.add_column('bookings', sa.Column('card_fingerprint', sa.Text(), nullable=True))
    op.create_index(
        'bookings_card_fingerprint',
        'bookings',
        ['card_fingerprint'],
        postgresql_where=sa.text('card_fingerprint IS NOT NULL'),
    )
    # Counting a parent's cancellations by contact details needs those columns to be searchable too.
    op.create_index('bookings_phone', 'bookings', ['phone'])
    op.create_index('bookings_email', 'bookings', ['email'])
    # The daily cap counts refunds sent today, so it reads this index rather than the whole table.
    op.create_index('refunds_sent_at', 'refunds', ['sent_at'], postgresql_where=sa.text('sent_at IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('refunds_sent_at', table_name='refunds')
    op.drop_index('bookings_email', table_name='bookings')
    op.drop_index('bookings_phone', table_name='bookings')
    op.drop_index('bookings_card_fingerprint', table_name='bookings')
    op.drop_column('bookings', 'card_fingerprint')
