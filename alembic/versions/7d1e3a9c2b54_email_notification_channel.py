"""email notification channel

Revision ID: 7d1e3a9c2b54
Revises: 4b9bc5052066
Create Date: 2026-09-30 19:05:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7d1e3a9c2b54'
down_revision: Union[str, Sequence[str], None] = '4b9bc5052066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('notifications_channel_valid', 'notifications', type_='check')
    op.create_check_constraint(
        'notifications_channel_valid', 'notifications', "channel IN ('sms', 'discord', 'email')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('notifications_channel_valid', 'notifications', type_='check')
    op.create_check_constraint('notifications_channel_valid', 'notifications', "channel IN ('sms', 'discord')")
