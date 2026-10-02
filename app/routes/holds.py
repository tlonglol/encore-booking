import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.pricing import PACKAGES, PricingError
from app.routes.availability import day_out
from app.schemas import (
    CheckoutIn,
    CheckoutOut,
    DraftIn,
    HoldCreatedOut,
    HoldEmailIn,
    HoldRequest,
    HoldStatusOut,
    LineItemOut,
    PartyDetails,
    PayIn,
    PayOut,
    SlotInfo,
)
from app.models import Slot
from app.notifications import notify_after_commit, notify_hold_spike
from app.services.booking import PaymentEvent, book_payment
from app.services.holds import (
    CheckoutInput,
    DateCoolingDown,
    SPIKE_HOLDS_PER_HOUR,
    draft_hold,
    HoldLimitReached,
    HoldNotPending,
    SlotNotFound,
    SlotTooSoon,
    SlotUnavailable,
    booking_for_hold,
    checkout_hold,
    payable_hold,
    create_hold,
    hold_state,
    normalize_ip,
    recent_unpaid_holds,
    release_hold,
    save_email,
)
from app.services.turnstile import verify as turnstile_verify
from app.services.slots import open_alternatives
from app.services.square import CardDeclined, SquareError, create_payment
from app.timeutil import format_date, format_day, format_time, venue_today

log = logging.getLogger(__name__)

router = APIRouter()


def client_ip(request: Request) -> str | None:
    """The connection's source IP. X-Forwarded-For is spoofable unless a trusted proxy (ngrok in dev) adds it."""
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return normalize_ip(forwarded.split(",")[-1])
    return normalize_ip(request.client.host if request.client else None)


def _alternatives(db: Session) -> list[dict[str, Any]]:
    settings = get_settings()
    today = venue_today(datetime.now(UTC))
    views = open_alternatives(db, today, settings.min_lead_days, settings.weekends_ahead, settings.weekend_default)
    return [day_out(view).model_dump(mode="json") for view in views]


def _parse_hold_id(hold_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(hold_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Hold not found") from error


@router.post("/api/holds", status_code=201, response_model=HoldCreatedOut)
def post_hold(body: HoldRequest, request: Request, db: Session = Depends(get_db)) -> Any:
    settings = get_settings()
    if settings.weekend_default == "request":
        raise HTTPException(
            status_code=422,
            detail="Dates are request-only right now. Send a request and we'll text you back.",
        )
    ip = client_ip(request)
    if not turnstile_verify(settings, body.turnstile_token, ip):
        raise HTTPException(status_code=403, detail="We couldn't verify that request. Refresh and try again.")
    session_id = getattr(request.state, "session_id", None)
    try:
        hold = create_hold(
            db,
            body.slot_id,
            ip,
            body.replace_hold_id,
            venue_today(datetime.now(UTC)),
            settings.min_lead_days,
            session_id=session_id,
            rate_per_hour=settings.hold_rate_per_hour,
            cooldown_minutes=settings.hold_cooldown_minutes,
        )
    except SlotNotFound as error:
        raise HTTPException(status_code=404, detail="That date is not available") from error
    except SlotTooSoon as error:
        raise HTTPException(
            status_code=422, detail="That date is too soon to book online. Call (646) 203-8087."
        ) from error
    except SlotUnavailable:
        return JSONResponse(
            status_code=409,
            content={"detail": "That date was just taken.", "alternatives": _alternatives(db)},
        )
    except DateCoolingDown as error:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "You've let this date go twice already. Give it a few minutes, or call"
                " (646) 203-8087 and we'll hold it for you.",
                "expires_at": error.until.isoformat(),
            },
        )
    except HoldLimitReached as error:
        return JSONResponse(
            status_code=429,
            content={"detail": str(error), "expires_at": error.expires_at.isoformat()},
        )
    # A notice, never a block: the caps above did the blocking, and the hold just made is a real one.
    unpaid = recent_unpaid_holds(db, session_id, ip)
    if unpaid > SPIKE_HOLDS_PER_HOUR:
        notify_hold_spike(db, settings, hold.id, unpaid, ip)
    slot = db.get(Slot, hold.slot_id)
    assert slot is not None
    return HoldCreatedOut(
        hold_id=str(hold.id),
        expires_at=hold.expires_at,
        seconds_remaining=max(0, int((hold.expires_at - datetime.now(UTC)).total_seconds())),
        date=slot.local_date.isoformat(),
        day_label=format_day(slot.starts_at),
        time_label=format_time(slot.starts_at),
    )


@router.post("/api/holds/{hold_id}/release")
def post_release(hold_id: str, request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    """The parent tapping their own held date to let it go. Same cancellation the date swap runs.

    Always 200: "that hold is not yours", "it already expired" and "you already released it" are all the same
    thing to this caller, which only wants the date to stop being held by it, and answering them apart would
    tell a stranger whether a hold id is live.
    """
    return {"released": release_hold(db, _parse_hold_id(hold_id), getattr(request.state, "session_id", None))}


@router.patch("/api/holds/{hold_id}/email")
def patch_hold_email(hold_id: str, body: HoldEmailIn, db: Session = Depends(get_db)) -> dict[str, bool]:
    """Abandoner recovery: saved on blur, long before the pay button. A dead hold is left alone."""
    saved = save_email(db, _parse_hold_id(hold_id), str(body.email))
    return {"saved": saved}


@router.patch("/api/holds/{hold_id}/draft", response_model=PartyDetails)
def patch_hold_draft(hold_id: str, body: DraftIn, db: Session = Depends(get_db)) -> Any:
    """Mirrors the funnel's draft onto the hold, priced server-side. Nothing here finalizes anything."""
    try:
        hold = draft_hold(
            db, _parse_hold_id(hold_id), body.package, body.kids, body.addons, body.birthday, venue_today(datetime.now(UTC))
        )
    except (PricingError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SlotNotFound as error:
        raise HTTPException(status_code=404, detail="Hold not found") from error
    except HoldNotPending as error:
        return JSONResponse(
            status_code=409,
            content={"detail": "This hold is no longer active, so the date was released.", "status": error.status},
        )
    slot = db.get(Slot, hold.slot_id)
    assert slot is not None
    return party_details(hold, slot.starts_at, None, None)


@router.post("/api/holds/{hold_id}/checkout", response_model=CheckoutOut)
def post_checkout(hold_id: str, body: CheckoutIn, db: Session = Depends(get_db)) -> Any:
    settings = get_settings()
    parsed = _parse_hold_id(hold_id)
    data = CheckoutInput(
        package=body.package,
        kids=body.kids,
        addons=body.addons,
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        email=str(body.email),
        kid_name=body.kid_name,
        kid_age=body.kid_age,
        birthday=body.birthday,
        notes=body.notes,
    )
    try:
        hold = checkout_hold(db, parsed, data, venue_today(datetime.now(UTC)))
    except (PricingError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SlotNotFound as error:
        raise HTTPException(status_code=404, detail="Hold not found") from error
    except HoldNotPending as error:
        return JSONResponse(
            status_code=409,
            content={"detail": "This hold is no longer active, so the date was released.", "status": error.status},
        )
    except HoldLimitReached as error:
        return JSONResponse(
            status_code=429,
            content={"detail": str(error), "expires_at": error.expires_at.isoformat()},
        )
    slot = db.get(Slot, hold.slot_id)
    assert slot is not None
    # Nothing is created at Square here any more. The card is collected on our own page and charged by
    # POST /pay, so this endpoint only snapshots the party at a server-computed price.
    return CheckoutOut(
        seconds_remaining=max(0, int((hold.expires_at - datetime.now(UTC)).total_seconds())),
        party=party_details(hold, slot.starts_at, None, None),
    )


@router.post("/api/holds/{hold_id}/pay", response_model=PayOut)
def post_pay(hold_id: str, body: PayIn, db: Session = Depends(get_db)) -> Any:
    """Charge the deposit against a single-use card token and commit the booking.

    The money moves before the booking exists, which is the one ordering that cannot lose a parent's
    payment: if this process dies between the two, Square's webhook finds the hold by reference_id and
    commits the same booking. The token is never stored, never logged, and never leaves this function.
    """
    settings = get_settings()
    parsed = _parse_hold_id(hold_id)

    # Already paid for. Two ways to arrive here and both are the same answer: the parent pressed the
    # button twice, or the webhook backstop committed the booking because this process died after
    # Square took the money. Telling either of them the date was released would be a lie, and charging
    # a second card would be worse, so show them the booking they already have.
    settled = booking_for_hold(db, parsed)
    if settled is not None:
        slot = db.get(Slot, settled.slot_id)
        assert slot is not None
        return PayOut(
            confirmation_code=settled.confirmation_code,
            manage_token=settled.manage_token,
            party=party_details(
                settled, slot.starts_at, settled.confirmation_code, settled.first_name, settled.manage_token
            ),
        )

    try:
        hold = payable_hold(db, parsed)
    except SlotNotFound as error:
        raise HTTPException(status_code=404, detail="Hold not found") from error
    except HoldNotPending as error:
        return JSONResponse(
            status_code=409,
            content={"detail": "This hold is no longer active, so the date was released.", "status": error.status},
        )
    slot = db.get(Slot, hold.slot_id)
    assert slot is not None

    try:
        payment = create_payment(settings, hold, body.token)
    except CardDeclined as error:
        # The hold and the date survive a declined card: another card is allowed to work.
        return JSONResponse(status_code=402, content={"detail": error.message, "code": error.code})
    except SquareError as error:
        raise HTTPException(
            status_code=502, detail="We couldn't reach the card processor. Your date is still held; please try again."
        ) from error

    if payment.status != "COMPLETED":
        # APPROVED-but-not-captured and friends: no booking, no money kept, hold intact.
        log.warning("Square payment %s came back %s, not COMPLETED", payment.id, payment.status)
        raise HTTPException(
            status_code=502, detail="That payment didn't complete. Your date is still held; please try again."
        )

    result = book_payment(
        db,
        PaymentEvent(
            event_id=f"sync:{payment.id}",
            event_type="payment.sync",
            payment_id=payment.id,
            order_id=payment.order_id,
            status=payment.status,
            amount_cents=payment.amount_cents,
            currency="USD",
            card_fingerprint=payment.card_fingerprint,
            reference_id=payment.reference_id or str(hold.id),
        ),
    )
    notify_after_commit(db, settings, result)

    booking = booking_for_hold(db, hold.id)
    if booking is None:
        # Square took the money and we could not record it: flagged for the owner by _apply_payment, and
        # never silently swallowed. The parent is told the truth and the hold is not resold.
        log.error("Payment %s for hold %s produced no booking (outcome=%s)", payment.id, hold.id, result.outcome)
        raise HTTPException(
            status_code=502,
            detail="Your payment went through but we couldn't finish locking the date. We'll call you within the hour.",
        )
    return PayOut(
        confirmation_code=booking.confirmation_code,
        manage_token=booking.manage_token,
        party=party_details(booking, slot.starts_at, booking.confirmation_code, booking.first_name,
                            booking.manage_token),
    )


def party_details(
    record: Any,
    starts_at: datetime,
    confirmation_code: str | None,
    first_name: str | None,
    manage_token: str | None = None,
) -> PartyDetails:
    """record is a Hold or a Booking; both carry the same snapshot fields. The manage token lives on the
    booking, so a hold view that has one passes it in rather than reading it off the record."""
    package = PACKAGES[record.package]
    booked = confirmation_code is not None
    return PartyDetails(
        confirmation_code=confirmation_code,
        manage_token=manage_token if booked else None,
        first_name=first_name,
        kid_name=record.kid_name if booked else None,
        kid_age=record.kid_age if booked else None,
        birthday=record.birthday if booked else None,
        notes=record.notes if booked else None,
        package=record.package,
        package_label=package.label,
        day_label=format_day(starts_at),
        time_label=format_time(starts_at),
        starts_at=starts_at,
        hours=package.hours + (1 if "extra_hour" in record.addons else 0),
        kids=record.kids,
        line_items=[LineItemOut(**item) for item in record.line_items],
        total_cents=record.total_cents,
        deposit_cents=record.deposit_cents,
        balance_cents=record.total_cents - record.deposit_cents,
    )


@router.get("/api/holds/{hold_id}")
def get_hold(hold_id: str, db: Session = Depends(get_db)) -> HoldStatusOut:
    state = hold_state(db, str(_parse_hold_id(hold_id)))
    if state is None:
        raise HTTPException(status_code=404, detail="Hold not found")
    hold = state.hold
    booked = state.status == "booked"
    return HoldStatusOut(
        hold_id=str(hold.id),
        status=state.status,
        expires_at=hold.expires_at,
        seconds_remaining=state.seconds_remaining,
        checkout_url=hold.checkout_url if state.status == "pending_payment" else None,
        slot=SlotInfo(
            date=state.local_date.isoformat(),
            day_label=format_date(state.local_date),
            time_label=format_time(state.starts_at),
        ),
        party=None
        if hold.package is None
        else party_details(
            hold,
            state.starts_at,
            state.confirmation_code if booked else None,
            hold.first_name if booked else None,
            state.manage_token if booked else None,
        ),
    )
