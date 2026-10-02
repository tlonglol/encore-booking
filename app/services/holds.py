import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import phonenumbers
from psycopg.errors import UniqueViolation
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking, Hold, Slot
from app.pricing import quote
from app.timeutil import first_bookable_date

BIRTHDAY_HORIZON_DAYS = 366
HOLD_MINUTES = 15
MAX_ACTIVE_HOLDS_PER_IP = 5
SLOT_INDEX = "holds_one_active_per_slot"
# Two lapsed, unpaid holds on one date is a parent who has stopped deciding and started sitting on it.
COOLDOWN_AFTER_EXPIRIES = 2
# More than this many holds in an hour from one client, with nothing paid, is worth the owner hearing about.
SPIKE_HOLDS_PER_HOUR = 10

# Advisory lock namespaces, always taken in this order so two checkout requests cannot deadlock.
LOCK_PHONE, LOCK_EMAIL, LOCK_IP = 1, 2, 3


class SlotNotFound(Exception):
    pass


class SlotTooSoon(Exception):
    pass


class SlotUnavailable(Exception):
    pass


class HoldNotPending(Exception):
    """The hold exists but is expired, cancelled, or already booked; checkout must change nothing."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class HoldLimitReached(Exception):
    def __init__(self, reason: str, expires_at: datetime) -> None:
        super().__init__(reason)
        self.expires_at = expires_at


class DateCoolingDown(Exception):
    """Two lapsed holds on this date from this browser. The date is open; this browser has to wait."""

    def __init__(self, until: datetime) -> None:
        super().__init__("cooling down")
        self.until = until


class TurnstileFailed(Exception):
    """Only reachable while TURNSTILE_ENABLED is on, which it is not by default."""


@dataclass(frozen=True)
class CheckoutInput:
    package: str
    kids: int
    addons: dict[str, int]
    first_name: str
    last_name: str
    phone: str
    email: str
    kid_name: str
    kid_age: int
    birthday: date | None = None


def check_birthday(birthday: date | None, today: date) -> date | None:
    """The upcoming birthday: today through a year out. Anything else is a typo or a past date."""
    if birthday is None:
        return None
    if birthday < today:
        raise ValueError("That birthday has already passed. Pick the upcoming one.")
    if birthday > today + timedelta(days=BIRTHDAY_HORIZON_DAYS):
        raise ValueError("That birthday is more than a year away. Check the year.")
    return birthday


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


def _enforce_ip_cap(db: Session, client_ip: str | None) -> None:
    """Creation is anonymous, so the IP cap is the only one that can apply here."""
    if client_ip is None:
        return
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_IP, "key": client_ip})
    row = db.execute(
        text(
            "SELECT count(*) AS held, min(expires_at) AS soonest FROM holds"
            " WHERE status = 'pending_payment' AND expires_at > now() AND client_ip = CAST(:ip AS inet)"
        ),
        {"ip": client_ip},
    ).one()
    if row.held >= MAX_ACTIVE_HOLDS_PER_IP:
        raise HoldLimitReached("Too many dates on hold from this network.", row.soonest)


def _enforce_creation_rate(db: Session, client_ip: str | None, per_hour: int) -> None:
    """A rolling hour of hold creations per IP.

    A hold swapped away by the same browser does not count. Swapping releases the old date in the same
    transaction, so it locks nothing and costs nobody anything; counting it would mean a parent who changed
    their mind five times was locked out for an hour, which is the opposite of the point. What this counts
    is distinct dates a client actually sat on.
    """
    if client_ip is None:
        return
    held = db.execute(
        text(
            "SELECT count(*) FROM holds"
            " WHERE client_ip = CAST(:ip AS inet) AND created_at > now() - interval '1 hour'"
            " AND status <> 'cancelled'"
        ),
        {"ip": client_ip},
    ).scalar_one()
    if held >= per_hour:
        raise HoldLimitReached(
            "That's a lot of dates from this network in the last hour. Give it a few minutes, or call"
            " (646) 203-8087 and we'll sort it out.",
            db.scalar(select(func.now() + timedelta(hours=1))),
        )


def _enforce_date_cooldown(db: Session, session_id: str | None, slot_id: int, minutes: int) -> None:
    """After two consecutive lapsed, unpaid holds on one date, this browser waits before taking it again.

    Consecutive and per date on purpose: one expiry followed by a Re-hold is the expiry modal's normal
    path and is untouched, and holds on other dates are not what this is about.
    """
    if session_id is None:
        return
    recent = db.execute(
        text(
            """
            SELECT h.status, h.expires_at FROM holds h
            WHERE h.session_id = :session_id AND h.slot_id = :slot_id
              AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.hold_id = h.id)
            ORDER BY h.created_at DESC LIMIT :limit
            """
        ),
        {"session_id": session_id, "slot_id": slot_id, "limit": COOLDOWN_AFTER_EXPIRIES},
    ).all()
    lapsed = [row for row in recent if row.status == "expired" or row.expires_at <= datetime.now(UTC)]
    if len(lapsed) < COOLDOWN_AFTER_EXPIRIES or len(recent) < COOLDOWN_AFTER_EXPIRIES:
        return
    until = max(row.expires_at for row in lapsed) + timedelta(minutes=minutes)
    if until > datetime.now(UTC):
        raise DateCoolingDown(until)


def create_hold(
    db: Session,
    slot_id: int,
    client_ip: str | None,
    replace_hold_id: uuid.UUID | None,
    today: date,
    min_lead_days: int,
    session_id: str | None = None,
    rate_per_hour: int = MAX_ACTIVE_HOLDS_PER_IP,
    cooldown_minutes: int = 45,
) -> Hold:
    """One transaction: lock the slot, expire its stale hold, swap out this browser's old hold, check the
    caps, insert. The unique index picks the winner. No contact details yet; those arrive at checkout."""
    slot = db.execute(
        text("SELECT id, local_date, blocked_at FROM slots WHERE id = :id FOR SHARE"), {"id": slot_id}
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
        {"id": slot_id},
    )
    # One browser, one date. The client's replace_hold_id is still honoured, but the session is what makes
    # this a guarantee: a second tab, or a tab that lost its pointer, swaps rather than stacking.
    if session_id is not None:
        db.execute(
            text(
                "UPDATE holds SET status = 'cancelled', updated_at = now()"
                " WHERE session_id = :session_id AND status = 'pending_payment'"
            ),
            {"session_id": session_id},
        )
    if replace_hold_id is not None:
        db.execute(
            text(
                "UPDATE holds SET status = 'cancelled', updated_at = now()"
                " WHERE id = :id AND status = 'pending_payment'"
            ),
            {"id": replace_hold_id},
        )
    try:
        _enforce_date_cooldown(db, session_id, slot_id, cooldown_minutes)
        _enforce_creation_rate(db, client_ip, rate_per_hour)
        _enforce_ip_cap(db, client_ip)
    except (HoldLimitReached, DateCoolingDown):
        db.rollback()
        raise

    hold = Hold(
        slot_id=slot_id,
        status="pending_payment",
        expires_at=func.now() + timedelta(minutes=HOLD_MINUTES),
        deposit_cents=10000,
        client_ip=client_ip,
        session_id=session_id,
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


def payable_hold(db: Session, hold_id: uuid.UUID) -> Hold:
    """The hold a card is about to be charged against: still pending, still in time, already snapshotted.

    Read without a row lock on purpose. The lock that matters is taken later, inside the one function
    that writes the booking, and holding one across a call to Square would pin a database row open for
    the length of a card authorization.
    """
    row = db.execute(
        select(Hold, func.greatest(0, func.extract("epoch", Hold.expires_at - func.now())).label("remaining"))
        .where(Hold.id == hold_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise SlotNotFound()
    hold, remaining = row
    if hold.status != "pending_payment" or remaining <= 0:
        status = "expired" if hold.status == "pending_payment" else hold.status
        raise HoldNotPending(status)
    if hold.total_cents is None or not hold.phone or not hold.email:
        # Checkout is what writes the snapshot, and the deposit is only honest once it exists.
        raise HoldNotPending("incomplete")
    return hold


def booking_for_hold(db: Session, hold_id: uuid.UUID) -> Booking | None:
    return db.scalars(select(Booking).where(Booking.hold_id == hold_id)).one_or_none()


def save_email(db: Session, hold_id: uuid.UUID, email: str) -> bool:
    """Abandoner recovery: the checkout page persists the email on blur, long before the pay button."""
    updated = db.execute(
        text(
            "UPDATE holds SET email = :email, updated_at = now()"
            " WHERE id = :id AND status = 'pending_payment' AND square_order_id IS NULL RETURNING id"
        ),
        {"id": hold_id, "email": email.strip().lower()},
    ).scalar_one_or_none()
    db.commit()
    return updated is not None


def draft_hold(
    db: Session, hold_id: uuid.UUID, package: str, kids: int, addons: dict[str, int], birthday: date | None, today: date
) -> Hold:
    """The party as the parent builds it, priced by the same function checkout uses. Never touches contact
    details, the payment link, or the hold's status; checkout re-prices before any money moves."""
    priced = quote(package, kids, addons)
    checked_birthday = check_birthday(birthday, today)
    row = db.execute(
        select(Hold, func.greatest(0, func.extract("epoch", Hold.expires_at - func.now())).label("remaining"))
        .where(Hold.id == hold_id)
        .with_for_update(of=Hold)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        db.rollback()
        raise SlotNotFound()
    hold, remaining = row
    if hold.status != "pending_payment" or remaining <= 0:
        status = "expired" if hold.status == "pending_payment" else hold.status
        db.rollback()
        raise HoldNotPending(status)
    hold.birthday = checked_birthday
    hold.package = priced.package
    hold.kids = priced.kids
    hold.addons = priced.addons
    hold.line_items = priced.line_items_json()
    hold.total_cents = priced.total_cents
    db.commit()
    db.refresh(hold)
    return hold


def _enforce_contact_caps(db: Session, hold_id: uuid.UUID, phone: str, email: str) -> None:
    """One active hold per phone and per email, checked when the contact is first known."""
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_PHONE, "key": phone})
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"), {"ns": LOCK_EMAIL, "key": email})
    row = db.execute(
        text(
            """
            SELECT max(expires_at) FILTER (WHERE phone = :phone) AS phone_expiry,
                   max(expires_at) FILTER (WHERE email = :email) AS email_expiry
            FROM holds
            WHERE status = 'pending_payment' AND expires_at > now() AND id <> :hold_id
              AND (phone = :phone OR email = :email)
            """
        ),
        {"phone": phone, "email": email, "hold_id": hold_id},
    ).one()
    if row.phone_expiry is not None:
        raise HoldLimitReached("This phone number already has another date on hold.", row.phone_expiry)
    if row.email_expiry is not None:
        raise HoldLimitReached("This email already has another date on hold.", row.email_expiry)


def checkout_hold(db: Session, hold_id: uuid.UUID, data: CheckoutInput, today: date) -> Hold:
    """Prices server-side and snapshots everything onto the live hold, in one transaction, under the row lock
    the webhook also takes. The payment link is created by the caller afterwards; deposit_cents never changes."""
    priced = quote(data.package, data.kids, data.addons)
    phone = normalize_phone(data.phone)
    email = data.email.strip().lower()
    birthday = check_birthday(data.birthday, today)

    row = db.execute(
        select(Hold, func.greatest(0, func.extract("epoch", Hold.expires_at - func.now())).label("remaining"))
        .where(Hold.id == hold_id)
        .with_for_update(of=Hold)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        db.rollback()
        raise SlotNotFound()
    hold, remaining = row
    if hold.status != "pending_payment" or remaining <= 0:
        status = "expired" if hold.status == "pending_payment" else hold.status
        db.rollback()
        raise HoldNotPending(status)
    try:
        _enforce_contact_caps(db, hold.id, phone, email)
    except HoldLimitReached:
        db.rollback()
        raise

    hold.package = priced.package
    hold.kids = priced.kids
    hold.addons = priced.addons
    hold.line_items = priced.line_items_json()
    hold.total_cents = priced.total_cents
    hold.first_name = data.first_name.strip()
    hold.last_name = data.last_name.strip()
    hold.phone = phone
    hold.email = email
    hold.kid_name = data.kid_name.strip()
    hold.kid_age = data.kid_age
    hold.birthday = birthday
    db.commit()
    db.refresh(hold)
    return hold


@dataclass(frozen=True)
class HoldState:
    hold: Hold
    status: str
    starts_at: datetime
    local_date: date
    seconds_remaining: int
    confirmation_code: str | None
    manage_token: str | None


def hold_state(db: Session, hold_id: str) -> HoldState | None:
    row = db.execute(
        select(
            Hold,
            Slot.starts_at,
            Slot.local_date,
            Booking.confirmation_code,
            Booking.manage_token,
            func.greatest(0, func.extract("epoch", Hold.expires_at - func.now())).label("remaining"),
        )
        .join(Slot, Slot.id == Hold.slot_id)
        .outerjoin(Booking, Booking.hold_id == Hold.id)
        .where(Hold.id == hold_id)
    ).one_or_none()
    if row is None:
        return None
    hold, starts_at, local_date, code, manage_token, remaining = row
    status = hold.status
    if status == "pending_payment" and remaining <= 0:
        status = "expired"
    return HoldState(hold, status, starts_at, local_date, int(remaining), code, manage_token)


def attach_payment_link(db: Session, hold: Hold, link_id: str, url: str, order_id: str) -> None:
    hold.square_payment_link_id = link_id
    hold.checkout_url = url
    hold.square_order_id = order_id
    db.commit()


def recent_unpaid_holds(db: Session, session_id: str | None, client_ip: str | None) -> int:
    """How many holds this client has created in the last hour with nothing paid for.

    A notice, not a block: the caps above do the blocking. This is the number that tells the owner someone
    is working through the calendar rather than booking a party.
    """
    if session_id is None and client_ip is None:
        return 0
    return db.execute(
        text(
            """
            SELECT count(*) FROM holds h
            WHERE h.created_at > now() - interval '1 hour'
              AND (h.session_id = :session_id OR h.client_ip = CAST(:ip AS inet))
              AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.hold_id = h.id)
            """
        ),
        {"session_id": session_id, "ip": client_ip},
    ).scalar_one()
