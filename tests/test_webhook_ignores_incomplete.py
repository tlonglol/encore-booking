import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.helpers import create_hold_via_api, deliver, payment_event


@pytest.mark.parametrize("status", ["APPROVED", "PENDING", "FAILED", "CANCELED", ""])
def test_webhook_ignores_incomplete(db: Session, engine: Engine, status: str) -> None:
    _, order_id = create_hold_via_api(db)
    response = deliver(payment_event(order_id, status=status))
    assert response.status_code == 200
    assert response.json() == {"outcome": "ignored"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM bookings")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM square_events")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM payment_flags")).scalar_one() == 0
        assert conn.execute(text("SELECT status FROM holds")).scalar_one() == "pending_payment"


def test_non_payment_events_are_ignored(db: Session) -> None:
    _, order_id = create_hold_via_api(db)
    response = deliver(payment_event(order_id, event_type="refund.created"))
    assert response.status_code == 200
    assert response.json() == {"outcome": "ignored"}
