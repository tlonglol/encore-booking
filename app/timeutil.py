from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

VENUE_TZ = ZoneInfo("America/New_York")
SLOT_START = time(10, 0)
SATURDAY = 5
SUNDAY = 6


def venue_today(now: datetime) -> date:
    return now.astimezone(VENUE_TZ).date()


def slot_start_utc(day: date) -> datetime:
    return datetime.combine(day, SLOT_START, tzinfo=VENUE_TZ).astimezone(UTC)


def first_bookable_date(today: date, min_lead_days: int) -> date:
    """Lead time counts calendar days in New York: with 2, Thursday can book Saturday, Friday cannot."""
    return today + timedelta(days=min_lead_days)


def upcoming_weekend_days(today: date, weekends: int) -> list[date]:
    """Weekend days from today through the end of the Nth weekend, counting the weekend in progress as the first."""
    saturday = today + timedelta(days=(SATURDAY - today.weekday()) % 7)
    if today.weekday() == SUNDAY:
        saturday = today - timedelta(days=1)
    days: list[date] = []
    for week in range(weekends):
        for offset in (0, 1):
            day = saturday + timedelta(weeks=week, days=offset)
            if day >= today:
                days.append(day)
    return days


def is_weekend(day: date) -> bool:
    return day.weekday() >= SATURDAY


def window_days(today: date, weekends: int) -> list[date]:
    """Every date from today through the last day of the Nth weekend."""
    last = upcoming_weekend_days(today, weekends)[-1]
    return [today + timedelta(days=offset) for offset in range((last - today).days + 1)]


def format_date(day: date) -> str:
    return f"{day:%a}, {day:%b} {day.day}"


def format_clock(moment: time) -> str:
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def format_day(moment: datetime) -> str:
    local = moment.astimezone(VENUE_TZ)
    return f"{local:%a}, {local:%b} {local.day}"


def format_time(moment: datetime) -> str:
    local = moment.astimezone(VENUE_TZ)
    return f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"
