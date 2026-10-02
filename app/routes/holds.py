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
    SlotInfo,
)
from app.models import Slot
from app.services.holds import (
    CheckoutInput,
    draft_hold,
    HoldLimitReached,
    HoldNotPending,
    SlotNotFound,
    SlotTooSoon,
    SlotUnavailable,
    attach_payment_link,
    checkout_hold,
    create_hold,
    hold_state,
    normalize_ip,
    save_email,
)
from app.services.slots import open_alternatives
from app.services.square import SquareError, create_deposit_link
from app.timeutil import format_date, format_day, format_time, venue_today

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
    try:
        hold = create_hold(
            db, body.slot_id, client_ip(request), body.replace_hold_id, venue_today(datetime.now(UTC)), settings.min_lead_days
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
    except HoldLimitReached as error:
        return JSONResponse(
            status_code=429,
            content={"detail": str(error), "expires_at": error.expires_at.isoformat()},
        )
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
    if not hold.square_order_id:
        try:
            link = create_deposit_link(settings, hold, f"{format_day(slot.starts_at)} {format_time(slot.starts_at)}")
        except SquareError as error:
            # The hold is kept: the parent's date is not released over a Square blip, and the button retries.
            raise HTTPException(
                status_code=502, detail="We couldn't start checkout. Your date is still held; please try again."
            ) from error
        attach_payment_link(db, hold, link.id, link.url, link.order_id)
    assert hold.checkout_url is not None
    return CheckoutOut(
        checkout_url=hold.checkout_url,
        seconds_remaining=max(0, int((hold.expires_at - datetime.now(UTC)).total_seconds())),
        party=party_details(hold, slot.starts_at, None, None),
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
