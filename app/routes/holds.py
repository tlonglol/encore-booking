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
from app.services.holds import (
    HoldInput,
    HoldLimitReached,
    SlotNotFound,
    SlotTooSoon,
    SlotUnavailable,
    create_hold,
    hold_state,
    normalize_ip,
)
from app.services.slots import open_alternatives
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
    return HoldCreatedOut(
        hold_id=str(hold.id),
        checkout_url=hold.checkout_url,
        expires_at=hold.expires_at,
        seconds_remaining=max(0, int((hold.expires_at - datetime.now(UTC)).total_seconds())),
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
    package = PACKAGES[hold.package]
    return HoldStatusOut(
        hold_id=str(hold.id),
        status=state.status,
        expires_at=hold.expires_at,
        seconds_remaining=state.seconds_remaining,
        checkout_url=hold.checkout_url if state.status == "pending_payment" else None,
        party=PartyDetails(
            confirmation_code=state.confirmation_code if booked else None,
            first_name=hold.first_name if booked else None,
            package=hold.package,
            package_label=package.label,
            day_label=format_day(state.starts_at),
            time_label=format_time(state.starts_at),
            hours=package.hours + (1 if "extra_hour" in hold.addons else 0),
            kids=hold.kids,
            line_items=[LineItemOut(**item) for item in hold.line_items],
            total_cents=hold.total_cents,
            deposit_cents=hold.deposit_cents,
            balance_cents=hold.total_cents - hold.deposit_cents,
        ),
    )
