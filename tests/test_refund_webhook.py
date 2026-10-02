"""Square's refund webhook: verified like a payment, and the authority on whether the money actually moved."""

import json
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Refund, SquareEvent
from tests.helpers import client, deliver, make_booking, make_hold, make_slot, today


def refund_event(
    refund_id: str,
    status: str = "COMPLETED",
    event_id: str | None = None,
    event_type: str = "refund.updated",
    payment_id: str = "pay-1",
) -> bytes:
    """Square nests the refund at data.object.refund (Refunds API docs, read 2026-10-02)."""
    return json.dumps(
        {
            "merchant_id": "MTEST",
            "type": event_type,
            "event_id": event_id or str(uuid.uuid4()),
            "created_at": "2026-10-03T14:00:00Z",
            "data": {
                "type": "refund",
                "id": refund_id,
                "object": {
                    "refund": {
                        "id": refund_id,
                        "status": status,
                        "amount_money": {"amount": 10000, "currency": "USD"},
                        "payment_id": payment_id,
                        "order_id": "order-1",
                        "updated_at": "2026-10-03T14:00:05Z",
                    }
                },
            },
        }
    ).encode()


def _cancelled_booking(db: Session) -> Refund:
    slot = make_slot(db, today() + timedelta(days=30))
    booking = make_booking(db, make_hold(db, slot), slot)
    assert client().post(f"/api/manage/{booking.manage_token}/cancel", json={}).status_code == 200
    refund = db.query(Refund).one()
    assert refund.status == "pending"
    assert refund.square_refund_id
    return refund


def test_a_completed_refund_settles_the_row(db: Session) -> None:
    refund = _cancelled_booking(db)
    assert deliver(refund_event(refund.square_refund_id)).status_code == 200
    db.refresh(refund)
    assert refund.status == "completed"
    assert refund.settled_at is not None


def test_a_tampered_refund_body_is_rejected_and_changes_nothing(db: Session) -> None:
    refund = _cancelled_booking(db)
    raw = refund_event(refund.square_refund_id)
    response = deliver(raw + b" ", signature=None, key="wrong-key")
    assert response.status_code == 401
    db.refresh(refund)
    assert refund.status == "pending"


def test_a_redelivered_refund_event_is_a_no_op(db: Session) -> None:
    refund = _cancelled_booking(db)
    raw = refund_event(refund.square_refund_id)
    assert deliver(raw).status_code == 200
    assert deliver(raw).json()["outcome"] == "duplicate"
    assert db.query(SquareEvent).filter(SquareEvent.event_type.startswith("refund.")).count() == 1


def test_created_then_updated_for_one_refund_settles_once(db: Session) -> None:
    refund = _cancelled_booking(db)
    assert deliver(refund_event(refund.square_refund_id, status="PENDING", event_type="refund.created")).status_code == 200
    db.refresh(refund)
    assert refund.status == "pending"

    assert deliver(refund_event(refund.square_refund_id, status="COMPLETED")).status_code == 200
    db.refresh(refund)
    assert refund.status == "completed"


def test_a_failed_refund_is_marked_for_the_owner(db: Session) -> None:
    refund = _cancelled_booking(db)
    assert deliver(refund_event(refund.square_refund_id, status="FAILED")).status_code == 200
    db.refresh(refund)
    assert refund.status == "failed"


def test_a_rejected_refund_is_marked_too(db: Session) -> None:
    refund = _cancelled_booking(db)
    deliver(refund_event(refund.square_refund_id, status="REJECTED"))
    db.refresh(refund)
    assert refund.status == "rejected"


def test_a_refund_we_never_sent_is_recorded_and_ignored(db: Session) -> None:
    response = deliver(refund_event("refund-from-somewhere-else"))
    assert response.status_code == 200
    assert response.json()["outcome"] == "refund_unmatched"
    assert db.query(Refund).count() == 0


def test_a_refund_event_never_touches_a_booking(db: Session) -> None:
    """Refund events settle a refund row. They must not look like payments and book anything."""
    refund = _cancelled_booking(db)
    before = db.execute(text("SELECT count(*) FROM bookings")).scalar_one()
    deliver(refund_event(refund.square_refund_id))
    after = db.execute(text("SELECT count(*) FROM bookings")).scalar_one()
    assert before == after
