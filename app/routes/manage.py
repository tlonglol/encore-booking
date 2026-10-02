from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Booking
from app.routes.holds import party_details
from app.schemas import ManageOut
from app.services.manage import find_booking, free_window_opens, inside_free_window
from app.timeutil import format_date, venue_today

router = APIRouter(prefix="/api/manage")


def _require_booking(db: Session, token: str) -> Booking:
    booking = find_booking(db, token)
    if booking is None:
        raise HTTPException(status_code=404, detail="We couldn't find that booking. Check the link we texted you.")
    return booking


def manage_view(booking: Booking, today: object) -> ManageOut:
    free_until = free_window_opens(booking)
    return ManageOut(
        status="cancelled" if booking.cancelled_at else "booked",
        party=party_details(
            booking, booking.event_starts_at, booking.confirmation_code, booking.first_name, booking.manage_token
        ),
        free_until=free_until,
        free_until_label=format_date(free_until),
        inside_free_window=booking.cancelled_at is None and inside_free_window(booking, today),  # type: ignore[arg-type]
    )


@router.get("/{token}")
def get_manage(token: str, db: Session = Depends(get_db)) -> ManageOut:
    """Everything the manage page renders, recomputed from the row on every load."""
    return manage_view(_require_booking(db, token), venue_today(datetime.now(UTC)))
