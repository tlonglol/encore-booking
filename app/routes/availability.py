from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.slots import DayView, availability
from app.timeutil import first_bookable_date, format_date, format_time, venue_today

router = APIRouter()


class DayOut(BaseModel):
    date: str
    day_label: str
    status: str
    slot_id: int | None
    time_label: str | None
    starts_at: datetime | None


class AvailabilityOut(BaseModel):
    today: str
    first_bookable_date: str
    weekend_default: str
    days: list[DayOut]


def day_out(view: DayView) -> DayOut:
    return DayOut(
        date=view.local_date.isoformat(),
        day_label=format_date(view.local_date),
        status=view.status,
        slot_id=view.slot_id,
        time_label=format_time(view.starts_at) if view.starts_at else None,
        starts_at=view.starts_at,
    )


@router.get("/api/availability")
def get_availability(db: Session = Depends(get_db)) -> AvailabilityOut:
    settings = get_settings()
    today = venue_today(datetime.now(UTC))
    views = availability(db, today, settings.min_lead_days, settings.weekends_ahead, settings.weekend_default)
    return AvailabilityOut(
        today=today.isoformat(),
        first_bookable_date=first_bookable_date(today, settings.min_lead_days).isoformat(),
        weekend_default=settings.weekend_default,
        days=[day_out(view) for view in views],
    )
