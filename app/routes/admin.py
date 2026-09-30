from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.services.admin_auth import COOKIE_NAME, SESSION_SECONDS, issue_token, password_matches, token_valid
from app.notifications import addons_summary
from app.pricing import PACKAGES
from app.services import admin as admin_service
from app.services.slots import SlotConflict, set_blocked
from app.timeutil import format_day, format_time

router = APIRouter(prefix="/api/admin")


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
            "balance_cents": row.total_cents - row.deposit_cents,
            "deposit_status": "paid (dev fake)" if row.square_payment_id.startswith("dev-pay-") else "paid",
            "name": f"{row.first_name} {row.last_name}",
            "phone": row.phone,
            "email": row.email,
            "notes": row.notes,
            "booked_at": row.created_at.isoformat(),
            "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
            "cancel_reason": row.cancel_reason,
            "full_refund": row.full_refund,
        }
        for row in admin_service.list_bookings(db)
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
