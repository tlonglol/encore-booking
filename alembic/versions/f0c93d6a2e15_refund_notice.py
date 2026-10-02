"""refund notice

Revision ID: f0c93d6a2e15
Revises: e5a2f9c71b38
Create Date: 2026-10-02 03:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f0c93d6a2e15'
down_revision: Union[str, Sequence[str], None] = 'e5a2f9c71b38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENTS = "'booked', 'late_payment', 'cancelled', 'rescheduled', 'addons_changed', 'refunded', 'request'"
OLD_EVENTS = "'booked', 'late_payment', 'cancelled', 'rescheduled', 'addons_changed', 'request'"
# Square sends refund.created and refund.updated for one refund and redelivers both, so the settlement
# notice joins the events that are claimed once rather than simply recorded.
CLAIMED_ONCE = "event IN ('booked', 'late_payment', 'refunded')"
OLD_CLAIMED_ONCE = "event IN ('booked', 'late_payment')"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('notifications_event_valid', 'notifications', type_='check')
    op.create_check_constraint('notifications_event_valid', 'notifications', f"event IN ({EVENTS})")
    op.drop_index('notifications_once_per_hold', table_name='notifications')
    op.create_index(
        'notifications_once_per_hold',
        'notifications',
        ['hold_id', 'audience', 'channel'],
        unique=True,
        postgresql_where=sa.text(CLAIMED_ONCE),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('notifications_once_per_hold', table_name='notifications')
    op.create_index(
        'notifications_once_per_hold',
        'notifications',
        ['hold_id', 'audience', 'channel'],
        unique=True,
        postgresql_where=sa.text(OLD_CLAIMED_ONCE),
    )
    op.drop_constraint('notifications_event_valid', 'notifications', type_='check')
    op.create_check_constraint('notifications_event_valid', 'notifications', f"event IN ({OLD_EVENTS})")
