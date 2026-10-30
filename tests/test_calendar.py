from datetime import timedelta

from sqlalchemy.orm import Session

from app.timeutil import slot_start_utc
from tests.helpers import client, make_booking, make_hold, make_slot, next_saturday


def test_calendar_ics_for_a_booking(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    booking = make_booking(db, make_hold(db, slot, status="booked"), slot)
    response = client().get(f"/api/bookings/{booking.confirmation_code}/calendar.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    body = response.text
    day = next_saturday(7)
    assert f"UID:{booking.confirmation_code}@book.encoregaming.us" in body
    # The venue opens at 10:00 America/New_York, which is 14:00 UTC under EDT and 15:00 under EST. Asking
    # the same function the slot was built with keeps this true on both sides of a changeover; the literal
    # that used to be here was written in October and went wrong on its own the moment the clocks went back.
    start = slot_start_utc(day)
    assert f"DTSTART:{start:%Y%m%dT%H%M%SZ}" in body, "10:00 AM at the venue, converted rather than assumed"
    assert f"DTEND:{start + timedelta(hours=3):%Y%m%dT%H%M%SZ}" in body, "a Standard party runs 3 hours"
    assert "SUMMARY:Encore Esports Standard party" in body
    assert "LOCATION:Encore Esports\\, 538 Main St\\, 2nd floor (entrance on Division St)" in body
    assert f"Confirmation {booking.confirmation_code}. $444 due at the party." in body
    assert "+1914" not in body and "@example.com" not in body, "no contact details in the calendar file"


def test_calendar_ics_missing_or_cancelled_is_404(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    booking = make_booking(db, make_hold(db, slot, status="booked"), slot)
    booking.cancelled_at = booking.created_at
    db.commit()
    assert client().get(f"/api/bookings/{booking.confirmation_code}/calendar.ics").status_code == 404
    assert client().get("/api/bookings/AAAAAAAAAA/calendar.ics").status_code == 404
