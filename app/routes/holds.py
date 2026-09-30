import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.pricing import PACKAGES, PricingError
from app.routes.availability import slot_out
from app.schemas import HoldCreatedOut, HoldRequest, HoldStatusOut, LineItemOut, PartyDetails
from app.models import Slot
from app.services.holds import (
    HoldInput,
    HoldLimitReached,
    SlotNotFound,
    SlotTooSoon,
    SlotUnavailable,
    attach_payment_link,
    cancel_pending_hold,
    create_hold,
    hold_state,
    normalize_ip,
)
from app.services.slots import open_alternatives
from app.services.square import SquareError, create_deposit_link
from app.timeutil import format_day, format_time, venue_today

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
    views = open_alternatives(db, today, settings.min_lead_days, settings.weekends_ahead)
    return [slot_out(view).model_dump(mode="json") for view in views]


@router.post("/api/holds", status_code=201, response_model=HoldCreatedOut)
def post_hold(body: HoldRequest, request: Request, db: Session = Depends(get_db)) -> Any:
    settings = get_settings()
    data = HoldInput(
        slot_id=body.slot_id,
        package=body.package,
        kids=body.kids,
        addons=body.addons,
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        email=str(body.email),
        notes=body.notes,
        client_ip=client_ip(request),
    )
    try:
        hold = create_hold(db, data, venue_today(datetime.now(UTC)), settings.min_lead_days)
    except (PricingError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
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
    try:
        link = create_deposit_link(settings, hold, f"{format_day(slot.starts_at)} {format_time(slot.starts_at)}")
    except SquareError as error:
        cancel_pending_hold(db, hold.id)
        raise HTTPException(
            status_code=502, detail="We couldn't start checkout, and your date was released. Please try again."
        ) from error
    attach_payment_link(db, hold, link.id, link.url, link.order_id)
    return HoldCreatedOut(
        hold_id=str(hold.id),
        checkout_url=hold.checkout_url,
        expires_at=hold.expires_at,
        seconds_remaining=max(0, int((hold.expires_at - datetime.now(UTC)).total_seconds())),
    )


def party_details(
    record: Any, starts_at: datetime, confirmation_code: str | None, first_name: str | None
) -> PartyDetails:
    """record is a Hold or a Booking; both carry the same snapshot fields."""
    package = PACKAGES[record.package]
    return PartyDetails(
        confirmation_code=confirmation_code,
        first_name=first_name,
        package=record.package,
        package_label=package.label,
        day_label=format_day(starts_at),
        time_label=format_time(starts_at),
        hours=package.hours + (1 if "extra_hour" in record.addons else 0),
        kids=record.kids,
        line_items=[LineItemOut(**item) for item in record.line_items],
        total_cents=record.total_cents,
        deposit_cents=record.deposit_cents,
        balance_cents=record.total_cents - record.deposit_cents,
    )


@router.get("/api/holds/{hold_id}")
def get_hold(hold_id: str, db: Session = Depends(get_db)) -> HoldStatusOut:
    try:
        parsed = uuid.UUID(hold_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Hold not found") from error
    state = hold_state(db, str(parsed))
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
        party=party_details(
            hold,
            state.starts_at,
            state.confirmation_code if booked else None,
            hold.first_name if booked else None,
        ),
    )
