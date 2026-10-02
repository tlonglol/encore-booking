import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from psycopg.errors import UniqueViolation
from sqlalchemy import func, text
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Booking, Hold
from app.pricing import quote
from app.services.booking import new_confirmation_code, new_manage_token
from app.services.holds import SLOT_INDEX, SlotUnavailable, normalize_phone
from app.services.slots import lock_or_create_slot

FULL_REFUND_NOTICE = timedelta(days=7)
# The second index that guards a slot. Both are checked by the manual path, because a manual booking
# writes both a hold row and a booking row and either one can lose the race.
BOOKING_SLOT_INDEX = "bookings_one_live_per_slot"


class BookingNotFound(Exception):
    pass


class AlreadyCancelled(Exception):
    pass


@dataclass(frozen=True)
class ManualBookingInput:
    """What the owner types in. No prices: the server computes them from the package and the headcount."""

    local_date: date
    package: str
    kids: int
    addons: dict[str, int]
    first_name: str
    last_name: str
    phone: str
    email: str
    kid_name: str | None
    kid_age: int | None
    birthday: date | None
    notes: str | None
    deposit_collected: bool


@dataclass(frozen=True)
class Cancelled:
    booking_id: int
    cancelled_at: datetime
    full_refund: bool


def list_bookings(db: Session) -> list[Row]:
    return list(
        db.execute(
            text(
                """
                SELECT b.id, b.confirmation_code, b.event_starts_at, b.package, b.kids, b.addons, b.total_cents,
                       b.deposit_cents, b.paid_amount_cents, b.first_name, b.last_name, b.phone, b.email,
                       b.kid_name, b.kid_age, b.birthday, b.notes, b.origin, b.balance_paid_at,
                       b.square_payment_id, b.created_at, b.cancelled_at, b.cancel_reason,
                       b.reschedule_count, b.rescheduled_at, b.cancel_source,
                       b.event_starts_at - coalesce(b.cancelled_at, now()) >= interval '7 days' AS full_refund,
                       (SELECT r.status FROM refunds r WHERE r.booking_id = b.id) AS refund_status
                FROM bookings b
                ORDER BY b.created_at DESC, b.id DESC
                """
            )
        )
    )


def create_manual_booking(db: Session, data: ManualBookingInput, today: date) -> Booking:
    """A party taken over the phone, written through the same slot exclusivity a parent's booking gets.

    No card, no Square, no parent notification. What it does share with the online path is the only part
    that must not be re-implemented: the date is claimed by writing a hold row and a booking row in one
    transaction, so `holds_one_active_per_slot` and `bookings_one_live_per_slot` decide who wins. An
    application-level "is it free?" check runs first for a clear error message, but it is not the guard;
    the indexes are, which is what makes the gap between the form loading and the owner submitting safe.

    The owner can name any future date, including one the parents' grid has never materialized and one
    that is not a weekend. That date's slot row is created here, inside this same transaction and under
    the same lock, so a date that did not exist a moment ago is claimed exactly as exclusively as one
    that did.
    """
    if data.local_date < today:
        raise ValueError("That date has already passed. Pick a future date.")
    priced = quote(data.package, data.kids, data.addons)
    phone = normalize_phone(data.phone)
    email = data.email.strip().lower()

    slot = lock_or_create_slot(db, data.local_date)
    if slot.blocked_at is not None:
        db.rollback()
        raise SlotUnavailable()

    # Lazy expiry, exactly as create_hold does it: a lapsed hold still sits in the active-slot index until
    # something retires it, and it would otherwise block a date the grid already shows as open.
    db.execute(
        text(
            "UPDATE holds SET status = 'expired', updated_at = now()"
            " WHERE slot_id = :id AND status = 'pending_payment' AND expires_at <= now()"
        ),
        {"id": slot.id},
    )
    taken = db.execute(
        text(
            """
            SELECT EXISTS (SELECT 1 FROM holds WHERE slot_id = :id AND status = 'pending_payment'
                           AND expires_at > now())
                OR EXISTS (SELECT 1 FROM bookings WHERE slot_id = :id AND cancelled_at IS NULL)
            """
        ),
        {"id": slot.id},
    ).scalar_one()
    if taken:
        db.rollback()
        raise SlotUnavailable()

    hold = Hold(
        slot_id=slot.id,
        status="booked",
        # Booked outright, so there is no window to run out; the column is NOT NULL and wants a value.
        expires_at=func.now(),
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
        kid_name=(data.kid_name or "").strip() or None,
        kid_age=data.kid_age,
        birthday=data.birthday,
        notes=(data.notes or "").strip() or None,
    )
    db.add(hold)
    # One try around both writes. Both rows claim the slot: the hold's claim is tested when this flush
    # writes it, the booking's when the commit writes that, and either one losing means the date went while
    # the owner was typing. Outside this try, a flush that lost the race would be a 500 rather than a 409.
    try:
        db.flush()
        booking = Booking(
            hold_id=hold.id,
            slot_id=slot.id,
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
            # Not a Square id and shaped so it can never be mistaken for one. `origin` is what the refund path
            # actually reads; this keeps the unique NOT NULL column honest.
            square_payment_id=f"manual-{uuid.uuid4()}",
            square_order_id="",
            paid_amount_cents=priced.deposit_cents if data.deposit_collected else 0,
            origin="manual",
            event_starts_at=slot.starts_at,
        )
        db.add(booking)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        constraint = error.orig.diag.constraint_name if isinstance(error.orig, UniqueViolation) else None
        if constraint in (SLOT_INDEX, BOOKING_SLOT_INDEX):
            raise SlotUnavailable() from error
        raise
    db.refresh(booking)
    return booking


def set_balance_paid(db: Session, booking_id: int, paid: bool) -> datetime | None:
    """Stamps (or clears) the money the owner took in person. Moves nothing and tells nobody.

    Refused on a cancelled booking: there is no party to collect at, and a stamp there would only read as
    a record of money that was never taken.
    """
    row = db.execute(
        text("SELECT id, cancelled_at FROM bookings WHERE id = :id FOR UPDATE"), {"id": booking_id}
    ).one_or_none()
    if row is None:
        db.rollback()
        raise BookingNotFound()
    if row.cancelled_at is not None:
        db.rollback()
        raise AlreadyCancelled()
    stamped = db.execute(
        text(
            "UPDATE bookings SET balance_paid_at = CASE WHEN :paid THEN now() END"
            " WHERE id = :id RETURNING balance_paid_at"
        ),
        {"id": booking_id, "paid": paid},
    ).scalar_one()
    db.commit()
    return stamped


def cancel_booking(db: Session, booking_id: int, reason: str | None) -> Cancelled:
    """Frees the slot: the booking stops counting as live and its hold leaves the active-hold index, in one commit."""
    row = db.execute(
        text("SELECT id, hold_id, cancelled_at FROM bookings WHERE id = :id FOR UPDATE"), {"id": booking_id}
    ).one_or_none()
    if row is None:
        db.rollback()
        raise BookingNotFound()
    if row.cancelled_at is not None:
        db.rollback()
        raise AlreadyCancelled()
    cancelled = db.execute(
        text(
            "UPDATE bookings SET cancelled_at = now(), cancel_reason = :reason WHERE id = :id"
            " RETURNING cancelled_at, event_starts_at - now() >= interval '7 days' AS full_refund"
        ),
        {"id": booking_id, "reason": reason},
    ).one()
    db.execute(
        text("UPDATE holds SET status = 'cancelled', updated_at = now() WHERE id = :hold_id"), {"hold_id": row.hold_id}
    )
    db.commit()
    return Cancelled(booking_id, cancelled.cancelled_at, cancelled.full_refund)


def list_refunds(db: Session) -> list[Row]:
    """Every refund with the booking behind it. Queued ones first: they are the ones waiting on the owner."""
    return list(
        db.execute(
            text(
                """
                SELECT r.id, r.status, r.amount_cents, r.queued_reason, r.error, r.created_at, r.sent_at,
                       r.settled_at, r.square_refund_id, b.id AS booking_id, b.confirmation_code,
                       b.first_name, b.last_name, b.phone, b.email, b.event_starts_at, b.package,
                       (SELECT count(*) FROM bookings o WHERE o.cancelled_at IS NOT NULL
                          AND (o.phone = b.phone OR o.email = b.email
                               OR (o.card_fingerprint IS NOT NULL AND o.card_fingerprint = b.card_fingerprint))
                       ) AS cancellations
                FROM refunds r JOIN bookings b ON b.id = r.booking_id
                ORDER BY r.status <> 'pending_approval', r.created_at DESC
                """
            )
        )
    )


def list_repeat_cancellers(db: Session, threshold: int) -> list[Row]:
    """Parents excluded from automatic refunds, grouped the way the guard matches them: by card first, and
    by email where there is no card fingerprint to group on."""
    return list(
        db.execute(
            text(
                """
                SELECT coalesce(card_fingerprint, email) AS identity,
                       card_fingerprint IS NOT NULL AS by_card,
                       count(*) AS cancellations,
                       max(cancelled_at) AS last_cancelled_at,
                       min(first_name || ' ' || last_name) AS name,
                       min(email) AS email,
                       min(phone) AS phone
                FROM bookings
                WHERE cancelled_at IS NOT NULL
                GROUP BY coalesce(card_fingerprint, email), card_fingerprint IS NOT NULL
                HAVING count(*) > :threshold
                ORDER BY count(*) DESC
                """
            ),
            {"threshold": threshold},
        )
    )


def refund_for_approval(db: Session, refund_id: int) -> Row | None:
    """Locks a queued refund so two owner clicks cannot both release the same one to Square."""
    return db.execute(
        text("SELECT id, status FROM refunds WHERE id = :id FOR UPDATE"), {"id": refund_id}
    ).one_or_none()


def approve_refund(db: Session, refund_id: int) -> bool:
    """Moves a queued refund into the same state an automatic one starts in; the caller then sends it."""
    updated = db.execute(
        text(
            "UPDATE refunds SET status = 'pending', queued_reason = NULL"
            " WHERE id = :id AND status = 'pending_approval' RETURNING id"
        ),
        {"id": refund_id},
    ).one_or_none()
    db.commit()
    return updated is not None


def reject_refund(db: Session, refund_id: int) -> bool:
    """The owner decides this one is not getting refunded. Nothing is sent to Square."""
    updated = db.execute(
        text(
            "UPDATE refunds SET status = 'rejected', settled_at = now()"
            " WHERE id = :id AND status = 'pending_approval' RETURNING id"
        ),
        {"id": refund_id},
    ).one_or_none()
    db.commit()
    return updated is not None


def list_active_holds(db: Session) -> list[Row]:
    """Dates being held right now, newest first. Expiry is lazy everywhere else in the system, so this asks
    the same question availability does: pending_payment and not yet past its expiry."""
    return list(
        db.execute(
            text(
                """
                SELECT h.id, h.created_at, h.expires_at,
                       greatest(0, extract(epoch FROM h.expires_at - now()))::int AS seconds_remaining,
                       h.package, h.kids, h.first_name, h.last_name, h.phone, h.email, h.total_cents,
                       h.client_ip, h.phone IS NOT NULL AS reached_checkout,
                       s.starts_at AS slot_starts_at
                FROM holds h JOIN slots s ON s.id = h.slot_id
                WHERE h.status = 'pending_payment' AND h.expires_at > now()
                ORDER BY h.created_at DESC
                """
            )
        )
    )


def list_flags(db: Session) -> list[Row]:
    return list(
        db.execute(
            text(
                """
                SELECT f.id, f.kind, f.amount_cents, f.square_payment_id, f.square_order_id, f.created_at,
                       f.resolved_at, h.first_name, h.last_name, h.phone, h.email, h.package, h.kids,
                       s.starts_at AS slot_starts_at
                FROM payment_flags f
                LEFT JOIN holds h ON h.id = f.hold_id
                LEFT JOIN slots s ON s.id = h.slot_id
                ORDER BY f.resolved_at IS NOT NULL, f.created_at DESC
                """
            )
        )
    )


def resolve_flag(db: Session, flag_id: int) -> bool:
    updated = db.execute(
        text("UPDATE payment_flags SET resolved_at = coalesce(resolved_at, now()) WHERE id = :id RETURNING id"),
        {"id": flag_id},
    ).one_or_none()
    db.commit()
    return updated is not None


def list_notifications(db: Session, limit: int = 100) -> list[Row]:
    return list(
        db.execute(
            text(
                "SELECT id, created_at, sent_at, audience, channel, recipient, status, error, body"
                " FROM notifications ORDER BY created_at DESC, id DESC LIMIT :limit"
            ),
            {"limit": limit},
        )
    )


def list_waitlist(db: Session) -> list[Row]:
    """Everyone waiting, newest first, each row carrying the date it is waiting for.

    Ordered by the party date rather than by signup time: the owner reads this to decide whether a
    sold-out Saturday is worth opening a second slot for, and that question is asked per date.
    """
    return list(
        db.execute(
            text(
                """
                SELECT w.id, w.email, w.phone, w.created_at, w.notified_at,
                       s.local_date, s.starts_at,
                       count(*) OVER (PARTITION BY w.slot_id) AS waiting_on_this_date
                FROM waitlist w JOIN slots s ON s.id = w.slot_id
                ORDER BY s.local_date, w.created_at
                """
            )
        )
    )


def list_leads(db: Session) -> dict[str, list[Row]]:
    """Holds that captured an email and never paid. The waitlist has its own view."""
    abandoned = db.execute(
        text(
            """
            SELECT h.id, h.email, h.first_name, h.last_name, h.phone, h.package, h.kids, h.created_at,
                   s.starts_at AS slot_starts_at
            FROM holds h JOIN slots s ON s.id = h.slot_id
            WHERE h.email IS NOT NULL
              AND h.created_at > now() - interval '14 days'
              AND (h.status = 'cancelled' OR (h.status = 'pending_payment' AND h.expires_at <= now()))
              AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.hold_id = h.id)
            ORDER BY h.created_at DESC
            """
        )
    )
    return {"abandoned": list(abandoned)}


def list_requests(db: Session) -> list[Row]:
    return list(
        db.execute(
            text(
                "SELECT id, local_date, preferred_time, status, package, kids, addons, total_cents,"
                " deposit_cents, first_name, last_name, phone, email, kid_name, kid_age, birthday, notes, created_at"
                " FROM requests ORDER BY status <> 'new', created_at DESC"
            )
        )
    )
