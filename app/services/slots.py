from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Slot
from app.timeutil import first_bookable_date, slot_start_utc, upcoming_weekend_days

OPEN = "open"
HELD = "held"
BOOKED = "booked"
BLOCKED = "blocked"
PAST = "past"


@dataclass(frozen=True)
class SlotView:
    id: int
    local_date: date
    starts_at: datetime
    status: str


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


def slot_views(db: Session, first: date, last: date, today: date, min_lead_days: int) -> list[SlotView]:
    bookable_from = first_bookable_date(today, min_lead_days)
    views = []
    for row in db.execute(_STATUS_QUERY, {"first": first, "last": last}):
        if row.local_date < bookable_from:
            status = PAST
        elif row.booked:
            status = BOOKED
        elif row.held:
            status = HELD
        elif row.blocked:
            status = BLOCKED
        else:
            status = OPEN
        views.append(SlotView(row.id, row.local_date, row.starts_at, status))
    return views


def availability(db: Session, today: date, min_lead_days: int, weekends: int) -> list[SlotView]:
    days = upcoming_weekend_days(today, weekends)
    ensure_slots(db, days)
    if not days:
        return []
    return slot_views(db, days[0], days[-1], today, min_lead_days)


def open_alternatives(db: Session, today: date, min_lead_days: int, weekends: int, limit: int = 3) -> list[SlotView]:
    return [view for view in availability(db, today, min_lead_days, weekends) if view.status == OPEN][:limit]


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
