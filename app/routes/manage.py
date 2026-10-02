from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Booking
from app.notifications import addons_summary, notify_owner, owner_addons_text, owner_cancelled_text, owner_rescheduled_text
from app.pricing import PricingError
from app.routes.holds import party_details
from app.schemas import AddonsIn, CancelIn, CancelOut, ManageOut, ManageWhatYouCanDo, RescheduleIn
from app.services.manage import (
    BookingGone,
    NeedsConfirmation,
    NotEligible,
    SlotNotBookable,
    SlotTaken,
    add_to_booking,
    addons_close_at,
    addons_open,
    can_reschedule,
    cancel_booking,
    find_booking,
    free_window_opens,
    inside_free_window,
    reschedule_booking,
)
from app.services.refunds import SETTLEMENT_NOTICE, send_refund
from app.timeutil import format_date, format_day, format_time, venue_today

router = APIRouter(prefix="/api/manage")

ADDONS_CLOSED = "Your party is too close for us to change the order. Call (646) 203-8087."
COURTESY_SPENT = "Your party is less than a week away and the courtesy move has been used. Call (646) 203-8087."
NO_REFUND_INSIDE_WINDOW = "Your party is less than a week away, so the deposit is no longer refundable."


def _require_booking(db: Session, token: str) -> Booking:
    booking = find_booking(db, token)
    if booking is None:
        raise HTTPException(status_code=404, detail="We couldn't find that booking. Check the link we texted you.")
    return booking


def _require_live_booking(db: Session, token: str) -> Booking:
    booking = _require_booking(db, token)
    if booking.cancelled_at is not None:
        raise HTTPException(status_code=409, detail="This booking was already cancelled.")
    return booking


def what_you_can_do(booking: Booking, today: date, now: datetime, cutoff_hours: int) -> ManageWhatYouCanDo:
    """Decided here, on every load, from the row. The page renders these and never works them out itself."""
    if booking.cancelled_at is not None:
        return ManageWhatYouCanDo(
            can_cancel=False,
            can_reschedule=False,
            can_add_addons=False,
            refund_on_cancel=False,
            cancel_reason="This booking was cancelled.",
            reschedule_reason="This booking was cancelled.",
            addons_reason="This booking was cancelled.",
            addons_close_label=format_day(addons_close_at(booking, cutoff_hours)),
        )
    refundable = inside_free_window(booking, today)
    addons_still_open = addons_open(booking, cutoff_hours, now)
    reschedulable = can_reschedule(booking, today)
    return ManageWhatYouCanDo(
        can_cancel=True,
        can_reschedule=reschedulable,
        can_add_addons=addons_still_open,
        refund_on_cancel=refundable,
        cancel_reason=None if refundable else NO_REFUND_INSIDE_WINDOW,
        reschedule_reason=None if reschedulable else COURTESY_SPENT,
        addons_reason=None if addons_still_open else ADDONS_CLOSED,
        addons_close_label=format_day(addons_close_at(booking, cutoff_hours)),
    )


def manage_view(booking: Booking, today: date, now: datetime, cutoff_hours: int) -> ManageOut:
    free_until = free_window_opens(booking)
    return ManageOut(
        status="cancelled" if booking.cancelled_at else "booked",
        party=party_details(
            booking, booking.event_starts_at, booking.confirmation_code, booking.first_name, booking.manage_token
        ),
        free_until=free_until,
        free_until_label=format_date(free_until),
        inside_free_window=booking.cancelled_at is None and inside_free_window(booking, today),
        can=what_you_can_do(booking, today, now, cutoff_hours),
    )


def _view(booking: Booking) -> ManageOut:
    now = datetime.now(UTC)
    return manage_view(booking, venue_today(now), now, get_settings().addon_cutoff_hours)


@router.get("/{token}")
def get_manage(token: str, db: Session = Depends(get_db)) -> ManageOut:
    """Everything the manage page renders, recomputed from the row on every load."""
    return _view(_require_booking(db, token))


@router.post("/{token}/cancel")
def post_cancel(token: str, body: CancelIn, db: Session = Depends(get_db)) -> Any:
    """Cancel, free the date, and refund the deposit if the party is still a week or more away.

    The refund is recorded inside the cancelling transaction and sent to Square after it commits, so a
    Square outage leaves a visible refund to retry rather than a cancellation that half happened.
    """
    settings = get_settings()
    booking = _require_live_booking(db, token)
    try:
        result = cancel_booking(db, booking.id, venue_today(datetime.now(UTC)), body.forfeit_deposit)
    except BookingGone:
        raise HTTPException(status_code=409, detail="This booking was already cancelled.") from None
    except NeedsConfirmation:
        return JSONResponse(
            status_code=409,
            content={
                "detail": NO_REFUND_INSIDE_WINDOW,
                "needs_confirmation": True,
                "offer_reschedule": can_reschedule(booking, venue_today(datetime.now(UTC))),
            },
        )
    # A queued refund is not sent: the owner approves it in admin. Everything else goes to Square now.
    refund = None if result.refund_queued else (send_refund(db, settings, result.refund_id) if result.refund_id else None)
    db.refresh(booking)
    notify_owner(
        db,
        settings,
        booking,
        owner_cancelled_text(
            booking, refunded=refund is not None, queued=result.refund_queued, why=result.queued_reason
        ),
        event="cancelled",
    )
    return CancelOut(
        status="cancelled",
        refunded=refund is not None and refund.status in ("pending", "completed"),
        refund_queued=result.refund_queued,
        amount_cents=(refund.amount_cents if refund else booking.paid_amount_cents if result.refund_id else 0),
        message=_cancel_message(refund, result.refund_queued),
        settlement_notice=SETTLEMENT_NOTICE,
    )


def _cancel_message(refund: Any, queued: bool) -> str:
    if refund is None:
        return "Your party is cancelled and the date is open again. The deposit was inside the 7-day window, so it stays with Encore."
    if queued:
        return "Your party is cancelled and the date is open again. Your refund is being reviewed and we'll confirm it shortly."
    if refund.status == "failed":
        return "Your party is cancelled and the date is open again. We hit a problem sending the refund; Encore will finish it by hand and call you."
    return "Your party is cancelled and the date is open again. Your $100 deposit is on its way back to your card."


@router.post("/{token}/reschedule")
def post_reschedule(token: str, body: RescheduleIn, db: Session = Depends(get_db)) -> Any:
    """Moves the party to another open weekend. No Square call: the deposit simply comes along."""
    settings = get_settings()
    booking = _require_live_booking(db, token)
    moved_from = booking.event_starts_at
    try:
        reschedule_booking(db, booking.id, body.slot_id, venue_today(datetime.now(UTC)), settings.min_lead_days)
    except BookingGone:
        raise HTTPException(status_code=409, detail="This booking was already cancelled.") from None
    except NotEligible as error:
        raise HTTPException(status_code=409, detail=error.message) from error
    except SlotNotBookable as error:
        raise HTTPException(status_code=422, detail=error.message) from error
    except SlotTaken:
        return JSONResponse(
            status_code=409,
            content={"detail": "That date was just taken. Your party has not moved; pick another date."},
        )
    db.refresh(booking)
    notify_owner(db, settings, booking, owner_rescheduled_text(booking, moved_from), event="rescheduled")
    return _view(booking)


@router.post("/{token}/addons")
def post_addons(token: str, body: AddonsIn, db: Session = Depends(get_db)) -> Any:
    """Additions only, re-priced by the same function the funnel uses. The deposit never changes: the whole
    increase lands in what is due at the party."""
    settings = get_settings()
    booking = _require_live_booking(db, token)
    before = dict(booking.addons)
    before_kids = booking.kids
    now = datetime.now(UTC)
    try:
        add_to_booking(db, booking.id, body.addons, body.kids, settings.addon_cutoff_hours, now)
    except BookingGone:
        raise HTTPException(status_code=409, detail="This booking was already cancelled.") from None
    except NotEligible as error:
        raise HTTPException(status_code=409, detail=error.message) from error
    except PricingError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.refresh(booking)
    notify_owner(
        db, settings, booking, owner_addons_text(booking, _what_changed(before, before_kids, booking)), event="addons_changed"
    )
    return _view(booking)


def _what_changed(before: dict[str, int], before_kids: int, booking: Booking) -> str:
    """What the owner actually has to buy: the difference, not the whole order."""
    added = {
        key: quantity - before.get(key, 0)
        for key, quantity in booking.addons.items()
        if quantity > before.get(key, 0)
    }
    parts = [addons_summary(added)] if added else []
    if booking.kids > before_kids:
        parts.append(f"{booking.kids - before_kids} more kids (now {booking.kids})")
    return ", ".join(parts) or "nothing"
