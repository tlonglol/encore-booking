from sqlalchemy.orm import Session

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
    assert f"DTSTART:{day:%Y%m%d}T140000Z" in body, "10:00 AM New York is 14:00 UTC under EDT"
    assert f"DTEND:{day:%Y%m%d}T170000Z" in body, "a Standard party runs 3 hours"
    assert "SUMMARY:Encore Esports Standard party" in body
    assert "LOCATION:Encore Esports\\, 538 Main St\\, 2nd floor (entrance on Division St)" in body
    assert f"Confirmation {booking.confirmation_code}. Balance $444 due day of party." in body
    assert "+1914" not in body and "@example.com" not in body, "no contact details in the calendar file"


def test_calendar_ics_missing_or_cancelled_is_404(db: Session) -> None:
    slot = make_slot(db, next_saturday(7))
    booking = make_booking(db, make_hold(db, slot, status="booked"), slot)
    booking.cancelled_at = booking.created_at
    db.commit()
    assert client().get(f"/api/bookings/{booking.confirmation_code}/calendar.ics").status_code == 404
    assert client().get("/api/bookings/AAAAAAAAAA/calendar.ics").status_code == 404
