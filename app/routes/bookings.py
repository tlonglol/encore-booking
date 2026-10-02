from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Booking
from app.pricing import PACKAGES
from app.routes.holds import party_details
from app.schemas import BookingOut
from app.services.booking import CODE_ALPHABET, CODE_LENGTH

router = APIRouter()

ICS_LOCATION = "Encore Esports, 538 Main St, 2nd floor (entrance on Division St), New Rochelle, NY"


def _find_booking(db: Session, code: str) -> Booking | None:
    normalized = code.strip().upper()
    if len(normalized) != CODE_LENGTH or any(char not in CODE_ALPHABET for char in normalized):
        return None
    return db.scalar(select(Booking).where(Booking.confirmation_code == normalized))


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def booking_ics(booking: Booking) -> str:
    """One VEVENT in UTC; the calendar app renders it in the parent's timezone."""
    hours = PACKAGES[booking.package].hours + (1 if "extra_hour" in booking.addons else 0)
    start = booking.event_starts_at.astimezone(UTC)
    stamp = "{:%Y%m%dT%H%M%SZ}"
    balance = (booking.total_cents - booking.deposit_cents) // 100
    description = (
        f"Confirmation {booking.confirmation_code}. ${balance} due at the party."
        f" Questions: (646) 203-8087."
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Encore Esports//Party Booking//EN",
        "BEGIN:VEVENT",
        f"UID:{booking.confirmation_code}@book.encoregaming.us",
        f"DTSTAMP:{stamp.format(booking.created_at.astimezone(UTC))}",
        f"DTSTART:{stamp.format(start)}",
        f"DTEND:{stamp.format(start + timedelta(hours=hours))}",
        f"SUMMARY:{_ics_escape(f'Encore Esports {PACKAGES[booking.package].label} party')}",
        f"LOCATION:{_ics_escape(ICS_LOCATION)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


@router.get("/api/bookings/{code}/calendar.ics")
def get_booking_ics(code: str, db: Session = Depends(get_db)) -> Response:
    booking = _find_booking(db, code)
    if booking is None or booking.cancelled_at is not None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return Response(
        content=booking_ics(booking),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="encore-party.ics"'},
    )


@router.get("/api/bookings/{code}")
def get_booking(code: str, db: Session = Depends(get_db)) -> BookingOut:
    booking = _find_booking(db, code)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return BookingOut(
        status="cancelled" if booking.cancelled_at else "booked",
        party=party_details(
            booking, booking.event_starts_at, booking.confirmation_code, booking.first_name, booking.manage_token
        ),
    )
