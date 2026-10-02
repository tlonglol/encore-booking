"""Putting the system back into a known demo state.

This deletes bookings, so it is guarded three ways: it is an admin endpoint like every other, it refuses
unless `DEMO_RESET_ENABLED` is on, and it refuses outright against production Square credentials. The
second and third exist because the first is only a password, and a password is one leak away from someone
wiping a real calendar.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Booking, Hold, Slot
from app.pricing import quote
from app.services.booking import new_confirmation_code, new_manage_token
from app.services.holds import HOLD_MINUTES
from app.services.slots import ensure_slots
from app.timeutil import first_bookable_date, is_weekend, upcoming_weekend_days

# Everything the demo writes, in an order that respects the foreign keys between them.
DEMO_TABLES = (
    "notifications",
    "refunds",
    "payment_flags",
    "square_events",
    "bookings",
    "holds",
    "requests",
    "waitlist",
)

# Three parties that read like real ones, so the owner sees his own business rather than "Test Test".
SAMPLE_PARTIES = (
    {
        "package": "plus",
        "kids": 18,
        "addons": {"pizza_pack": 2, "tournament": 1},
        "first_name": "Marisol",
        "last_name": "Ortega",
        "phone": "+19145550147",
        "email": "marisol.ortega@example.com",
        "kid_name": "Diego",
        "kid_age": 9,
    },
    {
        "package": "premium",
        "kids": 25,
        "addons": {"pizza_pack": 3, "drinks_pack": 2, "extra_hour": 1},
        "first_name": "Danielle",
        "last_name": "Brooks",
        "phone": "+19145550182",
        "email": "d.brooks@example.com",
        "kid_name": "Amara",
        "kid_age": 11,
    },
    {
        "package": "standard",
        "kids": 15,
        "addons": {},
        "first_name": "Kevin",
        "last_name": "Walsh",
        "phone": "+19145550119",
        "email": "kwalsh@example.com",
        "kid_name": "Rory",
        "kid_age": 7,
    },
)

# A parent mid-funnel: a date held, extras chosen, contact details not typed yet.
SAMPLE_HOLD = {"package": "plus", "kids": 20, "addons": {"pizza_pack": 2}}


class ResetNotAllowed(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class DemoState:
    booked: list[date]
    held: list[date]
    open: list[date]
    bookings: int


def check_allowed(settings: Settings) -> None:
    if not settings.demo_reset_enabled:
        raise ResetNotAllowed("Demo reset is switched off. Set DEMO_RESET_ENABLED=true to use it.")
    if settings.square_environment == "production":
        raise ResetNotAllowed("Demo reset refuses to run against production Square credentials.")


def bookable_weekend_days(db: Session, today: date, settings: Settings) -> list[date]:
    """The weekend dates a parent could actually book right now, which is what the grid has to mix up."""
    days = [day for day in upcoming_weekend_days(today, settings.weekends_ahead) if is_weekend(day)]
    ensure_slots(db, days)
    return [day for day in days if day >= first_bookable_date(today, settings.min_lead_days)]


def _slots_by_date(db: Session, days: list[date]) -> dict[date, Slot]:
    if not days:
        return {}
    rows = db.query(Slot).filter(Slot.local_date.in_(days)).all()
    return {slot.local_date: slot for slot in rows}


def _book_sample(db: Session, slot: Slot, party: dict[str, object], booked_days_ago: int) -> Booking:
    """Built the same way the webhook builds one: a booked hold and a booking sharing the snapshot."""
    priced = quote(str(party["package"]), int(party["kids"]), dict(party["addons"]))  # type: ignore[arg-type]
    created = datetime.now(UTC) - timedelta(days=booked_days_ago)
    hold = Hold(
        id=uuid.uuid4(),
        slot_id=slot.id,
        status="booked",
        expires_at=created + timedelta(minutes=HOLD_MINUTES),
        package=priced.package,
        kids=priced.kids,
        addons=priced.addons,
        line_items=priced.line_items_json(),
        total_cents=priced.total_cents,
        deposit_cents=priced.deposit_cents,
        first_name=party["first_name"],
        last_name=party["last_name"],
        phone=party["phone"],
        email=party["email"],
        kid_name=party["kid_name"],
        kid_age=party["kid_age"],
        square_order_id=f"demo-order-{uuid.uuid4()}",
    )
    db.add(hold)
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
        # The birthday a few days after the party, which is what the owner's day-of line is for.
        birthday=slot.local_date + timedelta(days=3),
        square_payment_id=f"demo-pay-{secrets.token_hex(6)}",
        square_order_id=hold.square_order_id or "",
        card_fingerprint=f"demo-card-{secrets.token_hex(4)}",
        paid_amount_cents=hold.deposit_cents,
        event_starts_at=slot.starts_at,
        created_at=created,
    )
    db.add(booking)
    db.flush()
    return booking


def _hold_sample(db: Session, slot: Slot) -> Hold:
    priced = quote(SAMPLE_HOLD["package"], SAMPLE_HOLD["kids"], SAMPLE_HOLD["addons"])  # type: ignore[arg-type]
    hold = Hold(
        id=uuid.uuid4(),
        slot_id=slot.id,
        status="pending_payment",
        expires_at=datetime.now(UTC) + timedelta(minutes=HOLD_MINUTES),
        package=priced.package,
        kids=priced.kids,
        addons=priced.addons,
        line_items=priced.line_items_json(),
        total_cents=priced.total_cents,
        deposit_cents=priced.deposit_cents,
    )
    db.add(hold)
    db.flush()
    return hold


def reset(db: Session, settings: Settings, today: date) -> DemoState:
    """Wipes what the demo writes and lays down a grid with OPEN, BOOKED and one live HOLD on it.

    One transaction, so a reset that fails leaves the previous state rather than half of a new one.
    """
    check_allowed(settings)
    db.rollback()
    db.execute(text(f"TRUNCATE {', '.join(DEMO_TABLES)} RESTART IDENTITY CASCADE"))
    db.execute(text("UPDATE slots SET blocked_at = NULL"))
    db.commit()

    days = bookable_weekend_days(db, today, settings)
    if len(days) < 5:
        raise ResetNotAllowed("Not enough bookable weekend dates to lay out a demo grid.")
    slots = _slots_by_date(db, days)

    # Spread the booked dates through the list rather than filling the front, so the grid looks like a
    # calendar that is filling up rather than one that is simply closed for the next month.
    booked_days = [days[1], days[3], days[6]] if len(days) > 6 else [days[1], days[3], days[4]]
    held_day = days[4] if len(days) > 6 else days[2]

    bookings = 0
    for party, day, days_ago in zip(SAMPLE_PARTIES, booked_days, (9, 4, 1)):
        _book_sample(db, slots[day], party, days_ago)
        bookings += 1
    _hold_sample(db, slots[held_day])
    db.commit()

    taken = set(booked_days) | {held_day}
    return DemoState(
        booked=sorted(booked_days),
        held=[held_day],
        open=[day for day in days if day not in taken],
        bookings=bookings,
    )
