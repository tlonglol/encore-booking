import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Booking, Hold, PaymentFlag, Slot, SquareEvent
from app.services.refunds import settle_from_webhook
from app.services.square import signature_valid

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 10
# Square answers PENDING while a card refund is in flight; these are the words that end the story.
TERMINAL_REFUND_STATUSES = ("COMPLETED", "FAILED", "REJECTED")
# 24 bytes: long enough that the manage page needs no login, short enough to sit in an SMS.
MANAGE_TOKEN_BYTES = 24

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentEvent:
    event_id: str
    event_type: str
    payment_id: str
    order_id: str | None
    status: str
    amount_cents: int | None
    currency: str | None
    # Square's identifier for the card, at payment.card_details.card.fingerprint. Absent for other tender
    # types, which is why every guard that uses it falls back to the phone and the email.
    card_fingerprint: str | None = None
    # The hold id, set by the pay endpoint on CreatePayment. A deposit taken through the Payments API has
    # no order, so this is the only thing tying the payment back to a hold.
    reference_id: str | None = None


@dataclass(frozen=True)
class RefundEvent:
    event_id: str
    event_type: str
    refund_id: str
    payment_id: str | None
    status: str
    updated_at: datetime | None


@dataclass(frozen=True)
class WebhookResult:
    status_code: int
    outcome: str
    hold_id: uuid.UUID | None = None
    booking_id: int | None = None
    # Set for a refund that settled, so the caller knows there is a parent to tell.
    refund_booking_id: int | None = None


def new_confirmation_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def new_manage_token() -> str:
    return secrets.token_urlsafe(MANAGE_TOKEN_BYTES)


def parse_payment_event(event: Any) -> PaymentEvent | None:
    """Returns None for anything that is not a payment event carrying a payment object."""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    payment = ((event.get("data") or {}).get("object") or {}).get("payment")
    if not isinstance(event_type, str) or not event_type.startswith("payment."):
        return None
    if not isinstance(payment, dict) or not event.get("event_id") or not payment.get("id"):
        return None
    money = payment.get("amount_money") or {}
    amount = money.get("amount")
    card = ((payment.get("card_details") or {}).get("card") or {}).get("fingerprint")
    return PaymentEvent(
        event_id=str(event["event_id"]),
        event_type=event_type,
        payment_id=str(payment["id"]),
        order_id=payment.get("order_id"),
        status=str(payment.get("status", "")),
        amount_cents=amount if isinstance(amount, int) else None,
        currency=money.get("currency"),
        card_fingerprint=card if isinstance(card, str) and card else None,
        reference_id=payment.get("reference_id") or None,
    )


def parse_refund_event(event: Any) -> RefundEvent | None:
    """Square nests the refund at data.object.refund on refund.created and refund.updated (docs 2026-10-02)."""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    refund = ((event.get("data") or {}).get("object") or {}).get("refund")
    if not isinstance(event_type, str) or not event_type.startswith("refund."):
        return None
    if not isinstance(refund, dict) or not event.get("event_id") or not refund.get("id"):
        return None
    return RefundEvent(
        event_id=str(event["event_id"]),
        event_type=event_type,
        refund_id=str(refund["id"]),
        payment_id=refund.get("payment_id"),
        status=str(refund.get("status", "")),
        updated_at=_parse_time(refund.get("updated_at")),
    )


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def process_refund_event(db: Session, event: RefundEvent) -> WebhookResult:
    """Same shape as a payment event: claim the event id first, then act, then commit. Square's refund
    webhook is the authority on whether the money actually left the account."""
    claimed = db.execute(
        insert(SquareEvent)
        .values(
            event_id=event.event_id,
            event_type=event.event_type,
            payment_id=event.payment_id,
            refund_id=event.refund_id,
            outcome="duplicate",
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(SquareEvent.event_id)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        return WebhookResult(200, "duplicate")
    booking_id = settle_from_webhook(db, event.refund_id, event.status, event.updated_at)
    outcome = "refund_settled" if booking_id is not None else "refund_unmatched"
    db.execute(
        text("UPDATE square_events SET outcome = :outcome WHERE event_id = :id"),
        {"outcome": outcome, "id": event.event_id},
    )
    db.commit()
    log.info("Square refund event %s outcome=%s status=%s", event.event_id, outcome, event.status)
    settled = event.status in TERMINAL_REFUND_STATUSES
    return WebhookResult(200, outcome, refund_booking_id=booking_id if settled else None)


def _recorded_hold(db: Session, payment_id: str) -> tuple[bool, uuid.UUID | None]:
    """Whether this payment already became a booking or a flag, and the hold it belongs to."""
    row = db.execute(
        text(
            "SELECT hold_id FROM bookings WHERE square_payment_id = :id"
            " UNION ALL SELECT hold_id FROM payment_flags WHERE square_payment_id = :id LIMIT 1"
        ),
        {"id": payment_id},
    ).one_or_none()
    return (row is not None, row.hold_id if row is not None else None)


def _flag(db: Session, event: PaymentEvent, hold_id: uuid.UUID | None, kind: str) -> None:
    db.execute(
        insert(PaymentFlag)
        .values(
            hold_id=hold_id,
            square_payment_id=event.payment_id,
            square_order_id=event.order_id,
            amount_cents=event.amount_cents,
            kind=kind,
        )
        .on_conflict_do_nothing(index_elements=["square_payment_id"])
    )


def _book(db: Session, hold: Hold, event: PaymentEvent) -> Booking:
    slot = db.get(Slot, hold.slot_id)
    assert slot is not None
    booking = Booking(
        hold_id=hold.id,
        slot_id=hold.slot_id,
        confirmation_code=new_confirmation_code(),
        manage_token=new_manage_token(),
        package=hold.package,
        kids=hold.kids,
        addons=hold.addons,
        line_items=hold.line_items,
        total_cents=hold.total_cents,
        deposit_cents=hold.deposit_cents,
        first_name=hold.first_name,
        last_name=hold.last_name,
        phone=hold.phone,
        email=hold.email,
        kid_name=hold.kid_name,
        kid_age=hold.kid_age,
        birthday=hold.birthday,
        notes=hold.notes,
        square_payment_id=event.payment_id,
        square_order_id=event.order_id or "",
        card_fingerprint=event.card_fingerprint,
        paid_amount_cents=event.amount_cents or 0,
        event_starts_at=slot.starts_at,
    )
    hold.status = "booked"
    db.add(booking)
    db.flush()
    return booking


def _find_hold(db: Session, event: PaymentEvent) -> Hold | None:
    """By reference_id first, then order_id, and always FOR UPDATE.

    The row lock is what serializes a synchronous pay request against Square's webhook for the same
    payment: whichever gets the lock first commits, and the other re-checks under it and sees a
    duplicate. Payments API deposits carry a reference; payment-link payments carry an order; a booking
    made before this change has only the order, so both lookups stay.
    """
    locked = lambda clause: db.scalars(  # noqa: E731
        select(Hold).where(clause).with_for_update().execution_options(populate_existing=True)
    ).one_or_none()
    if event.reference_id:
        try:
            return locked(Hold.id == uuid.UUID(event.reference_id))
        except ValueError:
            pass
    if event.order_id:
        return locked(Hold.square_order_id == event.order_id)
    return None


def book_payment(db: Session, event: PaymentEvent) -> WebhookResult:
    """The synchronous path: Square has already taken the money, now record it.

    Deliberately the same `_apply_payment` the webhook uses, rather than a second implementation that
    would drift. It keys on square_payment_id, so this and the webhook are idempotent against each other
    whichever order they arrive in. No square_events row is claimed here: that ledger is Square's
    deliveries, and this was not one.
    """
    result = _apply_payment(db, event)
    db.commit()
    log.info("Square payment %s outcome=%s (synchronous)", event.payment_id, result.outcome)
    return result


def _apply_payment(db: Session, event: PaymentEvent) -> WebhookResult:
    recorded, recorded_hold = _recorded_hold(db, event.payment_id)
    if recorded:
        return WebhookResult(200, "duplicate", recorded_hold)
    hold = _find_hold(db, event)
    if hold is None:
        _flag(db, event, None, "unmatched_order")
        return WebhookResult(200, "unmatched")
    # Re-check under the hold lock: payment.created and payment.updated for one payment can arrive together.
    if _recorded_hold(db, event.payment_id)[0]:
        return WebhookResult(200, "duplicate", hold.id)
    if db.scalar(select(Booking.id).where(Booking.hold_id == hold.id)) is not None:
        _flag(db, event, hold.id, "duplicate_payment")
        return WebhookResult(200, "duplicate_payment", hold.id)
    if event.amount_cents != hold.deposit_cents or event.currency != "USD":
        _flag(db, event, hold.id, "amount_mismatch")
        return WebhookResult(200, "amount_mismatch", hold.id)
    live = hold.status == "pending_payment" and db.scalar(select(func.now() < hold.expires_at))
    if not live:
        hold.status = "paid_after_expiry"
        _flag(db, event, hold.id, "paid_after_expiry")
        return WebhookResult(200, "expired_hold", hold.id)
    booking = _book(db, hold, event)
    return WebhookResult(200, "booked", hold.id, booking.id)


def process_payment_event(db: Session, event: PaymentEvent) -> WebhookResult:
    """One transaction. Claiming the event id first means a concurrent redelivery waits here, then sees a duplicate."""
    claimed = db.execute(
        insert(SquareEvent)
        .values(
            event_id=event.event_id,
            event_type=event.event_type,
            payment_id=event.payment_id,
            order_id=event.order_id,
            outcome="duplicate",
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(SquareEvent.event_id)
    ).scalar_one_or_none()
    if claimed is None:
        db.rollback()
        return WebhookResult(200, "duplicate", _recorded_hold(db, event.payment_id)[1])
    result = _apply_payment(db, event)
    db.execute(
        text("UPDATE square_events SET outcome = :outcome WHERE event_id = :id"),
        {"outcome": result.outcome, "id": event.event_id},
    )
    db.commit()
    log.info("Square event %s outcome=%s", event.event_id, result.outcome)
    return result


def handle_square_webhook(db: Session, settings: Settings, raw_body: bytes, signature: str | None) -> WebhookResult:
    """Verify, then parse, then act. Nothing in the body is read before the signature checks out."""
    if not signature_valid(
        raw_body, signature, settings.square_webhook_signature_key, settings.square_webhook_notification_url
    ):
        return WebhookResult(401, "bad_signature")
    try:
        body = json.loads(raw_body)
    except ValueError:
        return WebhookResult(400, "malformed")
    refund = parse_refund_event(body)
    if refund is not None:
        return process_refund_event(db, refund)
    event = parse_payment_event(body)
    if event is None or event.status != "COMPLETED":
        return WebhookResult(200, "ignored")
    return process_payment_event(db, event)
