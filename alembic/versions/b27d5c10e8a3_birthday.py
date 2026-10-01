"""birthday on holds, bookings, and requests

Revision ID: b27d5c10e8a3
Revises: 93c4f81ab2e0
Create Date: 2026-10-01 21:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b27d5c10e8a3'
down_revision: Union[str, Sequence[str], None] = '93c4f81ab2e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('holds', 'bookings', 'requests')


def upgrade() -> None:
    """Upgrade schema."""
    for table in TABLES:
        op.add_column(table, sa.Column('birthday', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.drop_column(table, 'birthday')
