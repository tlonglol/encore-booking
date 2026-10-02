"""The no-login manage page: finding a booking by its token, and what its parent may still do with it.

Every rule here is computed from the database row on the request that asks, never from anything the page
sends. The page renders what it is told; it is not trusted to have told the truth about the party's date.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from psycopg.errors import UniqueViolation
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking
from app.pricing import ADDONS, Quote, quote
from app.services.refunds import record_refund, refund_decision
from app.timeutil import VENUE_TZ, first_bookable_date

# The live site's rule: free cancel or reschedule up to a week before the party.
FREE_WINDOW_DAYS = 7
# A token is a fixed-length url-safe secret; anything else is not worth a database round trip.
MIN_TOKEN_LENGTH = 16
MAX_TOKEN_LENGTH = 128
# The two indexes that between them make a slot exclusive. A move has to satisfy both.
SLOT_INDEXES = ("holds_one_active_per_slot", "bookings_one_live_per_slot")


class BookingGone(Exception):
    """Already cancelled: nothing to cancel, move or add to."""


class NotEligible(Exception):
    """A rule the parent cannot override: the courtesy move is spent, or add-ons have closed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NeedsConfirmation(Exception):
    """Cancelling inside the free window forfeits the deposit, so it takes a deliberate second answer."""


class SlotTaken(Exception):
    """Someone else holds or has booked the date the parent picked. The booking is untouched."""


class SlotNotBookable(Exception):
    """Blocked, unknown, or inside the lead-time window: the same rules a first booking obeys."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Cancelled:
    booking_id: int
    refund_id: int | None
    refund_queued: bool
    queued_reason: str | None


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


def addons_open(booking: Booking, cutoff_hours: int, now: datetime) -> bool:
    return now < addons_close_at(booking, cutoff_hours)


def refundable_through_square(booking: Booking) -> bool:
    """Whether there is a real Square payment behind this booking to send a refund against.

    A manual booking's payment id is synthetic, so handing it to Square would be a guaranteed failure
    dressed up as a pending refund. The owner took that deposit in person (or waived it) and gives it
    back the same way.
    """
    return booking.origin == "online" and booking.paid_amount_cents > 0


def can_reschedule(booking: Booking, today: date) -> bool:
    """Free and unlimited while the party is a week or more away; inside that, the one courtesy move."""
    return inside_free_window(booking, today) or not booking.courtesy_reschedule_used


def _lock_live_booking(db: Session, booking_id: int) -> Booking:
    """Takes the row lock every write below shares, so two clicks cannot both pass the same check."""
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if booking is None or booking.cancelled_at is not None:
        db.rollback()
        raise BookingGone()
    return booking


def cancel_booking(db: Session, booking_id: int, today: date, forfeit_deposit: bool) -> Cancelled:
    """One transaction: cancel, free the date, and record the refund the caller then sends to Square.

    Inside the free window the deposit is refundable and a refund row is written. Outside it nothing is
    refunded, and the parent has to say so explicitly, because forfeiting $100 is not a thing to do on a
    mis-tap. Either way the date goes back on the grid: an unsold Saturday helps nobody.
    """
    booking = _lock_live_booking(db, booking_id)
    refundable = inside_free_window(booking, today) and refundable_through_square(booking)
    if not refundable and not forfeit_deposit:
        db.rollback()
        raise NeedsConfirmation()

    refund = None
    if refundable:
        decision = refund_decision(db, booking, today)
        refund = record_refund(db, booking, decision.status, decision.reason)
    booking.cancelled_at = func.now()
    booking.cancel_source = "parent"
    booking.cancel_reason = "Cancelled by the parent from the manage page"
    # The hold shares the slot-exclusivity index with the booking, so both have to let go of the date.
    db.execute(
        text("UPDATE holds SET status = 'cancelled', updated_at = now() WHERE id = :hold_id"),
        {"hold_id": booking.hold_id},
    )
    db.commit()
    db.refresh(booking)
    return Cancelled(
        booking_id=booking.id,
        refund_id=refund.id if refund else None,
        refund_queued=refund is not None and refund.status == "pending_approval",
        queued_reason=refund.queued_reason if refund else None,
    )


def reschedule_booking(db: Session, booking_id: int, slot_id: int, today: date, min_lead_days: int) -> Booking:
    """Moves the party to another open weekend in one transaction, touching no money at all.

    The booking and its hold both carry the slot, and between them they are what makes a slot exclusive, so
    both move together: the old date is free the moment this commits and the new one is claimed by the same
    unique indexes that decide a first booking. Someone else getting there first is a 409, not a half-move.
    """
    booking = _lock_live_booking(db, booking_id)
    if not can_reschedule(booking, today):
        db.rollback()
        raise NotEligible(
            "Your party is less than a week away and the courtesy move has been used. Call (646) 203-8087."
        )
    spends_courtesy = not inside_free_window(booking, today)

    slot = db.execute(
        text("SELECT id, local_date, starts_at, blocked_at FROM slots WHERE id = :id FOR SHARE"), {"id": slot_id}
    ).one_or_none()
    if slot is None:
        db.rollback()
        raise SlotNotBookable("That date is not available.")
    if slot.id == booking.slot_id:
        db.rollback()
        raise SlotNotBookable("That is already your party date.")
    if slot.local_date < first_bookable_date(today, min_lead_days):
        db.rollback()
        raise SlotNotBookable("That date is too soon to move to. Call (646) 203-8087.")
    if slot.blocked_at is not None:
        db.rollback()
        raise SlotTaken()

    # A lapsed hold on the new date is not a claim on it, exactly as it is not when a first hold is made.
    db.execute(
        text(
            "UPDATE holds SET status = 'expired', updated_at = now()"
            " WHERE slot_id = :id AND status = 'pending_payment' AND expires_at <= now()"
        ),
        {"id": slot_id},
    )
    booking.slot_id = slot.id
    booking.event_starts_at = slot.starts_at
    booking.reschedule_count += 1
    booking.rescheduled_at = func.now()
    if spends_courtesy:
        booking.courtesy_reschedule_used = True
    try:
        # Both claims are made inside one try: the booking's index is checked when this flush writes the
        # row, the hold's when the statement after it does, and either one losing means the date was taken.
        db.flush()
        db.execute(
            text("UPDATE holds SET slot_id = :slot_id, updated_at = now() WHERE id = :hold_id"),
            {"slot_id": slot.id, "hold_id": booking.hold_id},
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if isinstance(error.orig, UniqueViolation) and error.orig.diag.constraint_name in SLOT_INDEXES:
            raise SlotTaken() from error
        raise
    db.refresh(booking)
    return booking


def merge_additions(current: dict[str, int], requested: dict[str, int]) -> dict[str, int]:
    """Additions only: every quantity is the larger of the two, so nothing already paid for can be removed
    by a request that simply leaves it out. The client's idea of the diff is never trusted."""
    merged = dict(current)
    for key, quantity in requested.items():
        if key not in ADDONS:
            raise NotEligible(f"Unknown add-on: {key}")
        merged[key] = max(merged.get(key, 0), quantity)
    return merged


def add_to_booking(
    db: Session,
    booking_id: int,
    addons: dict[str, int],
    kids: int,
    cutoff_hours: int,
    now: datetime,
    notes: str | None = None,
) -> tuple[Booking, Quote]:
    """Re-prices the party with the additions through the same quote() everything else uses.

    The deposit never moves: the whole increase lands in what is due at the party. Nothing here talks to
    Square, because nothing here charges anything.

    `notes`, like the add-ons, can only be replaced under the same cutoff; unlike them, it is not an
    addition that has to grow, so a notes-only edit (nothing added, headcount unchanged) is a real change
    rather than the no-op the guard below would otherwise call it. `None` means the caller never mentioned
    notes at all and the stored value is left untouched; a caller that means to clear them sends "".
    """
    booking = _lock_live_booking(db, booking_id)
    if not addons_open(booking, cutoff_hours, now):
        db.rollback()
        raise NotEligible(
            "Your party is too close for us to change the order. Call (646) 203-8087 and we'll see what we can do."
        )
    if kids < booking.kids:
        db.rollback()
        raise NotEligible("Headcount can only go up here. To reduce it, call (646) 203-8087.")
    merged = merge_additions(booking.addons, addons)
    new_notes = notes.strip() or None if notes is not None else booking.notes
    notes_changed = notes is not None and new_notes != booking.notes
    if merged == booking.addons and kids == booking.kids and not notes_changed:
        db.rollback()
        raise NotEligible("Nothing was added.")

    priced = quote(booking.package, kids, merged)
    booking.kids = priced.kids
    booking.addons = priced.addons
    booking.line_items = priced.line_items_json()
    booking.total_cents = priced.total_cents
    booking.notes = new_notes
    db.commit()
    db.refresh(booking)
    return booking, priced
