from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Booking
from app.routes.holds import party_details
from app.schemas import BookingOut
from app.services.booking import CODE_ALPHABET, CODE_LENGTH

router = APIRouter()


@router.get("/api/bookings/{code}")
def get_booking(code: str, db: Session = Depends(get_db)) -> BookingOut:
    normalized = code.strip().upper()
    if len(normalized) != CODE_LENGTH or any(char not in CODE_ALPHABET for char in normalized):
        raise HTTPException(status_code=404, detail="Booking not found")
    booking = db.scalar(select(Booking).where(Booking.confirmation_code == normalized))
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return BookingOut(
        status="cancelled" if booking.cancelled_at else "booked",
        party=party_details(booking, booking.event_starts_at, booking.confirmation_code, booking.first_name),
    )
