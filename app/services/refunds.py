"""Deposit refunds.

The order matters and mirrors how the booking webhook treats notifications. The refund row is written and
committed inside the cancellation's transaction, *before* Square is called. Square is then called outside
that transaction and the row is settled with the answer.

That way a Square outage leaves a cancelled booking, a freed date and a refund row the owner can see and
retry, instead of a cancellation that silently did not happen or a refund nobody recorded. The idempotency
key is derived from the booking, so retrying that row can never refund the same deposit twice.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Booking, Refund
from app.services.square import SquareError, refund_payment

log = logging.getLogger(__name__)

REFUND_REASON = "Cancelled more than 7 days before the party"
# Square settles card refunds in days, not seconds. Saying so is the difference between a trusting parent
# and a phone call tomorrow.
SETTLEMENT_NOTICE = "Refunds take about 5 to 10 business days to appear on your statement."
# Booking and cancelling more than twice stops being bad luck: once a parent has two cancellations behind
# them, the third and everything after it queues for the owner instead of refunding itself.
REPEAT_CANCELLER_THRESHOLD = 2
# Advisory lock namespace for the daily cap. 1, 2 and 3 belong to the hold caps in services/holds.py.
LOCK_REFUND_CAP = 4


@dataclass(frozen=True)
class Decision:
    """Whether this refund goes to Square now, and if not, what to tell the owner it is waiting on."""

    status: str
    reason: str | None


def previous_cancellations(db: Session, booking: Booking) -> int:
    """How many bookings this parent has already cancelled, not counting the one being cancelled now.

    A parent is matched three ways and any match counts: the card's fingerprint as Square reports it, the
    normalized phone, and the email. Changing one of them is therefore not enough to reset the count, and a
    payment with no card fingerprint (a tender type that has none) still matches on contact details.
    """
    return db.execute(
        text(
            """
            SELECT count(*) FROM bookings
            WHERE cancelled_at IS NOT NULL AND id <> :id
              AND (phone = :phone OR email = :email
                   OR (card_fingerprint IS NOT NULL AND card_fingerprint = :fingerprint))
            """
        ),
        {
            "id": booking.id,
            "phone": booking.phone,
            "email": booking.email,
            "fingerprint": booking.card_fingerprint,
        },
    ).scalar_one()


def refunds_authorized_today(db: Session, today: date) -> int:
    """Refunds already authorized to go to Square today, in the venue's own timezone.

    Counted by created_at, not sent_at: the row is written inside the cancelling transaction while Square is
    called after it commits, so sent_at is still null at the moment this has to decide.
    """
    return db.execute(
        text(
            "SELECT count(*) FROM refunds"
            " WHERE status <> 'pending_approval'"
            " AND (created_at AT TIME ZONE 'America/New_York')::date = :today"
        ),
        {"today": today},
    ).scalar_one()


def refund_decision(db: Session, booking: Booking, today: date) -> Decision:
    """Whether this refund goes to Square now or waits for the owner.

    Both guards queue the refund rather than denying it: the parent is still cancelled, the date is still
    freed, and whether the money moves becomes a decision the owner makes with the facts in front of them.

    The day's count is taken under an advisory lock on the day itself, the same pattern the hold caps use.
    Without it two cancellations in flight would each read a count that does not include the other, and a
    cap of three would let four through.
    """
    if previous_cancellations(db, booking) >= REPEAT_CANCELLER_THRESHOLD:
        count = previous_cancellations(db, booking) + 1
        return Decision(
            status="pending_approval",
            reason=f"This card or contact has now cancelled {count} bookings. Approve the refund if it looks right.",
        )
    db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"),
        {"ns": LOCK_REFUND_CAP, "key": today.isoformat()},
    )
    cap = get_settings().refund_daily_cap
    authorized = refunds_authorized_today(db, today)
    if authorized >= cap:
        return Decision(
            status="pending_approval",
            reason=f"Daily automatic refund cap of {cap} already used today ({authorized} authorized).",
        )
    return Decision(status="pending", reason=None)


def idempotency_key_for(booking: Booking) -> str:
    """Stable per booking and inside Square's 45-character limit, so a retry refunds once."""
    return f"encore-refund-{booking.id}"


def record_refund(db: Session, booking: Booking, status: str, queued_reason: str | None = None) -> Refund:
    """Writes the intent to refund. Called inside the cancellation transaction; the caller commits."""
    refund = Refund(
        booking_id=booking.id,
        square_payment_id=booking.square_payment_id,
        idempotency_key=idempotency_key_for(booking),
        # The amount comes off the payment record, never off the request that asked for the refund.
        amount_cents=booking.paid_amount_cents,
        status=status,
        queued_reason=queued_reason,
    )
    db.add(refund)
    db.flush()
    return refund


def send_refund(db: Session, settings: Settings, refund_id: int) -> Refund | None:
    """Calls Square for a refund already recorded and committed. Never raises: a failure is written to the
    row and surfaced in admin, because the booking is already cancelled and the date already released."""
    refund = db.get(Refund, refund_id, populate_existing=True)
    if refund is None or refund.status not in ("pending", "pending_approval"):
        return refund
    try:
        result = refund_payment(
            settings, refund.square_payment_id, refund.amount_cents, refund.idempotency_key, REFUND_REASON
        )
    except SquareError as error:
        log.warning("Refund %s could not be sent: %s", refund.id, error)
        db.rollback()
        _settle(db, refund_id, status="failed", error=str(error)[:500])
        return db.get(Refund, refund_id, populate_existing=True)
    # Square answers PENDING for a refund it has accepted; its webhook moves the row to completed.
    status = {"COMPLETED": "completed", "REJECTED": "rejected", "FAILED": "failed"}.get(result.status, "pending")
    db.execute(
        text(
            "UPDATE refunds SET status = :status, square_refund_id = :refund_id, sent_at = now(),"
            " error = NULL, settled_at = CASE WHEN :status <> 'pending' THEN now() END WHERE id = :id"
        ),
        {"status": status, "refund_id": result.id, "id": refund_id},
    )
    db.commit()
    return db.get(Refund, refund_id, populate_existing=True)


def _settle(db: Session, refund_id: int, status: str, error: str | None) -> None:
    db.execute(
        text(
            "UPDATE refunds SET status = :status, error = :error,"
            " settled_at = CASE WHEN :status <> 'pending' THEN now() END WHERE id = :id"
        ),
        {"status": status, "error": error, "id": refund_id},
    )
    db.commit()


def settle_from_webhook(db: Session, square_refund_id: str, square_status: str, updated: datetime | None) -> int | None:
    """Square's refund webhook is the authority on whether the money actually moved. Returns the booking id
    the refund belongs to, or None when the refund is not one of ours."""
    refund = db.scalar(
        select(Refund).where(Refund.square_refund_id == square_refund_id).with_for_update()
    )
    if refund is None:
        return None
    status = {"COMPLETED": "completed", "REJECTED": "rejected", "FAILED": "failed"}.get(square_status, "pending")
    refund.status = status
    refund.settled_at = updated if status != "pending" else None
    if status == "completed":
        refund.error = None
    return refund.booking_id
