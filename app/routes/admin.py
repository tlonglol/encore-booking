from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.admin_auth import COOKIE_NAME, SESSION_SECONDS, issue_token, password_matches, token_valid
from app.notifications import addons_summary, notify_owner, owner_manual_booked_text
from app.pricing import PACKAGES
from app.services import admin as admin_service
from app.services import demo
from app.services.holds import SlotNotFound, SlotUnavailable, check_birthday
from app.services.refunds import REPEAT_CANCELLER_THRESHOLD, send_refund
from app.services.slots import SlotConflict, set_blocked
from app.timeutil import VENUE_TZ, format_clock, format_date, format_day, format_time, venue_today

router = APIRouter(prefix="/api/admin")


def short_date(day: date) -> str:
    return f"{day:%b} {day.day}"


def birthday_line(kid_name: str | None, kid_age: int | None, birthday: date | None, party_day: date) -> str | None:
    """The owner's day-of line: "Leo turns 8 Oct 14, party Oct 10". Nothing when no birthday was given."""
    if birthday is None:
        return None
    who = kid_name or "Birthday"
    turns = f"{who} turns {kid_age}" if kid_age else f"{who}'s birthday"
    return f"{turns} {short_date(birthday)}, party {short_date(party_day)}"


class LoginIn(BaseModel):
    password: str


def require_admin(request: Request) -> None:
    if not token_valid(request.cookies.get(COOKIE_NAME), get_settings().session_secret, datetime.now(UTC)):
        raise HTTPException(status_code=401, detail="Admin login required")


@router.post("/login")
def login(body: LoginIn, response: Response) -> dict[str, bool]:
    settings = get_settings()
    if not settings.admin_password or not settings.session_secret:
        raise HTTPException(status_code=503, detail="Admin login is not configured")
    if not password_matches(body.password, settings.admin_password):
        raise HTTPException(status_code=401, detail="Wrong password")
    response.set_cookie(
        COOKIE_NAME,
        issue_token(settings.session_secret, datetime.now(UTC)),
        max_age=SESSION_SECONDS,
        path="/api/admin",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/api/admin", secure=True, httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/session", dependencies=[Depends(require_admin)])
def session_check() -> dict[str, bool]:
    return {"ok": True}


def _set_blocked(slot_id: int, blocked: bool, db: Session) -> dict[str, object]:
    try:
        slot = set_blocked(db, slot_id, blocked)
    except SlotConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")
    return {"id": slot.id, "blocked": slot.blocked_at is not None}


@router.post("/slots/{slot_id}/block", dependencies=[Depends(require_admin)])
def block_slot(slot_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_blocked(slot_id, True, db)


@router.post("/slots/{slot_id}/unblock", dependencies=[Depends(require_admin)])
def unblock_slot(slot_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_blocked(slot_id, False, db)


class CancelIn(BaseModel):
    reason: str | None = None


def _deposit_status(row: Any) -> str:
    """What the owner needs to know about the deposit in three words, by how it was taken."""
    if row.origin == "manual":
        return "collected in person" if row.paid_amount_cents else "waived"
    return "paid (dev fake)" if row.square_payment_id.startswith("dev-pay-") else "paid"


def _when(moment: datetime | None) -> dict[str, str | None]:
    if moment is None:
        return {"day_label": None, "time_label": None}
    return {"day_label": format_day(moment), "time_label": format_time(moment)}


@router.get("/bookings", dependencies=[Depends(require_admin)])
def bookings(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "confirmation_code": row.confirmation_code,
            "starts_at": row.event_starts_at.isoformat(),
            **_when(row.event_starts_at),
            "package": PACKAGES[row.package].label,
            "kids": row.kids,
            "addons": addons_summary(row.addons),
            "total_cents": row.total_cents,
            "deposit_cents": row.deposit_cents,
            "paid_amount_cents": row.paid_amount_cents,
            # What is actually still owed: total less what was taken. The two readings only differ for a
            # manual booking whose deposit was waived, where nothing was paid and the whole total is due.
            "balance_cents": row.total_cents - row.paid_amount_cents,
            "balance_paid_at": row.balance_paid_at.isoformat() if row.balance_paid_at else None,
            "origin": row.origin,
            "deposit_status": _deposit_status(row),
            "kid_name": row.kid_name,
            "kid_age": row.kid_age,
            "birthday": row.birthday.isoformat() if row.birthday else None,
            "birthday_line": birthday_line(
                row.kid_name, row.kid_age, row.birthday, row.event_starts_at.astimezone(VENUE_TZ).date()
            ),
            "name": f"{row.first_name} {row.last_name}",
            "phone": row.phone,
            "email": row.email,
            "notes": row.notes,
            "booked_at": row.created_at.isoformat(),
            "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
            "cancel_reason": row.cancel_reason,
            "cancel_source": row.cancel_source,
            "reschedule_count": row.reschedule_count,
            "rescheduled_at": row.rescheduled_at.isoformat() if row.rescheduled_at else None,
            "refund_status": row.refund_status,
            "full_refund": row.full_refund,
        }
        for row in admin_service.list_bookings(db)
    ]


@router.get("/holds", dependencies=[Depends(require_admin)])
def holds(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Dates being held right now, newest first, so the owner can watch one happen during the demo."""
    return [
        {
            "hold_id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "seconds_remaining": row.seconds_remaining,
            "package": PACKAGES[row.package].label if row.package else None,
            "kids": row.kids,
            "total_cents": row.total_cents,
            "name": f"{row.first_name} {row.last_name}".strip() if row.first_name else None,
            "phone": row.phone,
            "email": row.email,
            "client_ip": str(row.client_ip) if row.client_ip else None,
            "reached_checkout": row.reached_checkout,
            **_when(row.slot_starts_at),
        }
        for row in admin_service.list_active_holds(db)
    ]


@router.get("/leads", dependencies=[Depends(require_admin)])
def leads(db: Session = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    rows = admin_service.list_leads(db)
    return {
        "abandoned": [
            {
                "hold_id": str(row.id),
                "email": row.email,
                "name": f"{row.first_name} {row.last_name}".strip() if row.first_name else None,
                "phone": row.phone,
                "package": PACKAGES[row.package].label if row.package else None,
                "kids": row.kids,
                "created_at": row.created_at.isoformat(),
                **_when(row.slot_starts_at),
            }
            for row in rows["abandoned"]
        ],
    }


@router.get("/waitlist", dependencies=[Depends(require_admin)])
def waitlist(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Who is waiting, on which date, and how many others want the same one."""
    return [
        {
            "id": row.id,
            "email": row.email,
            "phone": row.phone,
            "created_at": row.created_at.isoformat(),
            "notified_at": row.notified_at.isoformat() if row.notified_at else None,
            "date": row.local_date.isoformat(),
            "waiting_on_this_date": row.waiting_on_this_date,
            **_when(row.starts_at),
        }
        for row in admin_service.list_waitlist(db)
    ]


@router.post("/bookings/{booking_id}/cancel", dependencies=[Depends(require_admin)])
def cancel(booking_id: int, body: CancelIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        result = admin_service.cancel_booking(db, booking_id, (body.reason or "").strip() or None)
    except admin_service.BookingNotFound as error:
        raise HTTPException(status_code=404, detail="Booking not found") from error
    except admin_service.AlreadyCancelled as error:
        raise HTTPException(status_code=409, detail="Booking is already cancelled") from error
    return {"id": result.booking_id, "cancelled_at": result.cancelled_at.isoformat(), "full_refund": result.full_refund}


class ManualBookingIn(BaseModel):
    """No price field on purpose: the server quotes the party from the package and the headcount."""

    slot_id: int
    package: str
    kids: int
    addons: dict[str, int] = Field(default_factory=dict)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    phone: str
    email: EmailStr
    kid_name: str | None = Field(default=None, max_length=80)
    kid_age: int | None = Field(default=None, ge=1, le=18)
    birthday: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    deposit_collected: bool


class BalancePaidIn(BaseModel):
    paid: bool


@router.post("/bookings", status_code=201, dependencies=[Depends(require_admin)])
def create_booking(body: ManualBookingIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """A party taken over the phone. No card, no Square, no parent notification.

    The date is claimed by the same unique indexes a parent's booking goes through, so the gap between
    this form loading and the owner submitting it is a 409, never a second party on one Saturday.
    """
    settings = get_settings()
    try:
        booking = admin_service.create_manual_booking(
            db,
            admin_service.ManualBookingInput(
                slot_id=body.slot_id,
                package=body.package,
                kids=body.kids,
                addons=body.addons,
                first_name=body.first_name,
                last_name=body.last_name,
                phone=body.phone,
                email=str(body.email),
                kid_name=body.kid_name,
                kid_age=body.kid_age,
                birthday=check_birthday(body.birthday, venue_today(datetime.now(UTC))),
                notes=body.notes,
                deposit_collected=body.deposit_collected,
            ),
        )
    except ValueError as error:
        # PricingError, normalize_phone and check_birthday are all ValueErrors carrying the sentence the
        # owner should read.
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SlotNotFound as error:
        raise HTTPException(status_code=404, detail="That date does not exist") from error
    except SlotUnavailable as error:
        raise HTTPException(
            status_code=409,
            detail="That date was taken while you were filling this in. Nothing was saved; pick another date.",
        ) from error
    # The owner hears about it like any other booking; the parent does not, because they paid nothing here
    # and this system has promised them nothing.
    notify_owner(db, settings, booking, owner_manual_booked_text(booking), event="booked")
    return {
        "id": booking.id,
        "confirmation_code": booking.confirmation_code,
        "total_cents": booking.total_cents,
        "paid_amount_cents": booking.paid_amount_cents,
        "balance_cents": booking.total_cents - booking.paid_amount_cents,
    }


@router.post("/bookings/{booking_id}/balance-paid", dependencies=[Depends(require_admin)])
def balance_paid(booking_id: int, body: BalancePaidIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stamps the balance the owner took in person, or clears it after a mis-tap. No money moves."""
    try:
        stamped = admin_service.set_balance_paid(db, booking_id, body.paid)
    except admin_service.BookingNotFound as error:
        raise HTTPException(status_code=404, detail="Booking not found") from error
    except admin_service.AlreadyCancelled as error:
        raise HTTPException(status_code=409, detail="This booking is cancelled") from error
    return {"id": booking_id, "balance_paid_at": stamped.isoformat() if stamped else None}


@router.get("/refunds", dependencies=[Depends(require_admin)])
def refunds(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "status": row.status,
            "amount_cents": row.amount_cents,
            "queued_reason": row.queued_reason,
            "error": row.error,
            "created_at": row.created_at.isoformat(),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "settled_at": row.settled_at.isoformat() if row.settled_at else None,
            "square_refund_id": row.square_refund_id,
            "booking_id": row.booking_id,
            "confirmation_code": row.confirmation_code,
            "name": f"{row.first_name} {row.last_name}",
            "phone": row.phone,
            "email": row.email,
            "package": PACKAGES[row.package].label,
            "cancellations": row.cancellations,
            **_when(row.event_starts_at),
        }
        for row in admin_service.list_refunds(db)
    ]


@router.post("/refunds/{refund_id}/approve", dependencies=[Depends(require_admin)])
def approve_refund(refund_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Releases a queued refund down the same path an automatic one takes, idempotency key and all."""
    if not admin_service.approve_refund(db, refund_id):
        raise HTTPException(status_code=404, detail="No refund waiting for approval with that id")
    refund = send_refund(db, get_settings(), refund_id)
    assert refund is not None
    return {"id": refund.id, "status": refund.status, "error": refund.error}


@router.post("/refunds/{refund_id}/reject", dependencies=[Depends(require_admin)])
def reject_refund(refund_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    """The owner decides this one is not refunded. Nothing is sent to Square."""
    if not admin_service.reject_refund(db, refund_id):
        raise HTTPException(status_code=404, detail="No refund waiting for approval with that id")
    return {"ok": True}


@router.get("/repeat-cancellers", dependencies=[Depends(require_admin)])
def repeat_cancellers(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Parents whose refunds queue instead of sending themselves, and the count behind the flag."""
    return [
        {
            "identity": "card" if row.by_card else "email",
            "name": row.name,
            "email": row.email,
            "phone": row.phone,
            "cancellations": row.cancellations,
            "last_cancelled_at": row.last_cancelled_at.isoformat(),
        }
        for row in admin_service.list_repeat_cancellers(db, REPEAT_CANCELLER_THRESHOLD)
    ]


@router.post("/demo/reset", dependencies=[Depends(require_admin)])
def demo_reset(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Back to a known demo state. Admin-only like everything here, and refused unless DEMO_RESET_ENABLED
    is on and the credentials are not production, because this deletes bookings."""
    try:
        state = demo.reset(db, get_settings(), venue_today(datetime.now(UTC)))
    except demo.ResetNotAllowed as error:
        raise HTTPException(status_code=403, detail=error.message) from error
    return {
        "bookings": state.bookings,
        "booked": [day.isoformat() for day in state.booked],
        "held": [day.isoformat() for day in state.held],
        "open": len(state.open),
    }


@router.get("/flags", dependencies=[Depends(require_admin)])
def flags(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "amount_cents": row.amount_cents,
            "square_payment_id": row.square_payment_id,
            "square_order_id": row.square_order_id,
            "created_at": row.created_at.isoformat(),
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "name": f"{row.first_name} {row.last_name}" if row.first_name else None,
            "phone": row.phone,
            "email": row.email,
            "package": PACKAGES[row.package].label if row.package else None,
            "kids": row.kids,
            **_when(row.slot_starts_at),
        }
        for row in admin_service.list_flags(db)
    ]


@router.post("/flags/{flag_id}/resolve", dependencies=[Depends(require_admin)])
def resolve(flag_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    if not admin_service.resolve_flag(db, flag_id):
        raise HTTPException(status_code=404, detail="Flag not found")
    return {"ok": True}


@router.get("/notifications", dependencies=[Depends(require_admin)])
def notifications(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "audience": row.audience,
            "channel": row.channel,
            "recipient": row.recipient,
            "status": row.status,
            "error": row.error,
            "body": row.body,
        }
        for row in admin_service.list_notifications(db)
    ]


@router.get("/requests", dependencies=[Depends(require_admin)])
def party_requests(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": row.id,
            "date": row.local_date.isoformat(),
            "day_label": format_date(row.local_date),
            "time_label": format_clock(row.preferred_time),
            "status": row.status,
            "package": PACKAGES[row.package].label,
            "kids": row.kids,
            "addons": addons_summary(row.addons),
            "total_cents": row.total_cents,
            "deposit_cents": row.deposit_cents,
            "name": f"{row.first_name} {row.last_name}",
            "phone": row.phone,
            "email": row.email,
            "notes": row.notes,
            "birthday": row.birthday.isoformat() if row.birthday else None,
            "birthday_line": birthday_line(row.kid_name, row.kid_age, row.birthday, row.local_date),
            "created_at": row.created_at.isoformat(),
        }
        for row in admin_service.list_requests(db)
    ]
