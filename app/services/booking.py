import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Booking, Hold, PaymentFlag, Slot, SquareEvent
from app.services.square import signature_valid

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 10

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


@dataclass(frozen=True)
class WebhookResult:
    status_code: int
    outcome: str
    hold_id: uuid.UUID | None = None
    booking_id: int | None = None


def new_confirmation_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


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
    return PaymentEvent(
        event_id=str(event["event_id"]),
        event_type=event_type,
        payment_id=str(payment["id"]),
        order_id=payment.get("order_id"),
        status=str(payment.get("status", "")),
        amount_cents=amount if isinstance(amount, int) else None,
        currency=money.get("currency"),
    )


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
        notes=hold.notes,
        square_payment_id=event.payment_id,
        square_order_id=event.order_id or "",
        paid_amount_cents=event.amount_cents or 0,
        event_starts_at=slot.starts_at,
    )
    hold.status = "booked"
    db.add(booking)
    db.flush()
    return booking


def _apply_payment(db: Session, event: PaymentEvent) -> WebhookResult:
    recorded, recorded_hold = _recorded_hold(db, event.payment_id)
    if recorded:
        return WebhookResult(200, "duplicate", recorded_hold)
    hold = None
    if event.order_id:
        hold = db.scalars(
            select(Hold)
            .where(Hold.square_order_id == event.order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
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
    event = parse_payment_event(body)
    if event is None or event.status != "COMPLETED":
        return WebhookResult(200, "ignored")
    return process_payment_event(db, event)
