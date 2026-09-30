from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Slot
from app.timeutil import first_bookable_date, is_weekend, slot_start_utc, window_days

OPEN = "open"
HELD = "held"
BOOKED = "booked"
BLOCKED = "blocked"
PAST = "past"
REQUEST = "request"


@dataclass(frozen=True)
class DayView:
    local_date: date
    status: str
    slot_id: int | None
    starts_at: datetime | None


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
        row = rows.get(day)
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
