from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.slots import SlotView, availability
from app.timeutil import first_bookable_date, format_day, format_time, venue_today

router = APIRouter()


class SlotOut(BaseModel):
    id: int
    date: str
    day_label: str
    time_label: str
    starts_at: datetime
    status: str


class AvailabilityOut(BaseModel):
    today: str
    first_bookable_date: str
    slots: list[SlotOut]


def slot_out(view: SlotView) -> SlotOut:
    return SlotOut(
        id=view.id,
        date=view.local_date.isoformat(),
        day_label=format_day(view.starts_at),
        time_label=format_time(view.starts_at),
        starts_at=view.starts_at,
        status=view.status,
    )


@router.get("/api/availability")
def get_availability(db: Session = Depends(get_db)) -> AvailabilityOut:
    settings = get_settings()
    today = venue_today(datetime.now(UTC))
    views = availability(db, today, settings.min_lead_days, settings.weekends_ahead)
    return AvailabilityOut(
        today=today.isoformat(),
        first_bookable_date=first_bookable_date(today, settings.min_lead_days).isoformat(),
        slots=[slot_out(view) for view in views],
    )
