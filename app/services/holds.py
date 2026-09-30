import ipaddress
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import phonenumbers
from psycopg.errors import UniqueViolation
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking, Hold, Slot
from app.pricing import quote
from app.timeutil import first_bookable_date

HOLD_MINUTES = 15
MAX_ACTIVE_HOLDS_PER_IP = 5
SLOT_INDEX = "holds_one_active_per_slot"

# Advisory lock namespaces, always taken in this order so two hold requests cannot deadlock.
LOCK_PHONE, LOCK_EMAIL, LOCK_IP = 1, 2, 3


class SlotNotFound(Exception):
    pass


class SlotTooSoon(Exception):
    pass


class SlotUnavailable(Exception):
    pass


class HoldLimitReached(Exception):
    def __init__(self, reason: str, expires_at: datetime) -> None:
        super().__init__(reason)
        self.expires_at = expires_at


@dataclass(frozen=True)
class HoldInput:
    slot_id: int
    package: str
    kids: int
    addons: dict[str, int]
    first_name: str
    last_name: str
    phone: str
    email: str
    notes: str | None
    client_ip: str | None


def normalize_phone(raw: str) -> str:
    """US numbers to E.164. Raises ValueError for anything that is not a valid number."""
    try:
        parsed = phonenumbers.parse(raw, "US")
    except phonenumbers.NumberParseException as error:
        raise ValueError("Enter a valid phone number") from error
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Enter a valid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_ip(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def _lock_identities(db: Session, phone: str, email: str, client_ip: str | None) -> None:
    """Serializes concurrent holds for the same phone, email, or IP so the cap check below cannot be raced."""
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_PHONE, "key": phone})
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_EMAIL, "key": email})
    if client_ip:
        db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_IP, "key": client_ip})


def _enforce_caps(db: Session, phone: str, email: str, client_ip: str | None) -> None:
    row = db.execute(
        text(
            """
            SELECT max(expires_at) FILTER (WHERE phone = :phone) AS phone_expiry,
                   max(expires_at) FILTER (WHERE email = :email) AS email_expiry,
                   count(*) FILTER (WHERE client_ip = CAST(:ip AS inet)) AS ip_count,
                   min(expires_at) FILTER (WHERE client_ip = CAST(:ip AS inet)) AS ip_expiry
            FROM holds
            WHERE status = 'pending_payment' AND expires_at > now()
              AND (phone = :phone OR email = :email OR client_ip = CAST(:ip AS inet))
            """
        ),
        {"phone": phone, "email": email, "ip": client_ip},
    ).one()
    if row.phone_expiry is not None:
        raise HoldLimitReached("This phone number already has a date on hold.", row.phone_expiry)
    if row.email_expiry is not None:
        raise HoldLimitReached("This email already has a date on hold.", row.email_expiry)
    if row.ip_count >= MAX_ACTIVE_HOLDS_PER_IP:
        raise HoldLimitReached("Too many dates on hold from this network.", row.ip_expiry)


def create_hold(db: Session, data: HoldInput, today: date, min_lead_days: int) -> Hold:
    """One transaction: lock the slot, expire its stale hold, check caps, insert. The unique index picks the winner."""
    priced = quote(data.package, data.kids, data.addons)
    phone = normalize_phone(data.phone)
    email = data.email.strip().lower()

    slot = db.execute(
        text("SELECT id, local_date, blocked_at FROM slots WHERE id = :id FOR SHARE"), {"id": data.slot_id}
    ).one_or_none()
    if slot is None:
        db.rollback()
        raise SlotNotFound()
    if slot.local_date < first_bookable_date(today, min_lead_days):
        db.rollback()
        raise SlotTooSoon()
    if slot.blocked_at is not None:
        db.rollback()
        raise SlotUnavailable()

    db.execute(
        text(
            "UPDATE holds SET status = 'expired', updated_at = now()"
            " WHERE slot_id = :id AND status = 'pending_payment' AND expires_at <= now()"
        ),
        {"id": data.slot_id},
    )
    _lock_identities(db, phone, email, data.client_ip)
    try:
        _enforce_caps(db, phone, email, data.client_ip)
    except HoldLimitReached:
        db.rollback()
        raise

    hold = Hold(
        slot_id=data.slot_id,
        status="pending_payment",
        expires_at=func.now() + timedelta(minutes=HOLD_MINUTES),
        package=priced.package,
        kids=priced.kids,
        addons=priced.addons,
        line_items=priced.line_items_json(),
        total_cents=priced.total_cents,
        deposit_cents=priced.deposit_cents,
        first_name=data.first_name.strip(),
        last_name=data.last_name.strip(),
        phone=phone,
        email=email,
        notes=(data.notes or "").strip() or None,
        client_ip=data.client_ip,
    )
    db.add(hold)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if isinstance(error.orig, UniqueViolation) and error.orig.diag.constraint_name == SLOT_INDEX:
            raise SlotUnavailable() from error
        raise
    db.refresh(hold)
    return hold


@dataclass(frozen=True)
class HoldState:
    hold: Hold
    status: str
    starts_at: datetime
    seconds_remaining: int
    confirmation_code: str | None


def hold_state(db: Session, hold_id: str) -> HoldState | None:
    row = db.execute(
        select(
            Hold,
            Slot.starts_at,
            Booking.confirmation_code,
            func.greatest(0, func.extract("epoch", Hold.expires_at - func.now())).label("remaining"),
        )
        .join(Slot, Slot.id == Hold.slot_id)
        .outerjoin(Booking, Booking.hold_id == Hold.id)
        .where(Hold.id == hold_id)
    ).one_or_none()
    if row is None:
        return None
    hold, starts_at, code, remaining = row
    status = hold.status
    if status == "pending_payment" and remaining <= 0:
        status = "expired"
    return HoldState(hold, status, starts_at, int(remaining), code)


def attach_payment_link(db: Session, hold: Hold, link_id: str, url: str, order_id: str) -> None:
    hold.square_payment_link_id = link_id
    hold.checkout_url = url
    hold.square_order_id = order_id
    db.commit()


def cancel_pending_hold(db: Session, hold_id: object) -> None:
    """Frees the slot when checkout could not be started."""
    db.rollback()
    db.execute(
        text("UPDATE holds SET status = 'cancelled', updated_at = now() WHERE id = :id AND status = 'pending_payment'"),
        {"id": hold_id},
    )
    db.commit()
