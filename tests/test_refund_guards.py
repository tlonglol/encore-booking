"""The two guards on automatic refunds: a daily cap, and parents who make a habit of cancelling.

Both queue the refund rather than denying it. The parent is still cancelled and the date is still freed;
only the money waits for the owner.
"""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, Refund
from tests.conftest import FakeSquareApi
from tests.helpers import admin_client, client, make_booking, make_hold, make_slot, today


@pytest.fixture
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _booking(db: Session, day_offset: int, **overrides) -> Booking:
    slot = make_slot(db, today() + timedelta(days=day_offset))
    booking = make_booking(db, make_hold(db, slot), slot)
    for key, value in overrides.items():
        setattr(booking, key, value)
    if overrides:
        db.commit()
    return booking


def _cancel(booking: Booking):  # type: ignore[no-untyped-def]
    return client().post(f"/api/manage/{booking.manage_token}/cancel", json={})


def _refund_calls(square_api: FakeSquareApi) -> int:
    return len([r for r in square_api.requests if r["path"] == "/v2/refunds"])


def test_the_fourth_refund_of_the_day_queues_for_approval(db: Session, square_api: FakeSquareApi) -> None:
    bookings = [_booking(db, 30 + offset * 7) for offset in range(4)]
    for booking in bookings[:3]:
        assert _cancel(booking).json()["refunded"] is True

    body = _cancel(bookings[3]).json()
    assert body["refund_queued"] is True
    assert body["refunded"] is False
    # The parent is still cancelled and the date is still freed; only the money waits.
    db.refresh(bookings[3])
    assert bookings[3].cancelled_at is not None

    assert _refund_calls(square_api) == 3
    queued = db.query(Refund).filter(Refund.status == "pending_approval").one()
    assert "Daily automatic refund cap of 3" in queued.queued_reason


def test_the_queued_refund_still_frees_the_date(db: Session, square_api: FakeSquareApi) -> None:
    bookings = [_booking(db, 30 + offset * 7) for offset in range(4)]
    for booking in bookings[:3]:
        _cancel(booking)
    _cancel(bookings[3])
    grid = client().get("/api/availability?weekends=52").json()["days"]
    day = bookings[3].event_starts_at.date().isoformat()
    assert next(entry["status"] for entry in grid if entry["date"] == day) == "open"


def test_the_cap_is_configurable(db: Session, square_api: FakeSquareApi, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("REFUND_DAILY_CAP", "1")
    get_settings.cache_clear()
    first, second = _booking(db, 30), _booking(db, 37)
    assert _cancel(first).json()["refunded"] is True
    assert _cancel(second).json()["refund_queued"] is True
    assert _refund_calls(square_api) == 1


def test_the_owner_is_told_a_refund_is_waiting(db: Session, square_api: FakeSquareApi, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("REFUND_DAILY_CAP", "0")
    get_settings.cache_clear()
    booking = _booking(db, 30)
    _cancel(booking)
    body = db.execute(text("SELECT body FROM notifications WHERE event = 'cancelled'")).scalar_one()
    assert "QUEUED for your approval" in body


def test_a_third_cancellation_from_one_card_queues(db: Session, square_api: FakeSquareApi) -> None:
    """Two cancellations behind them makes the third one the owner's call, however the contact changes."""
    card = "sq-card-fingerprint-abc"
    first = _booking(db, 30, card_fingerprint=card, phone="+19145550001", email="a@example.com")
    second = _booking(db, 37, card_fingerprint=card, phone="+19145550002", email="b@example.com")
    third = _booking(db, 44, card_fingerprint=card, phone="+19145550003", email="c@example.com")

    assert _cancel(first).json()["refunded"] is True
    assert _cancel(second).json()["refunded"] is True
    body = _cancel(third).json()
    assert body["refund_queued"] is True
    assert _refund_calls(square_api) == 2
    queued = db.query(Refund).filter(Refund.status == "pending_approval").one()
    assert "cancelled 3 bookings" in queued.queued_reason


def test_the_same_email_counts_even_without_a_card(db: Session, square_api: FakeSquareApi) -> None:
    """A payment with no card fingerprint still has to be countable, or the guard has a hole."""
    email = "repeat@example.com"
    bookings = [_booking(db, 30 + n * 7, card_fingerprint=None, email=email, phone=f"+191455510{n:02d}") for n in range(3)]
    _cancel(bookings[0])
    _cancel(bookings[1])
    assert _cancel(bookings[2]).json()["refund_queued"] is True


def test_the_same_phone_counts_too(db: Session, square_api: FakeSquareApi) -> None:
    phone = "+19145557777"
    bookings = [_booking(db, 30 + n * 7, card_fingerprint=None, phone=phone, email=f"p{n}@example.com") for n in range(3)]
    _cancel(bookings[0])
    _cancel(bookings[1])
    assert _cancel(bookings[2]).json()["refund_queued"] is True


def test_different_parents_do_not_pool_their_cancellations(db: Session, square_api: FakeSquareApi) -> None:
    for n in range(3):
        booking = _booking(db, 30 + n * 7, card_fingerprint=f"card-{n}", phone=f"+191455520{n:02d}", email=f"x{n}@example.com")
        assert _cancel(booking).json()["refunded"] is True
    assert _refund_calls(square_api) == 3


def test_a_cancellation_inside_the_window_does_not_spend_a_cap_slot(db: Session, square_api: FakeSquareApi) -> None:
    """Nothing was refunded, so nothing should count against the day's automatic refunds."""
    near = _booking(db, 3)
    assert client().post(
        f"/api/manage/{near.manage_token}/cancel", json={"forfeit_deposit": True}
    ).status_code == 200
    for offset in range(3):
        assert _cancel(_booking(db, 30 + offset * 7)).json()["refunded"] is True
    assert _refund_calls(square_api) == 3


def test_admin_shows_the_queue_and_the_reason(db: Session, square_api: FakeSquareApi, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("REFUND_DAILY_CAP", "0")
    get_settings.cache_clear()
    booking = _booking(db, 30)
    _cancel(booking)
    rows = admin_client().get("/api/admin/refunds").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending_approval"
    assert rows[0]["confirmation_code"] == booking.confirmation_code
    assert "cap of 0" in rows[0]["queued_reason"]


def test_approving_a_queued_refund_sends_it(db: Session, square_api: FakeSquareApi, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("REFUND_DAILY_CAP", "0")
    get_settings.cache_clear()
    booking = _booking(db, 30)
    _cancel(booking)
    refund = db.query(Refund).one()
    assert _refund_calls(square_api) == 0

    response = admin_client().post(f"/api/admin/refunds/{refund.id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    sent = [r for r in square_api.requests if r["path"] == "/v2/refunds"]
    assert len(sent) == 1
    # Same idempotency key an automatic refund would have used, so approving twice refunds once.
    assert sent[0]["body"]["idempotency_key"] == f"encore-refund-{booking.id}"
    assert sent[0]["body"]["amount_money"]["amount"] == 10000


def test_approving_twice_sends_once(db: Session, square_api: FakeSquareApi, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("REFUND_DAILY_CAP", "0")
    get_settings.cache_clear()
    _cancel(_booking(db, 30))
    refund = db.query(Refund).one()
    assert admin_client().post(f"/api/admin/refunds/{refund.id}/approve").status_code == 200
    assert admin_client().post(f"/api/admin/refunds/{refund.id}/approve").status_code == 404
    assert _refund_calls(square_api) == 1


def test_a_queued_refund_can_be_rejected_outright(db: Session, square_api: FakeSquareApi, monkeypatch, reset_settings) -> None:
    monkeypatch.setenv("REFUND_DAILY_CAP", "0")
    get_settings.cache_clear()
    _cancel(_booking(db, 30))
    refund = db.query(Refund).one()
    assert admin_client().post(f"/api/admin/refunds/{refund.id}/reject").status_code == 200
    db.refresh(refund)
    assert refund.status == "rejected"
    assert _refund_calls(square_api) == 0


def test_the_refund_queue_needs_the_admin_password(db: Session) -> None:
    assert client().get("/api/admin/refunds").status_code == 401
    assert client().post("/api/admin/refunds/1/approve").status_code == 401
    assert client().get("/api/admin/repeat-cancellers").status_code == 401


def test_admin_flags_the_repeat_canceller(db: Session, square_api: FakeSquareApi) -> None:
    card = "sq-card-repeat"
    for n in range(3):
        _cancel(_booking(db, 30 + n * 7, card_fingerprint=card, phone=f"+191455530{n:02d}", email=f"r{n}@example.com"))
    rows = admin_client().get("/api/admin/repeat-cancellers").json()
    assert len(rows) == 1
    assert rows[0]["cancellations"] == 3
    assert rows[0]["identity"] == "card"


def test_a_parent_with_two_cancellations_is_not_flagged(db: Session, square_api: FakeSquareApi) -> None:
    card = "sq-card-twice"
    for n in range(2):
        _cancel(_booking(db, 30 + n * 7, card_fingerprint=card, phone=f"+191455540{n:02d}", email=f"t{n}@example.com"))
    assert admin_client().get("/api/admin/repeat-cancellers").json() == []
