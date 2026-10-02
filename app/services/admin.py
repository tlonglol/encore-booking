from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

FULL_REFUND_NOTICE = timedelta(days=7)


class BookingNotFound(Exception):
    pass


class AlreadyCancelled(Exception):
    pass


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
                       b.kid_name, b.kid_age, b.birthday, b.notes,
                       b.square_payment_id, b.created_at, b.cancelled_at, b.cancel_reason,
                       b.event_starts_at - coalesce(b.cancelled_at, now()) >= interval '7 days' AS full_refund
                FROM bookings b
                ORDER BY b.cancelled_at IS NOT NULL, b.event_starts_at
                """
            )
        )
    )


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


def list_leads(db: Session) -> dict[str, list[Row]]:
    """Follow-up material for the owner: waitlist signups, and holds that captured an email but never paid."""
    waitlist = db.execute(text("SELECT id, email, created_at FROM waitlist ORDER BY created_at DESC"))
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
    return {"waitlist": list(waitlist), "abandoned": list(abandoned)}


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
