from dataclasses import dataclass
from datetime import date, time, timedelta

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import PartyRequest
from app.pricing import quote
from app.services.holds import normalize_phone
from app.timeutil import first_bookable_date, is_weekend

EARLIEST_START = time(9, 0)
LATEST_START = time(20, 0)
HORIZON_DAYS = 366
DUPLICATE_INDEXES = {"requests_one_new_per_phone_date", "requests_one_new_per_email_date"}


class RequestNotAllowed(Exception):
    pass


class DuplicateRequest(Exception):
    pass


@dataclass(frozen=True)
class RequestInput:
    local_date: date
    preferred_time: time
    package: str
    kids: int
    addons: dict[str, int]
    first_name: str
    last_name: str
    phone: str
    email: str
    notes: str | None
    client_ip: str | None


def _weekend_state(db: Session, day: date) -> tuple[bool, bool]:
    row = db.execute(
        text(
            """
            SELECT EXISTS (SELECT 1 FROM bookings b JOIN slots s ON s.id = b.slot_id
                           WHERE s.local_date = :day AND b.cancelled_at IS NULL) AS booked,
                   EXISTS (SELECT 1 FROM slots s WHERE s.local_date = :day
                           AND s.blocked_at IS NOT NULL) AS blocked
            """
        ),
        {"day": day},
    ).one()
    return row.booked, row.blocked


def create_request(
    db: Session, data: RequestInput, today: date, min_lead_days: int, weekend_default: str
) -> PartyRequest:
    priced = quote(data.package, data.kids, data.addons)
    phone = normalize_phone(data.phone)
    email = data.email.strip().lower()

    if not EARLIEST_START <= data.preferred_time <= LATEST_START:
        raise RequestNotAllowed("Pick a start time between 9:00 AM and 8:00 PM.")
    if data.local_date < first_bookable_date(today, min_lead_days):
        raise RequestNotAllowed("That date is too soon to request online. Call (646) 203-8087.")
    if data.local_date > today + timedelta(days=HORIZON_DAYS):
        raise RequestNotAllowed("That date is more than a year out. Call (646) 203-8087.")
    if is_weekend(data.local_date):
        if weekend_default == "open":
            raise RequestNotAllowed("Weekend mornings are booked online. Pick the date in the calendar.")
        booked, blocked = _weekend_state(db, data.local_date)
        if booked or blocked:
            raise RequestNotAllowed("That date isn't available.")

    request = PartyRequest(
        local_date=data.local_date,
        preferred_time=data.preferred_time,
        status="new",
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
    db.add(request)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if isinstance(error.orig, UniqueViolation) and error.orig.diag.constraint_name in DUPLICATE_INDEXES:
            raise DuplicateRequest() from error
        raise
    db.refresh(request)
    return request
