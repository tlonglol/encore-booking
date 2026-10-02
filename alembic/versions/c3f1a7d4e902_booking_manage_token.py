"""booking manage token

Revision ID: c3f1a7d4e902
Revises: b27d5c10e8a3
Create Date: 2026-10-02 01:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3f1a7d4e902'
down_revision: Union[str, Sequence[str], None] = 'b27d5c10e8a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """The secret behind the no-login manage page. Added nullable, backfilled, then made required, so an
    existing booking gets a working link instead of a blank one."""
    op.add_column('bookings', sa.Column('manage_token', sa.Text(), nullable=True))
    # Two uuids' worth of hex: 256 random bits from the same CSPRNG the id defaults use, url-safe, and with
    # no pgcrypto dependency (gen_random_bytes would need the extension; gen_random_uuid is built in).
    op.execute(
        "UPDATE bookings SET manage_token ="
        " replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '')"
        " WHERE manage_token IS NULL"
    )
    op.alter_column('bookings', 'manage_token', nullable=False)
    op.create_index('bookings_manage_token_key', 'bookings', ['manage_token'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('bookings_manage_token_key', table_name='bookings')
    op.drop_column('bookings', 'manage_token')
