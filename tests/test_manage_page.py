"""The manage token: how it reaches the parent, and who it does not reach."""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import Booking
from tests.helpers import (
    admin_client,
    client,
    create_hold_via_api,
    deliver,
    make_booking,
    make_hold,
    make_slot,
    payment_event,
    today,
)


def _book(db: Session, days_ahead: int = 21, n: int = 1) -> Booking:
    """Through the real funnel and a real webhook, so the booking is made the way a parent makes one."""
    hold_id, ref = create_hold_via_api(db, days_ahead=days_ahead, n=n)
    assert deliver(payment_event(ref, payment_id=f"pay-{n}")).status_code == 200
    booking = db.query(Booking).filter(Booking.hold_id == hold_id).one()
    db.refresh(booking)
    return booking


def _book_on(db: Session, days_away: int) -> Booking:
    """A booking a chosen number of days out. next_saturday rounds up to a weekend, which is no good for
    testing a window measured in days, so this one places the party exactly."""
    slot = make_slot(db, today() + timedelta(days=days_away))
    return make_booking(db, make_hold(db, slot), slot)


def test_booking_gets_its_own_token_separate_from_the_confirmation_code(db: Session) -> None:
    booking = _book(db)
    assert booking.manage_token
    assert booking.manage_token != booking.confirmation_code
    assert len(booking.manage_token) >= 24


def test_tokens_are_unique_per_booking(db: Session) -> None:
    first = _book(db, days_ahead=21, n=1).manage_token
    second = _book(db, days_ahead=28, n=2).manage_token
    assert first != second


def test_confirmation_page_is_handed_the_manage_token(db: Session) -> None:
    booking = _book(db)
    party = client().get(f"/api/bookings/{booking.confirmation_code}").json()["party"]
    assert party["manage_token"] == booking.manage_token


def test_the_post_payment_confirmation_poll_is_handed_it_too(db: Session) -> None:
    """Square redirects to confirm.html?hold=..., so that view needs the link as much as the code view does."""
    booking = _book(db)
    body = client().get(f"/api/holds/{booking.hold_id}").json()
    assert body["status"] == "booked"
    assert body["party"]["manage_token"] == booking.manage_token


def test_a_pending_hold_never_exposes_a_manage_token(db: Session) -> None:
    hold_id, _ = create_hold_via_api(db)
    body = client().get(f"/api/holds/{hold_id}").json()
    assert body["status"] == "pending_payment"
    assert body["party"]["manage_token"] is None


def test_manage_page_loads_the_party_by_token(db: Session) -> None:
    booking = _book(db)
    response = client().get(f"/api/manage/{booking.manage_token}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "booked"
    assert body["party"]["confirmation_code"] == booking.confirmation_code
    assert body["inside_free_window"] is True


def test_a_party_inside_seven_days_says_so(db: Session) -> None:
    booking = _book_on(db, days_away=3)
    body = client().get(f"/api/manage/{booking.manage_token}").json()
    assert body["inside_free_window"] is False


def test_exactly_seven_days_out_is_still_free(db: Session) -> None:
    """The boundary the promise names: "up to 7 days before" includes the seventh day."""
    booking = _book_on(db, days_away=7)
    assert client().get(f"/api/manage/{booking.manage_token}").json()["inside_free_window"] is True


def test_six_days_out_is_not(db: Session) -> None:
    booking = _book_on(db, days_away=6)
    assert client().get(f"/api/manage/{booking.manage_token}").json()["inside_free_window"] is False


def test_the_free_window_closes_seven_days_before_the_party(db: Session) -> None:
    booking = _book(db)
    body = client().get(f"/api/manage/{booking.manage_token}").json()
    party_day = booking.event_starts_at.date()
    assert body["free_until"] == (party_day - timedelta(days=7)).isoformat()


def test_an_unknown_or_malformed_token_is_a_404(db: Session) -> None:
    _book(db)
    assert client().get("/api/manage/not-a-real-token-but-long-enough-x").status_code == 404
    assert client().get("/api/manage/short").status_code == 404


def test_admin_never_lists_the_manage_token(db: Session) -> None:
    """It is the parent's credential for destructive actions; the owner has their own login instead."""
    _book(db)
    rows = admin_client().get("/api/admin/bookings").json()
    assert rows
    assert all("manage_token" not in row for row in rows)


def test_the_parent_email_carries_the_manage_link(db: Session) -> None:
    booking = _book(db)
    rows = admin_client().get("/api/admin/notifications").json()
    email = next(row for row in rows if row["channel"] == "email" and row["audience"] == "parent")
    assert f"manage.html?t={booking.manage_token}" in email["body"]


def test_the_calendar_file_carries_the_code_but_not_the_token(db: Session) -> None:
    """The .ics gets shared with family; it must not hand them the cancel button."""
    booking = _book(db)
    ics = client().get(f"/api/bookings/{booking.confirmation_code}/calendar.ics").text
    assert booking.confirmation_code in ics
    assert booking.manage_token not in ics
