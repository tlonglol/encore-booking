"""Cancelling from the manage link, and the automatic refund behind it."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Booking, Refund
from tests.conftest import FakeSquareApi
from tests.helpers import client, make_booking, make_hold, make_slot, today


def _booking_on(db: Session, days_away: int) -> Booking:
    slot = make_slot(db, today() + timedelta(days=days_away))
    return make_booking(db, make_hold(db, slot), slot)


def _grid_status(db: Session, day) -> str:
    """What the next parent actually sees, which is the only thing that proves the date was released."""
    body = client().get("/api/availability?weekends=52").json()
    return next(entry["status"] for entry in body["days"] if entry["date"] == day.isoformat())


def _refund_requests(square_api: FakeSquareApi) -> list[dict]:
    return [request for request in square_api.requests if request["path"] == "/v2/refunds"]


def test_cancel_outside_the_window_refunds_the_deposit(db: Session, square_api: FakeSquareApi) -> None:
    booking = _booking_on(db, days_away=30)
    response = client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refunded"] is True
    assert body["amount_cents"] == 10000
    assert "5 to 10 business days" in body["settlement_notice"]

    sent = _refund_requests(square_api)
    assert len(sent) == 1
    assert sent[0]["body"]["payment_id"] == booking.square_payment_id
    # The amount comes off the payment record, never off the request.
    assert sent[0]["body"]["amount_money"] == {"amount": 10000, "currency": "USD"}
    assert sent[0]["body"]["idempotency_key"] == f"encore-refund-{booking.id}"
    assert len(sent[0]["body"]["idempotency_key"]) <= 45


def test_cancel_reopens_the_date_on_the_grid(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    day = booking.event_starts_at.date()
    assert _grid_status(db, day) == "booked"
    assert client().post(f"/api/manage/{booking.manage_token}/cancel", json={}).status_code == 200
    assert _grid_status(db, day) == "open"


def test_the_hold_releases_the_date_too(db: Session) -> None:
    """The hold shares the slot-exclusivity index with the booking, so a booked hold left behind would
    keep the date locked even though the booking is gone."""
    booking = _booking_on(db, days_away=30)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    status = db.execute(
        text("SELECT status FROM holds WHERE id = :id"), {"id": booking.hold_id}
    ).scalar_one()
    assert status == "cancelled"


def test_cancel_inside_the_window_refuses_until_the_parent_confirms(db: Session, square_api: FakeSquareApi) -> None:
    booking = _booking_on(db, days_away=3)
    response = client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    assert response.status_code == 409
    assert response.json()["needs_confirmation"] is True
    assert _refund_requests(square_api) == []
    db.refresh(booking)
    assert booking.cancelled_at is None


def test_confirmed_cancel_inside_the_window_forfeits_the_deposit(db: Session, square_api: FakeSquareApi) -> None:
    booking = _booking_on(db, days_away=3)
    response = client().post(
        f"/api/manage/{booking.manage_token}/cancel", json={"forfeit_deposit": True}
    )
    assert response.status_code == 200, response.text
    assert response.json()["refunded"] is False
    # Nothing was sent to Square, and nothing was recorded as owed.
    assert _refund_requests(square_api) == []
    assert db.query(Refund).count() == 0
    # The date still goes back on the grid: an unsold Saturday helps nobody.
    assert _grid_status(db, booking.event_starts_at.date()) == "open"


def test_a_replayed_cancel_refunds_once(db: Session, square_api: FakeSquareApi) -> None:
    booking = _booking_on(db, days_away=30)
    first = client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    second = client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    assert first.status_code == 200
    assert second.status_code == 409
    assert len(_refund_requests(square_api)) == 1
    assert db.query(Refund).count() == 1


def test_an_unknown_token_cancels_nothing(db: Session, square_api: FakeSquareApi) -> None:
    _booking_on(db, days_away=30)
    response = client().post("/api/manage/not-a-real-token-but-long-enough/cancel", json={})
    assert response.status_code == 404
    assert _refund_requests(square_api) == []
    assert db.query(Booking).filter(Booking.cancelled_at.isnot(None)).count() == 0


def test_a_square_outage_still_cancels_and_leaves_the_refund_to_retry(
    db: Session, square_api: FakeSquareApi
) -> None:
    """The parent's cancellation must not depend on Square being up. The date frees, the refund is visible."""
    booking = _booking_on(db, days_away=30)
    square_api.fail_with = 500
    response = client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    assert response.status_code == 200, response.text

    db.refresh(booking)
    assert booking.cancelled_at is not None
    assert _grid_status(db, booking.event_starts_at.date()) == "open"
    refund = db.query(Refund).one()
    assert refund.status == "failed"
    assert refund.error


def test_the_owner_is_told_on_every_cancellation(db: Session) -> None:
    booking = _booking_on(db, days_away=30)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    rows = db.execute(
        text("SELECT body, audience, event FROM notifications WHERE event = 'cancelled'")
    ).all()
    assert len(rows) == 1
    assert rows[0].audience == "owner"
    assert "CANCELLED" in rows[0].body
    assert "Date is open again" in rows[0].body


def test_a_cancelled_booking_still_loads_its_manage_page(db: Session) -> None:
    """The parent is owed a page explaining what happened, not a 404."""
    booking = _booking_on(db, days_away=30)
    client().post(f"/api/manage/{booking.manage_token}/cancel", json={})
    body = client().get(f"/api/manage/{booking.manage_token}").json()
    assert body["status"] == "cancelled"
    assert body["can"]["can_cancel"] is False
    assert body["can"]["can_reschedule"] is False


def test_the_manage_page_says_what_the_parent_can_do(db: Session) -> None:
    far = client().get(f"/api/manage/{_booking_on(db, days_away=30).manage_token}").json()["can"]
    assert (far["can_cancel"], far["refund_on_cancel"], far["can_reschedule"]) == (True, True, True)

    near = client().get(f"/api/manage/{_booking_on(db, days_away=3).manage_token}").json()["can"]
    assert near["can_cancel"] is True
    assert near["refund_on_cancel"] is False
    assert "no longer refundable" in near["cancel_reason"]
