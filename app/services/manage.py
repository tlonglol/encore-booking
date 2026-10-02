"""The no-login manage page: finding a booking by its token, and deciding what its parent may still do.

Every rule here is computed from the database row on the request that asks, never from anything the page
sends. The page renders what it is told; it is not trusted to have told the truth about the party's date.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Booking
from app.timeutil import VENUE_TZ

# The live site's rule: free cancel or reschedule up to a week before the party.
FREE_WINDOW_DAYS = 7
# A token is a fixed-length url-safe secret; anything else is not worth a database round trip.
MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 128


def find_booking(db: Session, token: str) -> Booking | None:
    """A cancelled booking still resolves: the parent is owed a page explaining what happened to it."""
    if not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH:
        return None
    return db.scalar(select(Booking).where(Booking.manage_token == token))


def party_date(booking: Booking) -> date:
    return booking.event_starts_at.astimezone(VENUE_TZ).date()


def free_window_opens(booking: Booking) -> date:
    """The last day the free window is still open, read off the party's current date."""
    return party_date(booking) - timedelta(days=FREE_WINDOW_DAYS)


def inside_free_window(booking: Booking, today: date) -> bool:
    """True while the party is a week or more away, which is what makes a cancellation refundable."""
    return (party_date(booking) - today).days >= FREE_WINDOW_DAYS


def addons_close_at(booking: Booking, cutoff_hours: int) -> datetime:
    return booking.event_starts_at - timedelta(hours=cutoff_hours)
