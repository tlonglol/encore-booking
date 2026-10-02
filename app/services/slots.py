from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session

from app.models import Slot
from app.timeutil import first_bookable_date, is_weekend, slot_start_utc, window_days

MAX_WEEKENDS = 52  # a year out; the cap on how far a parent can expand the date list

OPEN = "open"
HELD = "held"
BOOKED = "booked"
BLOCKED = "blocked"
PAST = "past"
REQUEST = "request"
# Only the owner's date field can see this one: a date that is free because it has no slot row at all.
# Everywhere a parent looks, a date with no row is a request date, never a bookable one.
NONE = "none"


@dataclass(frozen=True)
class DayView:
    local_date: date
    status: str
    slot_id: int | None
    starts_at: datetime | None


@dataclass(frozen=True)
class DateStatus:
    """What one date is, for the owner's date field. `starts_at` is what the party would start at whether
    or not a slot row exists yet, so the field can say "10:00 AM" before anything has been created."""

    local_date: date
    status: str
    starts_at: datetime
    slot_id: int | None
    confirmation_code: str | None


class SlotConflict(Exception):
    pass


def ensure_slots(db: Session, days: list[date]) -> None:
    """Idempotent: safe to run on every availability request and concurrently."""
    if not days:
        return
    rows = [{"starts_at": slot_start_utc(day), "local_date": day} for day in days]
    db.execute(insert(Slot).values(rows).on_conflict_do_nothing(index_elements=["starts_at"]))
    db.commit()


_STATUS_QUERY = text(
    """
    SELECT s.id, s.local_date, s.starts_at, s.blocked_at IS NOT NULL AS blocked,
           EXISTS (SELECT 1 FROM bookings b WHERE b.slot_id = s.id AND b.cancelled_at IS NULL) AS booked,
           EXISTS (SELECT 1 FROM holds h WHERE h.slot_id = s.id AND h.status = 'pending_payment'
                   AND h.expires_at > now()) AS held
    FROM slots s
    WHERE s.local_date BETWEEN :first AND :last
    ORDER BY s.starts_at
    """
)


def availability(
    db: Session, today: date, min_lead_days: int, weekends: int, weekend_default: str = "open"
) -> list[DayView]:
    """Every date from today through the last weekend: weekend slots with computed state, weekdays as request dates."""
    days = window_days(today, weekends)
    ensure_slots(db, [day for day in days if is_weekend(day)])
    rows = {
        row.local_date: row
        for row in db.execute(_STATUS_QUERY, {"first": days[0], "last": days[-1]})
    }
    bookable_from = first_bookable_date(today, min_lead_days)
    views = []
    for day in days:
        # Weekday state is never read from a slot row, because one can exist: the owner can book a weekday
        # by hand, which creates a slot for that date. To a parent a weekday is a request date regardless.
        row = rows.get(day) if is_weekend(day) else None
        if row is None:
            views.append(DayView(day, PAST if day < bookable_from else REQUEST, None, None))
            continue
        if day < bookable_from:
            status = PAST
        elif row.booked:
            status = BOOKED
        elif row.held:
            status = HELD
        elif row.blocked:
            status = BLOCKED
        elif weekend_default == "request":
            status = REQUEST
        else:
            status = OPEN
        views.append(DayView(day, status, row.id, row.starts_at))
    return views


def open_alternatives(
    db: Session, today: date, min_lead_days: int, weekends: int, weekend_default: str = "open", limit: int = 3
) -> list[DayView]:
    return [view for view in availability(db, today, min_lead_days, weekends, weekend_default) if view.status == OPEN][:limit]


_DATE_STATUS_QUERY = text(
    """
    SELECT s.id, s.blocked_at IS NOT NULL AS blocked,
           (SELECT b.confirmation_code FROM bookings b
            WHERE b.slot_id = s.id AND b.cancelled_at IS NULL) AS confirmation_code,
           EXISTS (SELECT 1 FROM holds h WHERE h.slot_id = s.id AND h.status = 'pending_payment'
                   AND h.expires_at > now()) AS held
    FROM slots s
    WHERE s.starts_at = :starts_at
    """
)


def date_status(db: Session, day: date, today: date) -> DateStatus:
    """What one date is right now. A read: a date with no slot row stays that way, and is reported as NONE.

    Past wins over everything else because that is the order the booking path enforces: a date that has
    already happened is refused before any slot work, whatever is sitting on it.
    """
    starts_at = slot_start_utc(day)
    row = db.execute(_DATE_STATUS_QUERY, {"starts_at": starts_at}).one_or_none()
    code = row.confirmation_code if row else None
    if day < today:
        status = PAST
    elif row is None:
        status = NONE
    elif code is not None:
        status = BOOKED
    elif row.held:
        status = HELD
    elif row.blocked:
        status = BLOCKED
    else:
        status = OPEN
    return DateStatus(day, status, starts_at, row.id if row else None, code)


def lock_or_create_slot(db: Session, day: date) -> Row:
    """The slot for one date, created at 10:00 AM if it does not exist, locked FOR SHARE either way.

    Commits nothing: this runs inside the caller's transaction, which is the whole point. The insert is
    unconditional with ON CONFLICT DO NOTHING on starts_at (the same conflict target ensure_slots uses),
    so two concurrent callers for one new date cannot both create it. The loser's insert blocks on the
    winner's uncommitted index entry for as long as the winner's transaction lives; when the winner
    commits, DO NOTHING applies and the select below sees the winner's row, which the caller's own
    availability checks then refuse. If the winner rolls back instead, the loser's insert simply succeeds.
    """
    starts_at = slot_start_utc(day)
    db.execute(
        insert(Slot).values(starts_at=starts_at, local_date=day).on_conflict_do_nothing(index_elements=["starts_at"])
    )
    # one(), not one_or_none(): after that insert the row exists, and nothing in this system ever deletes a
    # slot. A missing row here is a broken invariant, which is worth a crash rather than a tidy 404.
    return db.execute(
        text("SELECT id, starts_at, blocked_at FROM slots WHERE starts_at = :starts_at FOR SHARE"),
        {"starts_at": starts_at},
    ).one()


def set_blocked(db: Session, slot_id: int, blocked: bool) -> Slot | None:
    """Locks the slot row so a concurrent hold (which reads the slot FOR SHARE) cannot slip in between check and block."""
    slot = db.execute(
        text("SELECT id FROM slots WHERE id = :id FOR UPDATE"), {"id": slot_id}
    ).one_or_none()
    if slot is None:
        db.rollback()
        return None
    if blocked:
        occupied = db.execute(
            text(
                """
                SELECT EXISTS (SELECT 1 FROM holds WHERE slot_id = :id AND status = 'pending_payment'
                               AND expires_at > now())
                    OR EXISTS (SELECT 1 FROM bookings WHERE slot_id = :id AND cancelled_at IS NULL)
                """
            ),
            {"id": slot_id},
        ).scalar_one()
        if occupied:
            db.rollback()
            raise SlotConflict("This date has an active hold or booking. Cancel it before blocking the date.")
    db.execute(
        text("UPDATE slots SET blocked_at = CASE WHEN :blocked THEN now() ELSE NULL END WHERE id = :id"),
        {"id": slot_id, "blocked": blocked},
    )
    db.commit()
    return db.get(Slot, slot_id, populate_existing=True)
